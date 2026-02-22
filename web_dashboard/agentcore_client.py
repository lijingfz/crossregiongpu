"""AgentCore Runtime HTTP client for the Web Dashboard.

Invokes the remote AgentCore Runtime agent via boto3 instead of
creating local Agent instances. This ensures the Web Dashboard
connects to the deployed AgentCore Runtime.

Environment variables:
  AGENTCORE_AGENT_ARN  — AgentCore Runtime agent ARN (required)
  AGENTCORE_REGION     — AWS region for the AgentCore client (default: us-west-2)
"""

from __future__ import annotations

import codecs
import json
import logging
import os
from typing import Any, Generator

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# boto3 default read_timeout is 60s.  Launch operations routinely take
# 60-120s (cross-region probe-and-fill + DynamoDB persist + finalize).
# If the client times out mid-flight, the broken connection causes
# AgentCore Runtime to re-invoke the agent, producing duplicate
# launches.  A generous read timeout prevents this.
_BOTO_CONFIG = Config(
    read_timeout=500,       # ~8 min — enough for worst-case multi-region launch
    retries={"max_attempts": 0},  # disable boto3 auto-retry to avoid duplicate invocations
)


def _get_client():
    """Create a boto3 bedrock-agentcore client."""
    region = os.environ.get("AGENTCORE_REGION", "us-west-2")
    return boto3.client("bedrock-agentcore", region_name=region, config=_BOTO_CONFIG)


def _get_agent_arn() -> str:
    """Return the configured AgentCore Runtime agent ARN."""
    arn = os.environ.get("AGENTCORE_AGENT_ARN", "")
    if not arn:
        raise RuntimeError(
            "AGENTCORE_AGENT_ARN not configured. "
            "Set it to your AgentCore Runtime agent ARN."
        )
    return arn


def invoke_agent(
    *,
    session_id: str,
    prompt: str,
    token: str = "",
) -> dict[str, Any]:
    """Send a prompt to the AgentCore Runtime agent and return the response.

    Parameters
    ----------
    session_id:
        Runtime session ID for conversation continuity.
    prompt:
        User message to send to the agent.
    token:
        JWT token for agent-side authentication.

    Returns
    -------
    dict
        Parsed JSON response from the agent (AgentResponse format).
    """
    client = _get_client()
    agent_arn = _get_agent_arn()

    payload = json.dumps({
        "prompt": prompt,
        "token": token,
    }).encode()

    logger.info(
        "Invoking AgentCore Runtime: arn=%s, session=%s",
        agent_arn, session_id,
    )

    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        payload=payload,
    )

    return _parse_response(response)


def invoke_agent_stream(
    *,
    session_id: str,
    prompt: str,
    token: str = "",
) -> Generator[dict[str, Any], None, None]:
    """Stream events from the AgentCore Runtime agent.

    The entrypoint uses ``yield`` (async generator) so AgentCore
    Runtime returns ``text/event-stream``.  Each SSE ``data:`` line is
    a JSON string — either a tool-progress event or the final
    AgentResponse.

    Yields SSE-compatible event dicts:
      - ``{"type": "tool_start", "tool": "<name>"}``
      - ``{"type": "tool_end", "tool": "<name>"}``
      - ``{"type": "result", "data": <AgentResponse dict>}``
      - ``{"type": "error", "message": "..."}``
    """
    client = _get_client()
    agent_arn = _get_agent_arn()

    payload = json.dumps({
        "prompt": prompt,
        "token": token,
    }).encode()

    logger.info(
        "Invoking AgentCore Runtime (stream): arn=%s, session=%s",
        agent_arn, session_id,
    )

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=session_id,
            payload=payload,
        )
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
        return

    content_type = response.get("contentType", "")
    logger.info("AgentCore response contentType: %s", content_type)

    if "text/event-stream" not in content_type:
        # Non-streaming response — parse and yield as single result
        parsed = _parse_response(response)
        yield {"type": "result", "data": parsed}
        return

    # --- Stream processing ---
    # The entrypoint yields dicts.  AgentCore Runtime serializes each
    # dict as a JSON string in an SSE "data: <json>" line.
    # We use chunk_size=10 (per official AWS docs) to ensure lines
    # are split correctly.
    decoder = codecs.getincrementaldecoder("utf-8")("ignore")
    final_response: dict[str, Any] | None = None
    raw_chunks: list[str] = []
    line_count = 0

    for line in response["response"].iter_lines(chunk_size=10):
        if not line:
            continue
        decoded = decoder.decode(line if isinstance(line, bytes) else line.encode())
        line_count += 1

        # Log first few lines for debugging
        if line_count <= 10:
            logger.info("SSE line %d: %s", line_count, decoded[:300])

        raw_chunks.append(decoded)

        # Strip SSE prefix if present
        stripped = decoded.strip()
        if stripped.startswith("data: "):
            stripped = stripped[6:]
        if not stripped:
            continue

        # Try to parse as JSON
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            # Not valid JSON on its own — try accumulated chunks
            continue

        if not isinstance(obj, dict):
            continue

        # Route based on event type
        evt_type = obj.get("type", "")

        if evt_type in ("tool_start", "tool_end"):
            yield obj
            continue

        # If it has "status" key, it's the final AgentResponse
        if "status" in obj:
            final_response = obj
            continue

    # Flush decoder
    tail = decoder.decode(b"", final=True)
    if tail:
        raw_chunks.append(tail)

    logger.info(
        "SSE stream ended: %d lines, final_response=%s",
        line_count, final_response is not None,
    )

    # If no final_response found from line-by-line parsing,
    # try to parse the entire accumulated stream as one JSON blob
    if final_response is None and raw_chunks:
        full_text = "".join(raw_chunks).strip()
        # Remove any SSE "data: " prefixes
        if full_text.startswith("data: "):
            full_text = full_text[6:]

        logger.info("Attempting full-text parse, length=%d, preview=%.200s", len(full_text), full_text)

        try:
            obj = json.loads(full_text)
            if isinstance(obj, dict) and "status" in obj:
                final_response = obj
        except (json.JSONDecodeError, ValueError):
            # Try extracting JSON objects from the accumulated text
            # The stream may contain multiple JSON objects concatenated
            for chunk in raw_chunks:
                chunk = chunk.strip()
                if chunk.startswith("data: "):
                    chunk = chunk[6:]
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict):
                        if "status" in obj:
                            final_response = obj
                        elif obj.get("type") in ("tool_start", "tool_end"):
                            yield obj
                except (json.JSONDecodeError, ValueError):
                    pass

    if final_response is not None:
        yield {"type": "result", "data": final_response}
    else:
        logger.error(
            "No AgentResponse found in stream. Lines=%d, raw_preview=%.500s",
            line_count,
            "".join(raw_chunks)[:500] if raw_chunks else "(empty)",
        )
        yield {"type": "error", "message": "Agent stream ended without a response"}


def invoke_approval(
    *,
    session_id: str,
    approval_responses: list[dict],
    token: str = "",
) -> dict[str, Any]:
    """Send approval responses to the AgentCore Runtime agent.

    Parameters
    ----------
    session_id:
        Runtime session ID.
    approval_responses:
        List of approval decisions, each with interrupt_id and decision.
    token:
        JWT token for agent-side authentication.

    Returns
    -------
    dict
        Parsed JSON response from the agent.
    """
    client = _get_client()
    agent_arn = _get_agent_arn()

    payload = json.dumps({
        "approval_responses": approval_responses,
        "token": token,
    }).encode()

    logger.info(
        "Sending approval to AgentCore Runtime: arn=%s, session=%s",
        agent_arn, session_id,
    )

    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        payload=payload,
    )

    return _parse_response(response)


def _safe_decode(data: bytes) -> str:
    """Decode bytes to str, tolerating truncated multi-byte sequences."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Truncated multi-byte char at chunk boundary — decode with
        # errors="ignore" to drop the incomplete trailing bytes.
        return data.decode("utf-8", errors="ignore")


def _parse_response(response: dict) -> dict[str, Any]:
    """Parse the AgentCore Runtime streaming/JSON response into a dict.

    For streaming responses, we collect raw bytes and use an incremental
    UTF-8 decoder so that multi-byte characters (e.g. Chinese) split
    across chunk boundaries are handled correctly.

    IMPORTANT: We always consume the stream fully before returning.
    Closing the stream early causes AgentCore Runtime to re-invoke the
    agent within the same session, leading to duplicate execution.
    We capture the first complete JSON AgentResponse we see, then
    drain the remaining stream data silently.
    """
    import codecs

    content_type = response.get("contentType", "")

    if "text/event-stream" in content_type:
        # Incremental UTF-8 decoder handles multi-byte chars split across chunks
        decoder = codecs.getincrementaldecoder("utf-8")("ignore")
        text_buf: list[str] = []
        captured_result: dict[str, Any] | None = None

        for line in response["response"].iter_lines(chunk_size=4096):
            if not line:
                continue
            # line is bytes — decode incrementally
            decoded = decoder.decode(line if isinstance(line, bytes) else line.encode())
            if decoded.startswith("data: "):
                decoded = decoded[6:]
            text_buf.append(decoded)

            # Try to parse accumulated content as JSON after each chunk.
            # Capture the first complete AgentResponse but keep draining.
            if captured_result is None:
                raw = "".join(text_buf)
                try:
                    result = json.loads(raw)
                    if isinstance(result, dict) and "status" in result:
                        captured_result = result
                        logger.info(
                            "Captured complete AgentResponse, draining remaining stream"
                        )
                except (json.JSONDecodeError, ValueError):
                    continue

        # Flush any remaining bytes in the decoder
        tail = decoder.decode(b"", final=True)
        if tail:
            text_buf.append(tail)

        if captured_result is not None:
            return captured_result

        # Fallback: stream ended without a valid JSON response
        raw = "".join(text_buf)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"status": "completed", "result": raw}

    elif content_type == "application/json":
        # Collect all bytes first, then decode once to avoid boundary issues
        raw_bytes = b""
        for chunk in response.get("response", []):
            if isinstance(chunk, bytes):
                raw_bytes += chunk
            else:
                raw_bytes += chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        raw = _safe_decode(raw_bytes)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"status": "completed", "result": raw}

    else:
        # Fallback for unknown content types
        raw_body = response.get("response", b"")
        if hasattr(raw_body, "read"):
            raw_body = raw_body.read()
        if isinstance(raw_body, bytes):
            raw_body = _safe_decode(raw_body)
        elif not isinstance(raw_body, str):
            raw_body = str(raw_body)
        try:
            return json.loads(raw_body)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {"status": "completed", "result": raw_body}

"""AgentCore Runtime HTTP client for the Web Dashboard.

Invokes the remote AgentCore Runtime agent via boto3 instead of
creating local Agent instances. This ensures the Web Dashboard
connects to the deployed AgentCore Runtime.

Environment variables:
  AGENTCORE_AGENT_ARN  — AgentCore Runtime agent ARN (required)
  AGENTCORE_REGION     — AWS region for the AgentCore client (default: us-west-2)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

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

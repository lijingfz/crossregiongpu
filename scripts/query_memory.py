#!/usr/bin/env python3
"""查询 AgentCore Memory 中保存的对话记录。

用法:
  # 列出账号下所有 Memory 资源
  python scripts/query_memory.py list

  # 查询某个 session 的对话记录（actor_id 和 session_id 都是必填的）
  python scripts/query_memory.py events --user test_user --session <session_id>

  # 指定 memory_id
  python scripts/query_memory.py events --user test_user --session <session_id> --memory-id <id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def cmd_list(args):
    """列出所有 Memory 资源。"""
    from bedrock_agentcore.memory import MemoryClient
    client = MemoryClient(region_name=args.region)

    print(f"列出所有 Memory 资源 (region={args.region})")
    print("=" * 70)

    try:
        memories = client.list_memories(max_results=100)
    except Exception as e:
        print(f"查询失败: {e}")
        sys.exit(1)

    if not memories:
        print("没有找到任何 Memory 资源。")
        return

    for m in memories:
        if isinstance(m, dict):
            print(json.dumps(m, indent=2, ensure_ascii=False, default=str))
        else:
            print(m)
        print()

    print(f"共 {len(memories)} 个 Memory 资源")


def cmd_events(args):
    """查询指定 session 的对话事件。"""
    from bedrock_agentcore.memory import MemoryClient
    client = MemoryClient(region_name=args.region)

    memory_id = args.memory_id
    print(f"查询对话记录: memory={memory_id}, user={args.user}, session={args.session}")
    print("=" * 70)

    try:
        events = client.list_events(
            memory_id=memory_id,
            actor_id=args.user,
            session_id=args.session,
            max_results=args.limit,
        )
    except Exception as e:
        print(f"查询失败: {e}")
        sys.exit(1)

    if not events:
        print("没有找到任何对话记录。")
        return

    for i, event in enumerate(events, 1):
        print(f"\n--- 事件 {i} ---")
        if isinstance(event, dict):
            print(json.dumps(event, indent=2, ensure_ascii=False, default=str))
        else:
            print(event)

    print(f"\n共 {len(events)} 条记录")


def main():
    parser = argparse.ArgumentParser(description="查询 AgentCore Memory 对话记录")
    parser.add_argument("--region", default=os.environ.get("MEMORY_REGION", "us-west-2"))
    parser.add_argument("--memory-id", default=os.environ.get("MEMORY_ID", "gpu_scheduler_memory-1az3i38LW2"))

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出所有 Memory 资源")

    ev = sub.add_parser("events", help="查询对话事件")
    ev.add_argument("--user", required=True, help="actor_id / user_id")
    ev.add_argument("--session", required=True, help="session_id")
    ev.add_argument("--limit", type=int, default=50, help="最大返回数量")

    args = parser.parse_args()

    try:
        from bedrock_agentcore.memory import MemoryClient  # noqa: F401
    except ImportError:
        print("错误: 请先安装 bedrock-agentcore-starter-toolkit")
        sys.exit(1)

    if args.command == "list":
        cmd_list(args)
    elif args.command == "events":
        cmd_events(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

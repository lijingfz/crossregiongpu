#!/usr/bin/env python3
"""为 AgentCore Memory 配置长期语义记忆（LTM）策略。

用法:
  # 添加 semantic 策略（默认）
  python scripts/setup_memory_strategy.py add

  # 添加指定策略类型
  python scripts/setup_memory_strategy.py add --strategy semanticMemoryStrategy

  # 查看当前 Memory 配置
  python scripts/setup_memory_strategy.py show

  # 测试 LTM 搜索
  python scripts/setup_memory_strategy.py search --query "GPU capacity in Tokyo"

环境变量:
  MEMORY_ID      — Memory 资源 ID（默认: gpu_scheduler_memory-1az3i38LW2）
  MEMORY_REGION  — AWS 区域（默认: us-west-2）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


DEFAULT_MEMORY_ID = "gpu_scheduler_memory-1az3i38LW2"
DEFAULT_REGION = "us-west-2"
DEFAULT_NAMESPACE = "gpu_scheduler"

SUPPORTED_STRATEGIES = [
    "semanticMemoryStrategy",
    "userPreferenceMemoryStrategy",
    "summaryMemoryStrategy",
]


def _get_client(region: str):
    from bedrock_agentcore.memory import MemoryClient
    return MemoryClient(region_name=region)


def cmd_add(args):
    """添加 LTM 策略到 Memory 资源。"""
    client = _get_client(args.region)
    memory_id = args.memory_id
    strategy = args.strategy
    namespace = args.namespace

    print(f"添加策略: {strategy}")
    print(f"  Memory ID:  {memory_id}")
    print(f"  Namespace:  {namespace}")
    print(f"  Region:     {args.region}")
    print()

    try:
        if strategy == "semanticMemoryStrategy":
            print("正在添加 semantic 策略（可能需要 30-60 秒）...")
            client.add_semantic_strategy_and_wait(
                memory_id=memory_id,
                name=namespace,
                namespaces=[namespace],
            )
        else:
            # Generic strategy addition for other types
            client.add_strategy(
                memory_id=memory_id,
                strategy_name=strategy,
                name=namespace,
                namespaces=[namespace],
            )
            print("策略已提交，等待生效...")
            time.sleep(10)

        print(f"策略 {strategy} 添加成功。")
        print()
        print("后续写入的对话事件（create_event）将自动触发 LTM 知识提取。")
        print("提取过程是异步的，通常需要 5-10 秒。")

    except Exception as e:
        print(f"添加策略失败: {e}")
        sys.exit(1)


def cmd_show(args):
    """显示 Memory 资源的当前配置。"""
    client = _get_client(args.region)

    print(f"查询 Memory 配置: {args.memory_id}")
    print("=" * 60)

    try:
        info = client.get_memory(memory_id=args.memory_id)
        print(json.dumps(info, indent=2, ensure_ascii=False, default=str))
    except Exception as e:
        print(f"查询失败: {e}")
        sys.exit(1)


def cmd_search(args):
    """测试 LTM 语义搜索。"""
    client = _get_client(args.region)

    print(f"LTM 语义搜索: \"{args.query}\"")
    print(f"  Memory ID:  {args.memory_id}")
    print(f"  Namespace:  {args.namespace}")
    print("=" * 60)

    try:
        results = client.search_long_term_memories(
            memory_id=args.memory_id,
            namespace=args.namespace,
            query=args.query,
            max_results=args.limit,
        )
    except Exception as e:
        print(f"搜索失败: {e}")
        sys.exit(1)

    if not results:
        print("没有找到相关的长期记忆。")
        print("提示: 确认已添加策略且有对话事件写入后等待 5-10 秒。")
        return

    for i, mem in enumerate(results, 1):
        print(f"\n--- 记忆 {i} ---")
        if isinstance(mem, dict):
            print(json.dumps(mem, indent=2, ensure_ascii=False, default=str))
        else:
            print(mem)

    print(f"\n共 {len(results)} 条记忆")


def main():
    parser = argparse.ArgumentParser(description="AgentCore Memory LTM 策略管理")
    parser.add_argument(
        "--region",
        default=os.environ.get("MEMORY_REGION", DEFAULT_REGION),
    )
    parser.add_argument(
        "--memory-id",
        default=os.environ.get("MEMORY_ID", DEFAULT_MEMORY_ID),
    )

    sub = parser.add_subparsers(dest="command")

    # add
    add_p = sub.add_parser("add", help="添加 LTM 策略")
    add_p.add_argument(
        "--strategy",
        default="semanticMemoryStrategy",
        choices=SUPPORTED_STRATEGIES,
        help="策略类型",
    )
    add_p.add_argument("--namespace", default=DEFAULT_NAMESPACE)

    # show
    sub.add_parser("show", help="查看 Memory 配置")

    # search
    search_p = sub.add_parser("search", help="测试 LTM 语义搜索")
    search_p.add_argument("--query", required=True, help="搜索查询")
    search_p.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    search_p.add_argument("--limit", type=int, default=5)

    args = parser.parse_args()

    try:
        from bedrock_agentcore.memory import MemoryClient  # noqa: F401
    except ImportError:
        print("错误: 请先安装 bedrock-agentcore-starter-toolkit")
        sys.exit(1)

    if args.command == "add":
        cmd_add(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "search":
        cmd_search(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

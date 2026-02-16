#!/usr/bin/env python3
"""生成用于 GPU Scheduler Agent 认证的 JWT Token。

用法:
  # 使用默认值生成（有效期 24 小时）
  python scripts/generate_token.py

  # 指定用户信息
  python scripts/generate_token.py --user-id admin01 --username "Li Jing" --roles admin,operator

  # 指定有效期（小时）
  python scripts/generate_token.py --expires 72

  # 指定 secret key（默认从 AUTH_SECRET_KEY 环境变量读取）
  python scripts/generate_token.py --secret my-secret-key

  # 输出纯 token（方便脚本调用）
  python scripts/generate_token.py --raw

环境变量:
  AUTH_SECRET_KEY — HMAC 签名密钥（必须与 agent 运行时一致）
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

import jwt


def generate_token(
    *,
    user_id: str,
    username: str,
    roles: list[str],
    secret_key: str,
    expires_hours: int = 24,
) -> str:
    """生成 HS256 JWT token。"""
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "sub": user_id,
        "username": username,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def main():
    parser = argparse.ArgumentParser(description="生成 GPU Scheduler 认证 JWT Token")
    parser.add_argument("--user-id", default="default_user", help="用户 ID")
    parser.add_argument("--username", default="Default User", help="用户名")
    parser.add_argument("--roles", default="operator", help="角色列表，逗号分隔")
    parser.add_argument("--expires", type=int, default=24, help="有效期（小时），默认 24")
    parser.add_argument("--secret", default=None, help="HMAC 密钥（默认读 AUTH_SECRET_KEY 环境变量）")
    parser.add_argument("--raw", action="store_true", help="只输出 token，不输出其他信息")

    args = parser.parse_args()

    secret_key = args.secret or os.environ.get("AUTH_SECRET_KEY", "")
    if not secret_key:
        print("错误: 请通过 --secret 参数或 AUTH_SECRET_KEY 环境变量提供密钥", file=sys.stderr)
        sys.exit(1)

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]

    token = generate_token(
        user_id=args.user_id,
        username=args.username,
        roles=roles,
        secret_key=secret_key,
        expires_hours=args.expires,
    )

    if args.raw:
        print(token)
    else:
        exp_time = datetime.now(timezone.utc) + timedelta(hours=args.expires)
        print(f"User ID:    {args.user_id}")
        print(f"Username:   {args.username}")
        print(f"Roles:      {roles}")
        print(f"Expires:    {exp_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ({args.expires}h)")
        print(f"Algorithm:  HS256")
        print()
        print(f"Token:")
        print(token)


if __name__ == "__main__":
    main()

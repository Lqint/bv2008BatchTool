#!/usr/bin/env python3
"""Undo activity member or service-time records through the bv2008 gateway."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bv_client import TOKEN as DEFAULT_TOKEN
from bv2008_gui.api import delete_activity_members, delete_timing_records


def split_iids(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in value.replace(",", " ").replace("，", " ").split():
            iid = part.strip()
            if iid and iid not in seen:
                result.append(iid)
                seen.add(iid)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="撤销活动成员或服务时长记录")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="accessToken；默认读取 config.py")
    parser.add_argument("--yes", action="store_true", help="跳过删除前确认")
    subparsers = parser.add_subparsers(dest="kind", required=True)

    member = subparsers.add_parser("member", help="从活动中删除成员")
    member.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="跳过删除前确认")
    member.add_argument("iids", nargs="+", help="成员关系 iid，可用空格或逗号分隔")

    timing = subparsers.add_parser("timing", help="删除服务时长记录")
    timing.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="跳过删除前确认")
    timing.add_argument("--activity-id", required=True, help="时长记录所属活动 ID")
    timing.add_argument("iids", nargs="+", help="时长记录 iid，可用空格或逗号分隔")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = str(args.token or "").strip()
    iids = split_iids(args.iids)
    if not token:
        raise SystemExit("缺少 token：请传 --token，或在 config.py 中配置 TOKEN")
    if not iids:
        raise SystemExit("至少需要一个记录 iid")

    action = "删除活动成员" if args.kind == "member" else "删除服务时长记录"
    if not args.yes:
        answer = input(f"即将{action} {len(iids)} 条，此操作不可自动恢复。输入 yes 继续：")
        if answer.strip().lower() != "yes":
            print("已取消")
            return 1

    if args.kind == "member":
        delete_activity_members(token, iids)
    else:
        delete_timing_records(token, args.activity_id, iids)

    print(json.dumps({"success": True, "action": args.kind, "count": len(iids)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

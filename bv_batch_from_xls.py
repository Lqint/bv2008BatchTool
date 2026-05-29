"""Batch-record hours from an XLS roster.

Reads a roster file (.xls/.xlsx) with columns 学生姓名 and 认定时(次)数,
then for each person:
  1. searches uid via findOrgUserList (SM2-encrypted name)
  2. ensures uid is in the post via addList  ← required, see note below
  3. allocates hours from START_DATE, max MAX_HOURS_PER_DAY per day (spill to next)
  4. uploads a proof image per batchAdd call (filePath is single-use)
  5. submits via activityTiming-batchAdd

NOTE: Step 2 (addList before batchAdd) is mandatory. The site has a bug where
activityTiming-batchAdd succeeds even when the uid is not a post member, leaving
hours recorded but the volunteer absent from the post roster. Always run addList first.

Usage:
    python3 bv_batch_from_xls.py <xls_path> [--start YYYY-MM-DD] [--max-hours 8] [--dry-run]

Configure TOKEN / ACTIVITY_ID / POST_ID / ORG_ID in config.py first.
"""
import sys
import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import xlrd

from bv_client import call, unwrap, sm2_encrypt, get_in_sm2_pk
from bv_record_hours import upload_proof

try:
    from config import TOKEN, ACTIVITY_ID, POST_ID, ORG_ID
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and fill in values")

NOTES = ""


def parse_roster(path: Path) -> list[tuple[str, float]]:
    wb = xlrd.open_workbook(str(path))
    sh = wb.sheet_by_index(0)
    header = sh.row_values(0)
    name_col = header.index("学生姓名")
    hour_col = next(i for i, h in enumerate(header) if "时" in str(h) and "数" in str(h))
    out = []
    for r in range(1, sh.nrows):
        row = sh.row_values(r)
        name = str(row[name_col]).strip()
        hours = float(row[hour_col])
        if name and hours > 0:
            out.append((name, hours))
    return out


def allocate(hours: float, start: date, max_per_day: float) -> list[tuple[str, float]]:
    out = []
    d = start
    remain = hours
    while remain > 1e-9:
        chunk = min(max_per_day, remain)
        out.append((d.isoformat(), chunk))
        remain -= chunk
        d += timedelta(days=1)
    return out


def search_uid(name: str, pk: str) -> tuple[str | None, dict | None]:
    biz = {
        "pageNo": 1,
        "pageSize": 10,
        "name": sm2_encrypt(name, pk),
        "activityId": ACTIVITY_ID,
        "postId": POST_ID,
        "orgId": ORG_ID,
    }
    data = unwrap(call("activityUser-findOrgUserList", biz, access_token=TOKEN))
    lst = data["resultData"]["dataList"]
    if not lst:
        return None, None
    if len(lst) > 1:
        print(f"  [warn] {name}: {len(lst)} matches, using first ({lst[0]['nameSensitive']})")
    u = lst[0]
    return u["uid"], u


def ensure_member(uids: list[str]) -> None:
    biz = {
        "activityId": ACTIVITY_ID,
        "postId": POST_ID,
        "orgId": ORG_ID,
        "uids": uids,
    }
    unwrap(call("activityUser-addList", biz, access_token=TOKEN))


def batch_add(uid: str, times: list[tuple[str, float]], file_path: str) -> dict:
    biz = {
        "activityId": ACTIVITY_ID,
        "postId": POST_ID,
        "orgId": ORG_ID,
        "notes": NOTES,
        "uids": [uid],
        "times": [{"time": t, "hour": h} for t, h in times],
        "filePath": file_path,
    }
    return unwrap(call("activityTiming-batchAdd", biz, access_token=TOKEN))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xls", type=Path)
    ap.add_argument("--start", default=date.today().isoformat())
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    roster = parse_roster(args.xls)
    print(f"[parse] {len(roster)} record(s) from {args.xls.name}")
    for n, h in roster:
        print(f"        {n}: {h}h")

    print(f"[fetch] inSm2Key public key")
    pk = get_in_sm2_pk(TOKEN)
    print(f"        pk={pk[:24]}... len={len(pk)}")

    print(f"[search] resolve uids")
    resolved: list[tuple[str, str, str, list[tuple[str, float]]]] = []
    missing: list[str] = []
    for name, hours in roster:
        uid, u = search_uid(name, pk)
        if not uid:
            missing.append(name)
            print(f"        ✗ {name} → no match")
            continue
        plan = allocate(hours, start, args.max_hours)
        plan_str = ", ".join(f"{t}={h}h" for t, h in plan)
        print(f"        ✓ {name} → uid={uid} ({u['nameSensitive']})  plan: {plan_str}")
        resolved.append((name, uid, u["nameSensitive"], plan))

    if not resolved:
        print("nothing to submit.")
        return 1

    if args.dry_run:
        print("[dry-run] skipping addList + upload + batchAdd")
        return 0

    print(f"[member] addList {len(resolved)} uid(s) → ensure post membership before batchAdd")
    ensure_member([uid for _, uid, _, _ in resolved])
    print(f"         done")

    print(f"[submit] upload+batchAdd × {len(resolved)}")
    results = []
    for name, uid, ns, plan in resolved:
        try:
            file_path = upload_proof(None)
            r = batch_add(uid, plan, file_path)
            ok = bool(r.get("resultData", False))
            print(f"         {'✓' if ok else '?'} {name} (uid={uid}): {json.dumps(r, ensure_ascii=False)}")
            results.append((name, ok))
        except Exception as e:
            print(f"         ✗ {name} (uid={uid}): {e}")
            results.append((name, False))

    ok_n = sum(1 for _, ok in results if ok)
    print(f"\nsummary: {ok_n}/{len(results)} ok. missing-by-name: {missing or 'none'}")
    return 0 if ok_n == len(results) and not missing else 1


if __name__ == "__main__":
    sys.exit(main())

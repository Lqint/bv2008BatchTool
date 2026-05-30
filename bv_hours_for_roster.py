"""Record hours for all already-recruited volunteers in a post.

Fetches the full recruited-volunteer roster via findRecruitVolunteerList,
then applies uniform hours to everyone (or a subset via --filter-uid).

NOTE: This script targets volunteers already in the post roster (state=5).
No addList step needed — they are already members.

Usage:
    python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01
    python3 bv_hours_for_roster.py --hours 8 --start 2026-05-01 --dry-run
    python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01 --filter-uid 90873434 234222082

Configure TOKEN / ACTIVITY_ID / POST_ID / ORG_ID in config.py first.
"""
import sys
import argparse
import json
from datetime import date, timedelta

from bv_client import call, unwrap
from bv_record_hours import upload_proof

try:
    from config import TOKEN, ACTIVITY_ID, POST_ID, ORG_ID
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and fill in values")

NOTES = ""


def fetch_roster(activity_id: str, post_id: str, state: str = "5") -> list[dict]:
    """Fetch all recruited volunteers, paginating automatically."""
    page, size = 1, 50
    out = []
    while True:
        biz = {
            "pageNo": page,
            "pageSize": size,
            "state": state,
            "activityId": activity_id,
            "postId": post_id,
        }
        data = unwrap(call("findRecruitVolunteerList", biz, access_token=TOKEN))
        lst = data["resultData"]["dataList"]
        out.extend(lst)
        total = data["resultData"]["totalCount"]
        if len(out) >= total:
            break
        page += 1
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
    ap.add_argument("--hours", type=float, required=True, help="hours per person")
    ap.add_argument("--start", default=date.today().isoformat(), help="start date YYYY-MM-DD")
    ap.add_argument("--max-hours", type=float, default=8.0, help="max hours per day (spill to next)")
    ap.add_argument("--filter-uid", nargs="*", metavar="UID", help="only apply to these uids")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    plan_template = allocate(args.hours, start, args.max_hours)
    plan_str = ", ".join(f"{t}={h}h" for t, h in plan_template)

    print(f"[1/3] fetch roster (postId={POST_ID[:8]}...)")
    roster = fetch_roster(ACTIVITY_ID, POST_ID)
    print(f"      {len(roster)} volunteer(s) found")

    if args.filter_uid:
        keep = set(args.filter_uid)
        roster = [u for u in roster if u["uid"] in keep]
        print(f"      filtered to {len(roster)} uid(s)")

    if not roster:
        print("      nothing to process.")
        return 1

    print(f"\n[2/3] plan  hours={args.hours}  {plan_str}")
    for u in roster:
        print(f"      {u['nameSensitive']}  uid={u['uid']}  userNumber={u['userNumber']}")

    if args.dry_run:
        print("\n[dry-run] skipping upload + batchAdd")
        return 0

    print(f"\n[3/3] submit × {len(roster)}")
    results = []
    for u in roster:
        uid = u["uid"]
        name = u["nameSensitive"]
        try:
            fp = upload_proof(None)
            r = batch_add(uid, plan_template, fp)
            ok = bool(r.get("resultData", False))
            print(f"      {'✓' if ok else '?'} {name} uid={uid}: {json.dumps(r, ensure_ascii=False)}")
            results.append(ok)
        except Exception as e:
            print(f"      ✗ {name} uid={uid}: {e}")
            results.append(False)

    ok_n = sum(results)
    print(f"\nsummary: {ok_n}/{len(results)} ok")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

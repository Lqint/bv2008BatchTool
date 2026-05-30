"""List posts under an activity.

Usage:
    python3 bv_find_posts.py
    python3 bv_find_posts.py --activity-id 1510325811016630272 --org-id <orgId>

Configure TOKEN / ACTIVITY_ID / ORG_ID in config.py first, or pass IDs on the
command line.
"""
import argparse
import json
import sys

from bv_client import call

try:
    from config import TOKEN, ACTIVITY_ID, ORG_ID
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and fill in values")


def parse_post_list_response(resp: dict) -> list[dict]:
    raw_data = resp.get("data")
    inner = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    if not isinstance(inner, dict):
        raise RuntimeError(f"unexpected response data: {raw_data!r}")

    code = str(inner.get("code", ""))
    if code != "200":
        msg = inner.get("message") or inner.get("msg") or "findPostList failed"
        raise RuntimeError(f"{msg} ({code or 'missing code'})")

    data = inner.get("data", {})
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected business data: {data!r}")

    result_data = data.get("resultData", {})
    data_list = result_data.get("dataList", [])
    if not isinstance(data_list, list):
        raise RuntimeError(f"unexpected dataList: {data_list!r}")
    return data_list


def find_posts(activity_id: str, org_id: str) -> list[dict]:
    biz = {
        "activityId": activity_id,
        "orgId": org_id,
    }
    resp = call("findPostList", biz, access_token=TOKEN, app_id="zybjfront")
    return parse_post_list_response(resp)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity-id", default=ACTIVITY_ID)
    ap.add_argument("--org-id", default=ORG_ID)
    args = ap.parse_args(argv)

    if not args.activity_id or not args.org_id:
        print("activityId and orgId are required. Fill config.py or pass --activity-id/--org-id.")
        return 2

    posts = find_posts(args.activity_id, args.org_id)
    if not posts:
        print("no posts found.")
        return 1

    print(f"{len(posts)} post(s) found:")
    for post in posts:
        name = post.get("postName", "")
        code = post.get("postCode", "")
        print(f"  {name}\t{code}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

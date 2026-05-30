"""Batch import volunteers into a recruit post.

Usage:
    python3 bv_import.py names.txt
    python3 bv_import.py 张三 李四

Configure TOKEN / ACTIVITY_ID / POST_ID / ORG_ID in config.py first.
"""
import sys
import json
from pathlib import Path

from bv_client import call, unwrap, sm2_encrypt, get_in_sm2_pk

try:
    from config import TOKEN, ACTIVITY_ID, POST_ID, ORG_ID
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and fill in values")


def load_names(args: list[str]) -> list[str]:
    if len(args) == 1 and Path(args[0]).is_file():
        return [ln.strip() for ln in Path(args[0]).read_text(encoding="utf-8").splitlines() if ln.strip()]
    return args


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
        print(f"[warn] {name}: {len(lst)} matches, using first ({lst[0]['nameSensitive']})")
    u = lst[0]
    return u["uid"], u


def add_batch(uids: list[str]) -> dict:
    biz = {
        "activityId": ACTIVITY_ID,
        "postId": POST_ID,
        "orgId": ORG_ID,
        "uids": uids,
    }
    return unwrap(call("activityUser-addList", biz, access_token=TOKEN))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    names = load_names(argv)
    print(f"[1/3] fetch inSm2Key public key...")
    pk = get_in_sm2_pk(TOKEN)
    print(f"      pk={pk[:24]}... (len={len(pk)})")

    print(f"[2/3] search {len(names)} name(s) → uid")
    found: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for n in names:
        uid, u = search_uid(n, pk)
        if uid:
            found.append((n, uid, u["nameSensitive"]))
            print(f"      ✓ {n} → uid={uid} ({u['nameSensitive']}, userNumber={u['userNumber']})")
        else:
            missing.append(n)
            print(f"      ✗ {n} → no match")

    if not found:
        print("[3/3] nothing to add.")
        return 1

    print(f"[3/3] addList batch of {len(found)} uid(s)")
    add_batch([uid for _, uid, _ in found])
    print(f"      ok. added {len(found)}, missing {len(missing)}.")
    if missing:
        print(f"      missing: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

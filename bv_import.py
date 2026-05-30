"""Batch import volunteers into a recruit post.

Usage:
    python3 bv_import.py names.txt
    python3 bv_import.py members.csv          # CSV/TSV/plain text: name[,certNo]
    python3 bv_import.py --cert-no 110101199001011234 张三
    python3 bv_import.py 张三 李四

Configure TOKEN / ACTIVITY_ID / POST_ID / ORG_ID in config.py first.
"""
import sys
import csv
from pathlib import Path

from bv_client import call, unwrap, sm2_encrypt, get_in_sm2_pk

try:
    from config import TOKEN, ACTIVITY_ID, POST_ID, ORG_ID
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and fill in values")


def load_entries_from_file(path: Path) -> list[tuple[str, str | None]]:
    entries = []
    with path.open(encoding="utf-8", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t") if "," in sample or "\t" in sample else None
        except csv.Error:
            dialect = "excel-tab" if "\t" in sample and "," not in sample else "excel"
        rows = csv.reader(f, dialect) if dialect else ([ln.strip()] for ln in f)
        for row in rows:
            if not row:
                continue
            name = str(row[0]).strip()
            cert_no = str(row[1]).strip() if len(row) >= 2 and str(row[1]).strip() else None
            if name and name not in {"姓名", "学生姓名", "name"}:
                entries.append((name, cert_no))
    return entries


def load_entries(args: list[str]) -> list[tuple[str, str | None]]:
    if len(args) == 1 and Path(args[0]).is_file():
        return load_entries_from_file(Path(args[0]))
    if len(args) == 3 and args[0] == "--cert-no":
        return [(args[2], args[1])]
    return [(name, None) for name in args]


def search_uid(name: str, cert_no: str | None, pk: str) -> tuple[str | None, dict | None, str | None]:
    biz = {
        "pageNo": 1,
        "pageSize": 10,
        "name": sm2_encrypt(name, pk),
        "activityId": ACTIVITY_ID,
        "postId": POST_ID,
        "orgId": ORG_ID,
    }
    if cert_no:
        biz["certNo"] = sm2_encrypt(cert_no, pk)
    data = unwrap(call("activityUser-findOrgUserList", biz, access_token=TOKEN))
    lst = data["resultData"]["dataList"]
    if not lst:
        return None, None, None
    if len(lst) > 1:
        extra = " + certNo" if cert_no else ""
        return None, None, f"{name}{extra}: {len(lst)} matches, skipped"
    u = lst[0]
    return u["uid"], u, None


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
    entries = load_entries(argv)
    print(f"[1/3] fetch inSm2Key public key...")
    pk = get_in_sm2_pk(TOKEN)
    print(f"      pk={pk[:24]}... (len={len(pk)})")

    print(f"[2/3] search {len(entries)} member(s) → uid")
    found: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for n, cert_no in entries:
        uid, u, warning = search_uid(n, cert_no, pk)
        lookup = f"{n} + certNo={cert_no[:6]}****" if cert_no else n
        if warning:
            missing.append(lookup)
            print(f"      ⚠ {warning}")
            continue
        if uid:
            found.append((n, uid, u["nameSensitive"]))
            print(f"      ✓ {lookup} → uid={uid} ({u['nameSensitive']}, userNumber={u['userNumber']})")
        else:
            missing.append(lookup)
            print(f"      ✗ {lookup} → no match")

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

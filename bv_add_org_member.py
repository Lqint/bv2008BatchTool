"""Add a volunteer to the organization member pool by name + ID number.

This is a prerequisite step for volunteers who are registered on the bv2008 platform
but have not yet joined your organization. Once added, they become searchable via
findOrgUserList and can be enrolled in activity posts.

Error 50000 "添加的人尚未注册为志愿者" means the person has no bv2008 account at all
and must register on the platform before they can be added.

Usage:
    python3 bv_add_org_member.py 施凯鑫 210221200601187519
    python3 bv_add_org_member.py --file members.csv     # CSV: name,certNo per line

Configure TOKEN in config.py first.
"""
import sys
import json
import csv
from pathlib import Path

from bv_client import call, get_in_sm2_pk, sm2_encrypt

try:
    from config import TOKEN
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and fill in values")


def add_member(name: str, cert_no: str, pk: str) -> tuple[bool, str]:
    biz = {
        "name":   sm2_encrypt(name, pk),
        "certNo": sm2_encrypt(cert_no, pk),
    }
    raw = call("addMember", biz, access_token=TOKEN, app_id="zybjfront")
    inner = json.loads(raw["data"])

    data = inner.get("data", {})
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {"message": data}
    if not isinstance(data, dict):
        data = {}

    code = str(data.get("code", ""))
    msg = data.get("message") or inner.get("message", "")
    if code == "200":
        return True, msg or "success"
    if code == "50000":
        return False, msg or "添加失败"
    if inner.get("success") is False:
        return False, msg or f"gateway error: {inner.get('code')}"
    return False, msg or f"unexpected code: {code or 'missing'}"


def load_csv(path: Path) -> list[tuple[str, str]]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                name, cert = row[0].strip(), row[1].strip()
                if name and cert:
                    rows.append((name, cert))
    return rows


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    if argv[0] == "--file":
        if len(argv) < 2:
            raise SystemExit("--file requires a path argument")
        pairs = load_csv(Path(argv[1]))
    elif len(argv) == 2:
        pairs = [(argv[0], argv[1])]
    else:
        print(__doc__)
        return 2

    print(f"fetch inSm2Key...")
    pk = get_in_sm2_pk(TOKEN)

    ok_n = 0
    for name, cert_no in pairs:
        ok, msg = add_member(name, cert_no, pk)
        status = "✓" if ok else "✗"
        print(f"  {status} {name}  certNo={cert_no[:6]}****  {msg}")
        if ok:
            ok_n += 1

    print(f"\n{ok_n}/{len(pairs)} ok")
    return 0 if ok_n == len(pairs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

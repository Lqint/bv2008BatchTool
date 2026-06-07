"""List all bv2008 projects for an organization.

Usage:
    python3 bv_list_projects.py
    python3 bv_list_projects.py --org-id 223718004
    python3 bv_list_projects.py --org-id 223718004 --json

Configure TOKEN / ORG_ID in config.py first. You can also override them with
--token and --org-id.

The returned row's "projectId" is the project iid. Use a second-level projectId
with bv_list_activities.py or findListByActivityIdAndName.
"""
import argparse
import json
import sys
from typing import Any

from bv_client import call, unwrap

try:
    from config import ORG_ID, TOKEN
except ImportError:
    TOKEN = ""
    ORG_ID = ""


def query_project_list(token: str, org_id: str, parent_id: str | None = None) -> list[dict[str, Any]]:
    biz = {"orgId": org_id}
    if parent_id:
        biz["parentId"] = parent_id
    data = unwrap(call("queryProjectList", biz, access_token=token))
    return data["resultData"].get("dataList") or []


def normalize_project(row: dict[str, Any], level: int) -> dict[str, Any]:
    return {
        "level": level,
        "projectId": row.get("iid"),
        "projectName": row.get("proName"),
        "parentId": row.get("parentId"),
        "orgId": row.get("orgId"),
        "startTime": row.get("startTime"),
        "endTime": row.get("endTime"),
        "raw": row,
    }


def list_all_projects(token: str, org_id: str) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    for row in query_project_list(token, org_id):
        project_id = str(row.get("iid") or "")
        if not project_id:
            continue

        key = (1, project_id)
        if key not in seen:
            projects.append(normalize_project(row, level=1))
            seen.add(key)

        for child in query_project_list(token, org_id, parent_id=project_id):
            child_id = str(child.get("iid") or "")
            if not child_id:
                continue
            child_key = (2, child_id)
            if child_key not in seen:
                projects.append(normalize_project(child, level=2))
                seen.add(child_key)

    return projects


def print_table(projects: list[dict[str, Any]]) -> None:
    print(f"{'level':<5} {'project_id':<34} {'parent_id':<34} {'date':<23} name")
    print("-" * 120)
    for p in projects:
        date_range = f"{p.get('startTime') or ''}..{p.get('endTime') or ''}"
        indent = "  " if p["level"] > 1 else ""
        print(
            f"{p['level']:<5} "
            f"{str(p.get('projectId') or ''):<34} "
            f"{str(p.get('parentId') or ''):<34} "
            f"{date_range:<23} "
            f"{indent}{p.get('projectName') or ''}"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="List all projects by org_id.")
    parser.add_argument("--token", default=TOKEN, help="accessToken JWT; defaults to config.TOKEN")
    parser.add_argument("--org-id", default=ORG_ID, help="organization id; defaults to config.ORG_ID")
    parser.add_argument("--json", action="store_true", help="print normalized JSON")
    parser.add_argument("--raw", action="store_true", help="print raw API rows in JSON")
    args = parser.parse_args(argv)

    if not args.token or not args.org_id:
        print(__doc__)
        return 2

    print(f"[1/2] query projects for orgId={args.org_id}...", file=sys.stderr)
    projects = list_all_projects(args.token, args.org_id)
    print(f"[2/2] found {len(projects)} project row(s).", file=sys.stderr)

    if args.raw:
        print(json.dumps([p["raw"] for p in projects], ensure_ascii=False, indent=2))
    elif args.json:
        slim = [{k: v for k, v in p.items() if k != "raw"} for p in projects]
        print(json.dumps(slim, ensure_ascii=False, indent=2))
    else:
        print_table(projects)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

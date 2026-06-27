from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

PUBLIC_DIR = Path(__file__).resolve().parents[1]
if str(PUBLIC_DIR) not in sys.path:
    sys.path.insert(0, str(PUBLIC_DIR))

from bv_client import GATEWAY, call, get_in_sm2_pk, make_sign, sm2_encrypt, unwrap


def _coerce_orgs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in ("orgId", "org_id", "iid", "id")):
            values = [value]
        else:
            values = []
            for key in ("dataList", "list", "records", "rows"):
                rows = value.get(key)
                if isinstance(rows, list):
                    values = rows
                    break
    elif isinstance(value, list):
        values = value
    else:
        values = []

    orgs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        org = dict(item)
        org_id = str(org.get("orgId") or org.get("org_id") or org.get("iid") or org.get("id") or "").strip()
        if not org_id or org_id in seen:
            continue
        org["orgId"] = org_id
        if "orgName" not in org and org.get("name"):
            org["orgName"] = org["name"]
        orgs.append(org)
        seen.add(org_id)
    return orgs


def call_no_auth(interface_id: str, params: dict[str, Any], app_id: str = "zybjuser") -> dict[str, Any]:
    form = {
        "app_id": app_id,
        "interface_id": interface_id,
        "version": "1.0",
        "header": '{"accessSource":"pc"}',
        "biz_content": json.dumps(params, separators=(",", ":"), ensure_ascii=False),
        "charset": "utf8",
        "timestamp": str(int(time.time() * 1000)),
        "origin": "1",
    }
    form["sign"] = make_sign(form)
    files = {key: (None, value) for key, value in form.items()}
    response = requests.post(GATEWAY, files=files, timeout=30)
    response.raise_for_status()
    return response.json()


def unwrap_raw(resp: dict[str, Any]) -> dict[str, Any]:
    return json.loads(resp["data"])


def create_login_qr() -> tuple[str, str]:
    inner = unwrap_raw(call_no_auth("createCityCode", {}))
    if not inner.get("success"):
        raise RuntimeError(f"createCityCode failed: {inner.get('message')} ({inner.get('code')})")
    data = inner["data"]["resultData"]
    return data["codeId"], data["codeData"]["codeContent"]


def check_login_status(code_id: str) -> tuple[str, str | None]:
    inner = unwrap_raw(call_no_auth("checkCodeStatus", {"codeId": code_id}))
    if not inner.get("success"):
        raise RuntimeError(f"checkCodeStatus failed: {inner.get('message')} ({inner.get('code')})")
    result = inner["data"]["resultData"]
    status = str(result.get("status", ""))
    token = None
    if status == "3":
        token = result["data"]["accessToken"]
    return status, token


def fetch_current_orgs(token: str) -> dict[str, Any]:
    data = unwrap(call("zybjfrontcurrUserInfo", {}, access_token=token, app_id="zybjuser"))
    result = data.get("resultData") or {}
    role_info = result.get("currUserRoleInfo") or {}
    user = role_info.get("user") or result.get("user") or {}
    orgs = _coerce_orgs(
        role_info.get("currOrgInfo")
        or result.get("currOrgInfo")
        or result.get("orgs")
        or result.get("orgList")
        or result.get("orgInfo")
    )
    default_org_id = str(
        result.get("defaultOrgId")
        or role_info.get("defaultOrgId")
        or user.get("defaultOrgId")
        or ""
    ).strip()
    if not default_org_id and orgs:
        default_org_id = str(orgs[0].get("orgId") or "")
    if default_org_id and not orgs:
        orgs = [{"orgId": default_org_id, "orgName": f"默认组织 {default_org_id}"}]
    return {
        "user": user,
        "defaultOrgId": default_org_id,
        "orgs": orgs,
    }


def query_project_list(token: str, org_id: str, parent_id: str | None = None) -> list[dict[str, Any]]:
    biz: dict[str, Any] = {"orgId": org_id}
    if parent_id:
        biz["parentId"] = parent_id
    data = unwrap(call("queryProjectList", biz, access_token=token))
    return data["resultData"].get("dataList") or []


def fetch_selectable_projects(token: str, org_id: str) -> list[dict[str, Any]]:
    second_level: list[dict[str, Any]] = []
    fallback_top: list[dict[str, Any]] = []
    for top in query_project_list(token, org_id):
        top_id = str(top.get("iid") or "")
        if not top_id:
            continue
        children = query_project_list(token, org_id, top_id)
        if children:
            second_level.extend({**child, "_level": 2} for child in children)
        else:
            fallback_top.append({**top, "_level": 1})
    return second_level or fallback_top


def fetch_activities(token: str, project_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for state in ("1", "2", "3"):
        page = 1
        state_seen = 0
        while True:
            biz = {"pageNo": page, "pageSize": 50, "state": state, "projectId": project_id}
            data = unwrap(call("findListByActivityIdAndName", biz, access_token=token))
            result = data["resultData"]
            batch = result.get("dataList") or []
            state_seen += len(batch)
            for item in batch:
                activity_id = str(item.get("iid") or "")
                if activity_id and activity_id not in seen:
                    rows.append({**item, "_queriedState": state})
                    seen.add(activity_id)
            total = int(result.get("totalCount") or 0)
            if not batch or state_seen >= total:
                break
            page += 1
    return rows


def fetch_posts(token: str, activity_id: str) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    page = 1
    while True:
        biz = {"activityId": activity_id, "pageNo": page, "pageSize": 50}
        data = unwrap(call("activityPost-findListByActivityId", biz, access_token=token))
        result = data["resultData"]
        batch = result.get("dataList") or []
        posts.extend(batch)
        total = int(result.get("totalCount") or 0)
        if not batch or len(posts) >= total:
            break
        page += 1
    return posts


def fetch_roster(token: str, activity_id: str, post_id: str, state: str = "5") -> list[dict[str, Any]]:
    roster: list[dict[str, Any]] = []
    page = 1
    while True:
        biz = {
            "pageNo": page,
            "pageSize": 50,
            "state": state,
            "activityId": activity_id,
            "postId": post_id,
        }
        data = unwrap(call("findRecruitVolunteerList", biz, access_token=token))
        result = data["resultData"]
        batch = result.get("dataList") or []
        roster.extend(batch)
        total = int(result.get("totalCount") or 0)
        if not batch or len(roster) >= total:
            break
        page += 1
    return roster


def search_org_user(token: str, name: str, pk: str, activity_id: str, post_id: str, org_id: str) -> list[dict[str, Any]]:
    biz = {
        "pageNo": 1,
        "pageSize": 10,
        "name": sm2_encrypt(name, pk),
        "activityId": activity_id,
        "postId": post_id,
        "orgId": org_id,
    }
    data = unwrap(call("activityUser-findOrgUserList", biz, access_token=token))
    return data["resultData"].get("dataList") or []


def add_org_member(token: str, name: str, cert_no: str, pk: str) -> tuple[bool, str]:
    raw = call(
        "addMember",
        {"name": sm2_encrypt(name, pk), "certNo": sm2_encrypt(cert_no, pk)},
        access_token=token,
        app_id="zybjfront",
    )
    inner = json.loads(raw["data"])
    return str(inner.get("code")) == "200", str(inner.get("message") or "")


def add_post_members(token: str, activity_id: str, post_id: str, org_id: str, uids: list[str]) -> None:
    biz = {"activityId": activity_id, "postId": post_id, "orgId": org_id, "uids": uids}
    unwrap(call("activityUser-addList", biz, access_token=token))


def get_public_key(token: str) -> str:
    return get_in_sm2_pk(token)

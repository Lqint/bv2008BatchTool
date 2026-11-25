from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from bv_client import call, unwrap

from . import api
from .app import allocate, is_auth_error, parse_record_iids, upload_proof
from .matching import candidate_summary, collect_candidates
from .config_store import load_config, save_config
from .excel import ImportRow, read_excel


class CliUsageError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(f"{message}; {self.format_usage().strip()}")


def _configure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _emit(payload: dict[str, Any], *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=stream, flush=True)


def _token(args: argparse.Namespace, cfg: dict[str, str]) -> str:
    token = str(getattr(args, "token", "") or os.environ.get("BV2008_TOKEN") or cfg.get("token") or "").strip()
    if not token:
        raise ValueError("缺少 token：请先在 GUI 登录、设置 BV2008_TOKEN，或传全局参数 --token")
    return token


def _configured(args: argparse.Namespace, cfg: dict[str, str], argument: str, config_key: str) -> str:
    value = str(getattr(args, argument, "") or cfg.get(config_key) or "").strip()
    if not value:
        raise ValueError(f"缺少 {config_key}：请先在 GUI 配置或传 --{argument.replace('_', '-')}")
    return value


def _masked_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 12:
        return "***"
    return f"{token[:4]}...{token[-8:]}"


def _row_result(row: ImportRow, plan: list[tuple[str, float]]) -> dict[str, Any]:
    return {
        "name": row.name,
        "hours": row.hours,
        "hasCertNo": bool(row.cert_no),
        "plan": [{"date": day, "hours": hours} for day, hours in plan],
        "uid": "",
        "member": "pending",
        "timing": "pending",
        "ok": False,
        "error": "",
    }


def _run_import(args: argparse.Namespace, cfg: dict[str, str]) -> tuple[dict[str, Any], int]:
    rows = read_excel(args.file)
    start = date.fromisoformat(args.start)
    if args.max_hours <= 0:
        raise ValueError("--max-hours 必须大于 0")
    results = [_row_result(row, allocate(row.hours, start, args.max_hours)) for row in rows]
    if args.dry_run:
        return {"dryRun": True, "file": str(Path(args.file).resolve()), "count": len(rows), "rows": results}, 0
    if not args.confirm:
        raise ValueError("正式导入会修改远端数据，必须显式传入 --confirm；可先使用 --dry-run")

    token = _token(args, cfg)
    activity_id = _configured(args, cfg, "activity_id", "activity_id")
    post_id = _configured(args, cfg, "post_id", "post_id")
    org_id = _configured(args, cfg, "org_id", "org_id")
    pk = api.get_public_key(token)

    roster_error = ""
    try:
        roster = api.fetch_roster(token, activity_id, post_id)
    except Exception as exc:
        if is_auth_error(exc):
            raise
        roster = []
        roster_error = str(exc)

    uid_by_index: dict[int, str] = {}
    already_member: set[int] = set()
    for index, row in enumerate(rows):
        result = results[index]
        try:
            users = api.search_org_user(token, row.name, pk, activity_id, post_id, org_id)
            candidates = collect_candidates(row, users, roster)
            if not candidates and row.cert_no:
                ok, message = api.add_org_member(token, row.name, row.cert_no, pk)
                if not ok:
                    result["error"] = f"addMember: {message}"
                else:
                    users = api.search_org_user(token, row.name, pk, activity_id, post_id, org_id)
                    candidates = collect_candidates(row, users, roster)
            if not candidates:
                result["error"] = "未找到组织成员"
                continue
            if len(candidates) > 1:
                result["candidates"] = [candidate_summary(user) for user in candidates]
                result["member"] = "ambiguous"
                result["timing"] = "skipped"
                result["error"] = "多名候选，请在 GUI 中核对账号 ID 并手动选择；此行已跳过"
                continue
            user = candidates[0]
            uid_by_index[index] = user["uid"]
            result["uid"] = user["uid"]
            if user["_in_roster"]:
                already_member.add(index)
                result["member"] = "already"
        except Exception as exc:
            if is_auth_error(exc):
                raise
            result["error"] = str(exc)

    add_indexes = [index for index in sorted(uid_by_index) if index not in already_member]
    if add_indexes:
        try:
            api.add_post_members(token, activity_id, post_id, org_id, [uid_by_index[index] for index in add_indexes])
            for index in add_indexes:
                results[index]["member"] = "added"
        except Exception as exc:
            if is_auth_error(exc):
                raise
            for index in add_indexes:
                results[index]["member"] = "failed"
                results[index]["error"] = f"加入岗位失败: {exc}"

    valid = [index for index in sorted(uid_by_index) if results[index]["member"] in ("added", "already")]
    for index in valid:
        row = rows[index]
        result = results[index]
        try:
            file_path = upload_proof(token)
            biz = {
                "activityId": activity_id,
                "postId": post_id,
                "orgId": org_id,
                "notes": "",
                "uids": [uid_by_index[index]],
                "times": [{"time": item["date"], "hour": item["hours"]} for item in result["plan"]],
                "filePath": file_path,
            }
            unwrap(call("activityTiming-batchAdd", biz, access_token=token))
            result["timing"] = "added"
            result["ok"] = True
            result["error"] = ""
        except Exception as exc:
            if is_auth_error(exc):
                raise
            result["timing"] = "failed"
            result["error"] = f"录入时数失败: {exc}"

    succeeded = sum(bool(item["ok"]) for item in results)
    payload = {
        "dryRun": False,
        "file": str(Path(args.file).resolve()),
        "activityId": activity_id,
        "postId": post_id,
        "orgId": org_id,
        "count": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "rosterWarning": roster_error,
        "rows": results,
    }
    return payload, 0 if succeeded == len(results) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="bv2008_gui cli", description="bv2008 GUI 的 Agent 友好命令行入口（JSON 输出）")
    parser.add_argument("--token", default="", help="临时 accessToken；也可使用 BV2008_TOKEN 环境变量或 GUI 已保存登录态")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="创建或检查扫码登录")
    login_sub = login.add_subparsers(dest="login_command", required=True)
    login_sub.add_parser("qr", help="创建登录二维码，返回 codeId 和 URL")
    login_check = login_sub.add_parser("check", help="检查二维码登录状态")
    login_check.add_argument("code_id")
    login_check.add_argument("--save", action="store_true", help="登录成功时保存 token，供 GUI 与后续 CLI 共用")
    login_wait = login_sub.add_parser("wait", help="轮询等待扫码完成")
    login_wait.add_argument("code_id")
    login_wait.add_argument("--timeout", type=float, default=120.0)
    login_wait.add_argument("--interval", type=float, default=2.0)
    login_wait.add_argument("--save", action="store_true", help="登录成功时保存 token")

    config = subparsers.add_parser("config", help="读取或修改 GUI 共用配置")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_show = config_sub.add_parser("show", help="显示配置（token 默认脱敏）")
    config_show.add_argument("--include-token", action="store_true")
    config_set = config_sub.add_parser("set", help="设置一个或多个配置项")
    token_group = config_set.add_mutually_exclusive_group()
    token_group.add_argument("--saved-token", default=None, help="保存 token 到 GUI 配置")
    token_group.add_argument("--clear-token", action="store_true")
    config_set.add_argument("--org-id", default=None)
    config_set.add_argument("--activity-id", default=None)
    config_set.add_argument("--post-id", default=None)

    listing = subparsers.add_parser("list", help="查询 GUI 中可见的业务数据")
    list_sub = listing.add_subparsers(dest="list_command", required=True)
    list_sub.add_parser("orgs", help="列出当前账号组织")
    projects = list_sub.add_parser("projects", help="列出组织的可选项目")
    projects.add_argument("--org-id", default="")
    activities = list_sub.add_parser("activities", help="列出项目活动")
    activities.add_argument("--project-id", required=True)
    posts = list_sub.add_parser("posts", help="列出活动岗位")
    posts.add_argument("--activity-id", default="")
    roster = list_sub.add_parser("roster", help="列出岗位成员")
    roster.add_argument("--activity-id", default="")
    roster.add_argument("--post-id", default="")
    roster.add_argument("--state", default="5")

    importing = subparsers.add_parser("import", help="预览或执行 Excel 全流程")
    importing.add_argument("file")
    importing.add_argument("--start", default=date.today().isoformat())
    importing.add_argument("--max-hours", type=float, default=8.0)
    importing.add_argument("--dry-run", action="store_true")
    importing.add_argument("--confirm", action="store_true", help="确认正式修改远端成员和时长数据")
    importing.add_argument("--org-id", default="")
    importing.add_argument("--activity-id", default="")
    importing.add_argument("--post-id", default="")

    undo = subparsers.add_parser("undo", help="撤销成员或时长记录")
    undo_sub = undo.add_subparsers(dest="undo_command", required=True)
    undo_member = undo_sub.add_parser("member", help="按成员关系 iid 删除活动成员")
    undo_member.add_argument("iids", nargs="+")
    undo_member.add_argument("--confirm", action="store_true", required=True, help="确认执行永久删除")
    undo_timing = undo_sub.add_parser("timing", help="按记录 iid 删除时长")
    undo_timing.add_argument("iids", nargs="+")
    undo_timing.add_argument("--activity-id", default="")
    undo_timing.add_argument("--confirm", action="store_true", required=True, help="确认执行永久删除")
    return parser


def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    cfg = load_config()
    if args.command == "login":
        if args.login_command == "qr":
            code_id, url = api.create_login_qr()
            return {"codeId": code_id, "url": url}, 0
        if args.login_command == "check":
            status, token = api.check_login_status(args.code_id)
        else:
            if args.timeout <= 0 or args.interval <= 0:
                raise ValueError("--timeout 和 --interval 必须大于 0")
            deadline = time.monotonic() + args.timeout
            status, token = "", None
            while time.monotonic() < deadline:
                status, token = api.check_login_status(args.code_id)
                if status in ("3", "4"):
                    break
                time.sleep(min(args.interval, max(0.0, deadline - time.monotonic())))
        saved = False
        if status == "3" and token and args.save:
            cfg["token"] = token
            save_config(cfg)
            saved = True
        code = 0 if status == "3" else 2 if args.login_command == "wait" else 0
        return {"codeId": args.code_id, "status": status, "accessToken": token or "", "saved": saved}, code

    if args.command == "config":
        if args.config_command == "show":
            result = dict(cfg)
            if not args.include_token:
                result["token"] = _masked_token(result.get("token", ""))
            return result, 0
        updates = {
            "org_id": args.org_id,
            "activity_id": args.activity_id,
            "post_id": args.post_id,
        }
        if args.saved_token is not None:
            updates["token"] = args.saved_token
        if args.clear_token:
            updates["token"] = ""
        changed: list[str] = []
        for key, value in updates.items():
            if value is not None:
                cfg[key] = str(value).strip()
                changed.append(key)
        if not changed:
            raise ValueError("没有提供要修改的配置项")
        save_config(cfg)
        return {"changed": changed}, 0

    if args.command == "list":
        token = _token(args, cfg)
        if args.list_command == "orgs":
            return api.fetch_current_orgs(token), 0
        if args.list_command == "projects":
            org_id = _configured(args, cfg, "org_id", "org_id")
            return {"orgId": org_id, "items": api.fetch_selectable_projects(token, org_id)}, 0
        if args.list_command == "activities":
            return {"projectId": args.project_id, "items": api.fetch_activities(token, args.project_id)}, 0
        if args.list_command == "posts":
            activity_id = _configured(args, cfg, "activity_id", "activity_id")
            return {"activityId": activity_id, "items": api.fetch_posts(token, activity_id)}, 0
        activity_id = _configured(args, cfg, "activity_id", "activity_id")
        post_id = _configured(args, cfg, "post_id", "post_id")
        return {
            "activityId": activity_id,
            "postId": post_id,
            "state": args.state,
            "items": api.fetch_roster(token, activity_id, post_id, args.state),
        }, 0

    if args.command == "import":
        return _run_import(args, cfg)

    token = _token(args, cfg)
    iids = parse_record_iids(" ".join(args.iids))
    if args.undo_command == "member":
        api.delete_activity_members(token, iids)
        return {"type": "member", "deleted": len(iids), "iids": iids}, 0
    activity_id = _configured(args, cfg, "activity_id", "activity_id")
    api.delete_timing_records(token, activity_id, iids)
    return {"type": "timing", "activityId": activity_id, "deleted": len(iids), "iids": iids}, 0


def main(argv: list[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        data, code = dispatch(args)
        _emit({"ok": code == 0, "command": args.command, "data": data})
        return code
    except SystemExit:
        raise
    except Exception as exc:
        _emit({"ok": False, "error": str(exc), "errorType": type(exc).__name__, "authError": is_auth_error(exc)}, error=True)
        return 3 if is_auth_error(exc) else 1

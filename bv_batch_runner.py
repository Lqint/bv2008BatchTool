"""Batch workflow for recording bv2008 volunteer hours from an XLSX file."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from openpyxl import load_workbook

from bv_api import BVApi, PostInfo

ProgressCallback = Callable[[str], None]


@dataclass
class BatchConfig:
    token: str
    activity_id: str
    org_id: str
    start_date: date
    xlsx_path: Path
    output_path: Path
    proof_name: str | None = None
    proof_bytes: bytes | None = None
    proof_mime: str | None = None


REQUIRED_HEADERS = ["姓名", "身份证号（选填）", "岗位", "时长"]
RESULT_HEADER = "录入结果"


def normalize_header(value) -> str:
    return str(value or "").strip()


def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_hours(value) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError("时长为空")
    hours = float(value)
    if hours <= 0:
        raise ValueError("时长必须大于 0")
    return hours


def allocate_hours(hours: float, start: date, today: date | None = None, max_per_day: float = 10.0) -> list[tuple[str, float]]:
    today = today or date.today()
    if start > today:
        raise ValueError("起始日期晚于今天，无法录入")
    available_days = (today - start).days + 1
    max_hours = available_days * max_per_day
    if hours - max_hours > 1e-9:
        raise ValueError(f"从 {start.isoformat()} 到 {today.isoformat()} 最多可录入 {max_hours:g} 小时，不足 {hours:g} 小时")

    out: list[tuple[str, float]] = []
    current = start
    remaining = hours
    while remaining > 1e-9:
        chunk = min(max_per_day, remaining)
        out.append((current.isoformat(), chunk))
        remaining -= chunk
        current += timedelta(days=1)
    return out


def result_output_path(xlsx_path: Path) -> Path:
    return xlsx_path.with_name(f"{xlsx_path.stem}_result{xlsx_path.suffix}")


def build_post_map(posts: list[PostInfo]) -> dict[str, str]:
    post_map: dict[str, str] = {}
    for post in posts:
        post_map[post.name.strip()] = post.post_id
        post_map[post.post_id.strip()] = post.post_id
        if post.post_code:
            post_map[post.post_code.strip()] = post.post_id
    return post_map


def load_sheet(path: Path):
    wb = load_workbook(path)
    ws = wb.active
    headers = [normalize_header(cell.value) for cell in ws[1]]
    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        raise ValueError(f"xlsx 缺少列：{', '.join(missing)}")

    index = {header: headers.index(header) + 1 for header in REQUIRED_HEADERS}
    if RESULT_HEADER in headers:
        result_col = headers.index(RESULT_HEADER) + 1
    else:
        result_col = len(headers) + 1
        ws.cell(row=1, column=result_col, value=RESULT_HEADER)
    return wb, ws, index, result_col


def process_row(
    api: BVApi,
    activity_id: str,
    org_id: str,
    post_id: str,
    name: str,
    cert_no: str,
    hours: float,
    start_date: date,
    proof_name: str | None,
    proof_bytes: bytes | None,
    proof_mime: str | None,
) -> str:
    times = allocate_hours(hours, start_date)

    user = api.find_org_user(name, cert_no, activity_id, post_id, org_id)
    if not user:
        if not cert_no:
            return "失败：按姓名未查询到志愿者，且缺少身份证号，无法加入团体"
        ok, message = api.add_member(name, cert_no)
        if not ok:
            return f"失败：加入团体失败：{message}"
        user = api.find_org_user(name, cert_no, activity_id, "1", org_id)
        if not user:
            return "失败：加入团体后使用通配符仍未查询到 uid"

    uid = str(user.get("uid", "")).strip()
    if not uid:
        return "失败：查询结果缺少 uid"

    try:
        api.add_to_post(activity_id, post_id, org_id, uid)
    except Exception as exc:
        if "已经在此活动中" not in str(exc):
            return f"失败：加入岗位失败 postId={post_id}：{exc}"

    file_path = api.upload_proof(proof_name, proof_bytes, proof_mime)
    result = api.record_hours(activity_id, post_id, org_id, uid, times, file_path)
    if result.get("resultData") is False:
        return f"失败：录入接口返回失败：{result}"
    return "成功"


def run_batch(config: BatchConfig, posts: list[PostInfo], progress: ProgressCallback | None = None) -> Path:
    def log(message: str) -> None:
        if progress:
            progress(message)

    api = BVApi(config.token)
    post_map = build_post_map(posts)
    wb, ws, index, result_col = load_sheet(config.xlsx_path)
    total = max(ws.max_row - 1, 0)
    log(f"读取 {config.xlsx_path.name}，共 {total} 行")

    for row in range(2, ws.max_row + 1):
        name = normalize_text(ws.cell(row=row, column=index["姓名"]).value)
        cert_no = normalize_text(ws.cell(row=row, column=index["身份证号（选填）"]).value)
        post_name = normalize_text(ws.cell(row=row, column=index["岗位"]).value)
        try:
            hours = parse_hours(ws.cell(row=row, column=index["时长"]).value)
        except Exception as exc:
            result = f"失败：{exc}"
            ws.cell(row=row, column=result_col, value=result)
            log(f"[{row - 1}/{total}] {name or '<空姓名>'}：{result}")
            continue

        if not name or not post_name:
            result = "失败：姓名、岗位均不能为空"
            ws.cell(row=row, column=result_col, value=result)
            log(f"[{row - 1}/{total}] {name or '<空姓名>'}：{result}")
            continue

        post_id = post_map.get(post_name)
        if not post_id:
            result = f"失败：未找到岗位：{post_name}"
            ws.cell(row=row, column=result_col, value=result)
            log(f"[{row - 1}/{total}] {name}：{result}")
            continue

        try:
            result = process_row(
                api=api,
                activity_id=config.activity_id,
                org_id=config.org_id,
                post_id=post_id,
                name=name,
                cert_no=cert_no,
                hours=hours,
                start_date=config.start_date,
                proof_name=config.proof_name,
                proof_bytes=config.proof_bytes,
                proof_mime=config.proof_mime,
            )
        except Exception as exc:
            result = f"失败：{exc}"

        ws.cell(row=row, column=result_col, value=result)
        log(f"[{row - 1}/{total}] {name} / {post_name} / {hours:g}h：{result}")

    wb.save(config.output_path)
    log(f"结果已写入：{config.output_path}")
    return config.output_path

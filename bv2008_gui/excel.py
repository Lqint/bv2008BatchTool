from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ImportRow:
    name: str
    hours: float
    cert_no: str = ""


def cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def cell_to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("小时", "").replace("h", "").replace("H", "")
    return float(text)


def normalize_header(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("_", "")
        .replace("-", "")
        .replace("（", "(")
        .replace("）", ")")
    )


def find_header(names: list[str], exact: set[str], contains: tuple[str, ...]) -> int | None:
    normalized = [normalize_header(name) for name in names]
    for i, name in enumerate(normalized):
        if name in exact:
            return i
    for i, name in enumerate(normalized):
        if any(token in name for token in contains):
            return i
    return None


def find_columns(header: list[Any]) -> tuple[int, int, int | None]:
    names = [cell_to_text(item) for item in header]
    name_col = find_header(
        names,
        {"学生姓名", "姓名", "名字", "name", "studentname", "志愿者姓名"},
        ("学生姓名", "志愿者姓名", "姓名"),
    )
    if name_col is None:
        raise ValueError("未找到姓名列：表头建议使用“学生姓名”或“姓名”")

    hour_col = find_header(
        names,
        {"时数", "小时数", "认定时数", "服务时数", "录入时数", "hours", "hour"},
        ("时数", "小时", "服务时间", "认定时间"),
    )
    if hour_col is None:
        raise ValueError("未找到时数列：表头建议使用“认定时数”“服务时数”或“hours”")

    cert_col = find_header(
        names,
        {
            "身份证号",
            "身份证号码",
            "身份证",
            "居民身份证号",
            "居民身份证号码",
            "证件号",
            "证件号码",
            "证件编号",
            "证件",
            "certno",
            "certnumber",
            "idcard",
            "idnumber",
        },
        ("身份证", "证件号", "证件号码", "证件编号", "certno", "idcard", "idnumber"),
    )
    return name_col, hour_col, cert_col


def read_excel(path: str) -> list[ImportRow]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".xls":
        return _read_xls(file_path)
    if suffix == ".xlsx":
        return _read_xlsx(file_path)
    raise ValueError("仅支持 .xls 或 .xlsx 文件")


def _read_xls(path: Path) -> list[ImportRow]:
    try:
        import xlrd
    except Exception as exc:
        raise RuntimeError("读取 .xls 需要安装 xlrd：pip install xlrd") from exc

    wb = xlrd.open_workbook(str(path))
    sheet = wb.sheet_by_index(0)
    if sheet.nrows < 2:
        return []
    name_col, hour_col, cert_col = find_columns(sheet.row_values(0))
    rows: list[ImportRow] = []
    for row_index in range(1, sheet.nrows):
        row = sheet.row_values(row_index)
        name = cell_to_text(row[name_col] if name_col < len(row) else "")
        hours = cell_to_float(row[hour_col] if hour_col < len(row) else 0)
        cert_no = cell_to_text(row[cert_col] if cert_col is not None and cert_col < len(row) else "")
        if name and hours > 0:
            rows.append(ImportRow(name=name, hours=hours, cert_no=cert_no))
    return rows


def _read_xlsx(path: Path) -> list[ImportRow]:
    try:
        from openpyxl import load_workbook
    except Exception as exc:
        raise RuntimeError("读取 .xlsx 需要安装 openpyxl：pip install openpyxl") from exc

    wb = load_workbook(str(path), read_only=True, data_only=True)
    sheet = wb.worksheets[0]
    values = list(sheet.iter_rows(values_only=True))
    if len(values) < 2:
        return []
    name_col, hour_col, cert_col = find_columns(list(values[0]))
    rows: list[ImportRow] = []
    for row in values[1:]:
        row_list = list(row)
        name = cell_to_text(row_list[name_col] if name_col < len(row_list) else "")
        hours = cell_to_float(row_list[hour_col] if hour_col < len(row_list) else 0)
        cert_no = cell_to_text(row_list[cert_col] if cert_col is not None and cert_col < len(row_list) else "")
        if name and hours > 0:
            rows.append(ImportRow(name=name, hours=hours, cert_no=cert_no))
    return rows


def recommended_template_text() -> str:
    return """推荐报表格式

建议直接使用下面这些表头，兼容性最好：

| 学生姓名 | 身份证号 | 认定时数 |
| 张三 | 110101200001010011 | 8 |
| 李四 | 110101200102020022 | 3.5 |

也可以增加备注列，程序会忽略不需要的列：

| 学生姓名 | 身份证号 | 认定时数 | 备注 |
| 王五 | 110101200203030033 | 6 | 春季运动会志愿服务 |

填写建议：
1. “学生姓名”必须与 bv2008 / 志愿北京实名信息一致。
2. “身份证号”建议保留为文本格式，避免 Excel 自动转成科学计数法或丢失末尾 X。
3. “认定时数”只填数字，例如 8、3.5，不要写“8小时”。
4. 每一行对应一个志愿者；同一个人多条记录建议先在表格里合并时数。
5. 表头名称推荐固定为“学生姓名 / 身份证号 / 认定时数”，这样最不容易识别失败。
"""

from __future__ import annotations

import base64
import mimetypes
import queue
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable

import tkinter as tk
from tkinter import ttk

import qrcode

PUBLIC_DIR = Path(__file__).resolve().parents[1]
if str(PUBLIC_DIR) not in sys.path:
    sys.path.insert(0, str(PUBLIC_DIR))

from bv_client import call, unwrap

try:
    from . import api
    from .config_store import load_config, save_config
    from .excel import ImportRow, read_excel, recommended_template_text
    from .widgets import ScrolledText, StatusBar, TreeFrame
except ImportError:
    import api  # type: ignore[no-redef]
    from config_store import load_config, save_config  # type: ignore[no-redef]
    from excel import ImportRow, read_excel, recommended_template_text  # type: ignore[no-redef]
    from widgets import ScrolledText, StatusBar, TreeFrame  # type: ignore[no-redef]


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def allocate(hours: float, start: date, max_per_day: float) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    current = start
    remaining = float(hours)
    while remaining > 1e-9:
        chunk = min(max_per_day, remaining)
        out.append((current.isoformat(), chunk))
        remaining -= chunk
        current += timedelta(days=1)
    return out


def upload_proof(token: str, path: Path | None = None) -> str:
    if path:
        name = path.name
        data = path.read_bytes()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    else:
        name = "proof.png"
        data = TINY_PNG
        mime = "image/png"
    biz = {"file": {"uid": f"vc-upload-{int(time.time() * 1000)}-1"}, "uploadType": "durationFile"}
    resp = call("zybj_uploadFile", biz, access_token=token, app_id="zybjuser", file=(name, data, mime))
    return unwrap(resp)["resultData"]["fileData"]["newName"]


def roster_name_masks(name: str) -> set[str]:
    clean = "".join(str(name).split())
    if not clean:
        return set()
    masks = {"*" + clean[1:]}
    if len(clean) > 2:
        masks.add("*" + clean[-2:])
    return masks


def match_roster_user(row: ImportRow, roster: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    masks = roster_name_masks(row.name)
    matches = [user for user in roster if str(user.get("nameSensitive") or "") in masks and user.get("uid")]
    if len(matches) == 1:
        return matches[0], "matched"
    if len(matches) > 1:
        return None, f"multiple roster matches: {len(matches)}"
    return None, "not found in roster"


class BasePanel(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, padding=12)
        self.app = app

    def ui(self, func: Callable[[], None]) -> None:
        def guarded() -> None:
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            func()

        self.app.ui(guarded)

    def notify(self, text: str) -> None:
        self.app.status.set(text)


class LoginPanel(BasePanel):
    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        self.qr_photo: Any = None
        self.token_var = tk.StringVar()

        ttk.Label(self, text="登录", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", pady=(0, 10))

        qr_box = ttk.LabelFrame(self, text="登录二维码", padding=10)
        qr_box.pack(fill="both", expand=True, pady=(0, 10))
        self.qr_label = ttk.Label(qr_box, text="点击“生成二维码”开始", anchor="center", justify="center")
        self.qr_label.pack(fill="both", expand=True)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 10))
        ttk.Button(row, text="生成二维码", command=self.start_qr).pack(side="left")
        ttk.Button(row, text="清空二维码", command=self.clear_qr).pack(side="left", padx=(8, 0))

        manual = ttk.LabelFrame(self, text="手动输入 Token", padding=10)
        manual.pack(fill="x")
        ttk.Entry(manual, textvariable=self.token_var).pack(side="left", fill="x", expand=True)
        ttk.Button(manual, text="保存 Token", command=self.save_token).pack(side="left", padx=(8, 0))
        self.refresh_status()

    def refresh_status(self) -> None:
        token = self.app.cfg.get("token", "")
        self.status_var.set(f"已登录：token=…{token[-16:]}" if token else "未登录")

    def clear_qr(self) -> None:
        self.qr_photo = None
        self.qr_label.configure(image="", text="点击“生成二维码”开始", font=("Microsoft YaHei UI", 9))

    def save_token(self) -> None:
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("提示", "请输入 token")
            return
        self.app.cfg["token"] = token
        save_config(self.app.cfg)
        self.refresh_status()
        self.notify("Token 已保存")

    def start_qr(self) -> None:
        self.qr_label.configure(image="", text="正在创建二维码…")
        self.notify("正在创建二维码")
        self.app.run_bg(self._qr_worker)

    def _show_qr(self, url: str) -> None:
        qr = qrcode.QRCode(border=2, box_size=7)
        qr.add_data(url)
        qr.make(fit=True)
        try:
            from PIL import ImageTk

            image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            self.qr_photo = ImageTk.PhotoImage(image)
            self.qr_label.configure(image=self.qr_photo, text="")
        except Exception:
            matrix = qr.get_matrix()
            lines = ["".join("  " if cell else "██" for cell in row) for row in matrix]
            self.qr_label.configure(text="\n".join(lines), font=("Consolas", 5), justify="center")

    def _qr_worker(self) -> None:
        try:
            code_id, url = api.create_login_qr()
            self.ui(lambda: self._show_qr(url))
        except Exception as exc:
            self.ui(lambda: messagebox.showerror("创建二维码失败", str(exc)))
            return

        deadline = time.time() + 120
        scanned = False
        while time.time() < deadline:
            try:
                status, token = api.check_login_status(code_id)
                if status == "2" and not scanned:
                    scanned = True
                    self.ui(lambda: self.qr_label.configure(image="", text="已扫码，等待确认…", font=("Microsoft YaHei UI", 12)))
                if status == "3" and token:
                    self.app.cfg["token"] = token
                    save_config(self.app.cfg)
                    self.ui(self.refresh_status)
                    self.ui(lambda: self.qr_label.configure(image="", text="登录成功", font=("Microsoft YaHei UI", 14)))
                    self.ui(lambda: self.notify("登录成功"))
                    return
            except Exception:
                pass
            time.sleep(3)
        self.ui(lambda: self.qr_label.configure(image="", text="登录超时，请重试", font=("Microsoft YaHei UI", 12)))


class ConfigPanel(BasePanel):
    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        ttk.Label(self, text="配置活动", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        self.orgs: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []
        self.vars = {key: tk.StringVar(value=self.app.cfg.get(key, "")) for key in ("org_id", "activity_id", "post_id")}

        picker = ttk.LabelFrame(self, text="推荐流程：组织 → 项目 → 活动 → 岗位", padding=12)
        picker.pack(fill="x", pady=(0, 10))
        self.org_var = tk.StringVar()
        self.project_var = tk.StringVar()
        self.activity_var = tk.StringVar()
        self.post_var = tk.StringVar()
        self.org_combo = self._combo_row(picker, 0, "组织", self.org_var, "获取组织", self.load_orgs)
        self.project_combo = self._combo_row(picker, 1, "项目", self.project_var, "获取项目", self.load_projects)
        self.activity_combo = self._combo_row(picker, 2, "活动", self.activity_var, "获取活动", self.load_activities)
        self.post_combo = self._combo_row(picker, 3, "岗位", self.post_var, "获取岗位", self.load_posts)
        picker.grid_columnconfigure(1, weight=1)

        self.org_combo.bind("<<ComboboxSelected>>", lambda _: self.on_org())
        self.project_combo.bind("<<ComboboxSelected>>", lambda _: self.on_project())
        self.activity_combo.bind("<<ComboboxSelected>>", lambda _: self.on_activity())
        self.post_combo.bind("<<ComboboxSelected>>", lambda _: self.on_post())

        form = ttk.LabelFrame(self, text="当前配置", padding=12)
        form.pack(fill="x")
        for row, (key, label) in enumerate((("org_id", "org_id"), ("activity_id", "activity_id"), ("post_id", "post_id"))):
            ttk.Label(form, text=label, width=16).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(form, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=5)
        form.grid_columnconfigure(1, weight=1)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="保存配置", command=self.save).pack(side="right")
        ttk.Button(row, text="刷新组织", command=self.load_orgs).pack(side="right", padx=(0, 8))
        ttk.Label(self, text="选中岗位后会自动回填三个 ID；仍保留手动输入以便应急。", foreground="#666").pack(anchor="w", pady=(10, 0))

        if self.app.cfg.get("token"):
            self.after(100, self.load_orgs)

    def _combo_row(self, parent: tk.Misc, row: int, label: str, var: tk.StringVar, button: str, command: Callable[[], None]) -> ttk.Combobox:
        ttk.Label(parent, text=label, width=10).grid(row=row, column=0, sticky="w", pady=5)
        combo = ttk.Combobox(parent, textvariable=var, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(parent, text=button, command=command).grid(row=row, column=2, padx=(8, 0), pady=5)
        return combo

    def token(self) -> str | None:
        token = self.app.cfg.get("token", "")
        if not token:
            messagebox.showerror("缺少登录", "请先登录")
            return None
        return token

    @staticmethod
    def label(item: dict[str, Any], name_key: str, id_key: str = "iid") -> str:
        return f"{item.get(name_key) or ''} | {item.get(id_key) or ''}"

    def selected(self, combo: ttk.Combobox, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        idx = combo.current()
        return items[idx] if 0 <= idx < len(items) else None

    def load_orgs(self) -> None:
        token = self.token()
        if token:
            self.notify("正在获取组织")
            self.app.run_bg(lambda: self._load_orgs(token))

    def _load_orgs(self, token: str) -> None:
        try:
            data = api.fetch_current_orgs(token)
            orgs = data["orgs"]
            default_id = data["defaultOrgId"]

            def fill() -> None:
                self.orgs = orgs
                self.org_combo.configure(values=[self.label(o, "orgName", "orgId") for o in orgs])
                if orgs:
                    idx = next((i for i, o in enumerate(orgs) if str(o.get("orgId") or "") == default_id), 0)
                    self.org_combo.current(idx)
                    self.on_org()
                    if len(orgs) == 1:
                        self.load_projects()
                self.notify(f"已获取 {len(orgs)} 个组织")

            self.ui(fill)
        except Exception as exc:
            self.ui(lambda: messagebox.showerror("获取组织失败", str(exc)))

    def on_org(self) -> None:
        org = self.selected(self.org_combo, self.orgs)
        if not org:
            return
        self.vars["org_id"].set(str(org.get("orgId") or ""))
        self.projects = []
        self.activities = []
        self.posts = []
        self.project_combo.configure(values=[])
        self.activity_combo.configure(values=[])
        self.post_combo.configure(values=[])
        self.project_var.set("")
        self.activity_var.set("")
        self.post_var.set("")

    def load_projects(self) -> None:
        token = self.token()
        org_id = self.vars["org_id"].get().strip()
        if not token:
            return
        if not org_id:
            messagebox.showwarning("缺少组织", "请先选择组织")
            return
        self.notify("正在获取项目")
        self.app.run_bg(lambda: self._load_projects(token, org_id))

    def _load_projects(self, token: str, org_id: str) -> None:
        try:
            projects = api.fetch_selectable_projects(token, org_id)

            def fill() -> None:
                self.projects = projects
                self.project_combo.configure(values=[self.label(p, "proName") for p in projects])
                if projects:
                    self.project_combo.current(0)
                    self.on_project()
                    if len(projects) == 1:
                        self.load_activities()
                self.notify(f"已获取 {len(projects)} 个项目")

            self.ui(fill)
        except Exception as exc:
            self.ui(lambda: messagebox.showerror("获取项目失败", str(exc)))

    def on_project(self) -> None:
        self.activities = []
        self.posts = []
        self.activity_combo.configure(values=[])
        self.post_combo.configure(values=[])
        self.activity_var.set("")
        self.post_var.set("")

    def load_activities(self) -> None:
        token = self.token()
        project = self.selected(self.project_combo, self.projects)
        if not token:
            return
        if not project:
            messagebox.showwarning("缺少项目", "请先选择项目")
            return
        self.notify("正在获取活动")
        self.app.run_bg(lambda: self._load_activities(token, str(project.get("iid") or "")))

    def _load_activities(self, token: str, project_id: str) -> None:
        try:
            activities = api.fetch_activities(token, project_id)

            def fill() -> None:
                self.activities = activities
                self.activity_combo.configure(values=[self.label(a, "activityName") for a in activities])
                if activities:
                    self.activity_combo.current(0)
                    self.on_activity()
                    if len(activities) == 1:
                        self.load_posts()
                self.notify(f"已获取 {len(activities)} 个活动")

            self.ui(fill)
        except Exception as exc:
            self.ui(lambda: messagebox.showerror("获取活动失败", str(exc)))

    def on_activity(self) -> None:
        activity = self.selected(self.activity_combo, self.activities)
        if activity:
            self.vars["activity_id"].set(str(activity.get("iid") or ""))
        self.posts = []
        self.post_combo.configure(values=[])
        self.post_var.set("")

    def load_posts(self) -> None:
        token = self.token()
        activity_id = self.vars["activity_id"].get().strip()
        if not token:
            return
        if not activity_id:
            messagebox.showwarning("缺少活动", "请先选择活动")
            return
        self.notify("正在获取岗位")
        self.app.run_bg(lambda: self._load_posts(token, activity_id))

    def _load_posts(self, token: str, activity_id: str) -> None:
        try:
            posts = api.fetch_posts(token, activity_id)

            def fill() -> None:
                self.posts = posts
                self.post_combo.configure(values=[self.label(p, "postName") for p in posts])
                if posts:
                    self.post_combo.current(0)
                    self.on_post()
                self.notify(f"已获取 {len(posts)} 个岗位")

            self.ui(fill)
        except Exception as exc:
            self.ui(lambda: messagebox.showerror("获取岗位失败", str(exc)))

    def on_post(self) -> None:
        post = self.selected(self.post_combo, self.posts)
        if post:
            self.vars["post_id"].set(str(post.get("iid") or ""))

    def save(self) -> None:
        for key, var in self.vars.items():
            self.app.cfg[key] = var.get().strip()
        save_config(self.app.cfg)
        self.notify("配置已保存")
        messagebox.showinfo("完成", "配置已保存")


class RosterPanel(BasePanel):
    COLUMNS = ("序号", "脱敏姓名", "uid", "userNumber", "审核时间")

    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        ttk.Label(self, text="岗位名单", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Button(self, text="刷新名单", command=self.refresh).pack(anchor="w", pady=(0, 8))
        self.table = TreeFrame(self, self.COLUMNS, height=18)
        self.table.pack(fill="both", expand=True)

    def refresh(self) -> None:
        cfg = self.app.cfg
        if not cfg.get("token") or not cfg.get("activity_id") or not cfg.get("post_id"):
            messagebox.showerror("缺少配置", "请先登录并配置活动和岗位")
            return
        self.notify("正在加载名单")
        self.app.run_bg(self._load)

    def _load(self) -> None:
        cfg = self.app.cfg
        try:
            roster = api.fetch_roster(cfg["token"], cfg["activity_id"], cfg["post_id"])

            def fill() -> None:
                self.table.clear()
                for i, user in enumerate(roster, 1):
                    self.table.add_row((i, user.get("nameSensitive", ""), user.get("uid", ""), user.get("userNumber", ""), user.get("approvedTime", "")))
                self.notify(f"已加载 {len(roster)} 人")

            self.ui(fill)
        except Exception as exc:
            self.ui(lambda: messagebox.showerror("加载失败", str(exc)))


class ImportPanel(BasePanel):
    COLUMNS = ("姓名", "时数", "证件号", "组织搜索", "入岗", "时数录入", "状态")

    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        ttk.Label(self, text="Excel 批量导入", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        self.path_var = tk.StringVar()
        file_box = ttk.LabelFrame(self, text="文件", padding=10)
        file_box.pack(fill="x", pady=(0, 8))
        ttk.Entry(file_box, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(file_box, text="浏览", command=self.browse).pack(side="left", padx=(8, 0))
        ttk.Button(file_box, text="加载", command=self.load_file).pack(side="left", padx=(8, 0))

        options = ttk.LabelFrame(self, text="录入参数", padding=10)
        options.pack(fill="x", pady=(0, 8))
        self.start_var = tk.StringVar(value=date.today().isoformat())
        self.max_var = tk.StringVar(value="8")
        ttk.Label(options, text="起始日期").pack(side="left")
        ttk.Entry(options, textvariable=self.start_var, width=14).pack(side="left", padx=(6, 16))
        ttk.Label(options, text="每日最大小时").pack(side="left")
        ttk.Entry(options, textvariable=self.max_var, width=8).pack(side="left", padx=(6, 0))

        self.table = TreeFrame(self, self.COLUMNS, height=12)
        self.table.pack(fill="both", expand=True, pady=(0, 8))
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="仅预览", command=self.preview).pack(side="left")
        ttk.Button(actions, text="开始全流程", command=self.run_pipeline).pack(side="left", padx=(8, 0))
        self.log = ScrolledText(self, height=10)
        self.log.pack(fill="both", expand=True)

    def browse(self) -> None:
        path = filedialog.askopenfilename(title="选择 Excel 文件", filetypes=[("Excel files", "*.xls *.xlsx"), ("All files", "*.*")])
        if path:
            self.path_var.set(path)

    def load_file(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("提示", "请选择 Excel 文件")
            return
        self.notify("正在读取 Excel")
        self.app.run_bg(lambda: self._load_file(path))

    def _load_file(self, path: str) -> None:
        try:
            rows = read_excel(path)
            self.app.import_rows = rows

            def fill() -> None:
                self.table.clear()
                for i, row in enumerate(rows):
                    cert = "有" if row.cert_no else "无"
                    self.table.add_row((row.name, row.hours, cert, "-", "-", "-", "待处理"), iid=str(i))
                self.log.clear()
                self.log.write_line(f"已加载 {len(rows)} 行：{Path(path).name}")
                self.notify(f"已加载 {len(rows)} 行")

            self.ui(fill)
        except Exception as exc:
            self.ui(lambda: messagebox.showerror("读取失败", str(exc)))

    def options(self) -> tuple[date, float]:
        start = date.fromisoformat(self.start_var.get().strip())
        max_hours = float(self.max_var.get().strip() or "8")
        if max_hours <= 0:
            raise ValueError("每日最大小时必须大于 0")
        return start, max_hours

    def preview(self) -> None:
        if not self.app.import_rows:
            messagebox.showwarning("提示", "请先加载 Excel")
            return
        try:
            start, max_hours = self.options()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        self.log.clear()
        for row in self.app.import_rows:
            plan = " | ".join(f"{day}={hours:g}h" for day, hours in allocate(row.hours, start, max_hours))
            cert = "有身份证/证件号" if row.cert_no else "无身份证/证件号"
            self.log.write_line(f"{row.name} {row.hours:g}h -> {plan} ({cert})")
        self.notify("预览完成")

    def run_pipeline(self) -> None:
        cfg = self.app.cfg
        if not self.app.import_rows:
            messagebox.showwarning("提示", "请先加载 Excel")
            return
        if not all(cfg.get(key) for key in ("token", "activity_id", "post_id", "org_id")):
            messagebox.showerror("缺少配置", "请先登录并配置活动、岗位和组织")
            return
        try:
            start, max_hours = self.options()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        if not messagebox.askyesno("确认", f"将对 {len(self.app.import_rows)} 行执行全流程，是否继续？"):
            return
        self.notify("全流程执行中")
        self.app.run_bg(lambda: self._pipeline(self.app.import_rows, start, max_hours))

    def set_cell(self, row: int, column: str, value: str) -> None:
        self.table.update_cell(str(row), column, value)

    def _pipeline(self, rows: list[ImportRow], start: date, max_hours: float) -> None:
        cfg = self.app.cfg
        token = cfg["token"]

        def log(message: str) -> None:
            self.ui(lambda: self.log.write_line(message))

        def set_cell(row: int, column: str, value: str) -> None:
            self.ui(lambda: self.set_cell(row, column, value))

        try:
            pk = api.get_public_key(token)
        except Exception as exc:
            log(f"获取加密公钥失败：{exc}")
            return

        try:
            roster = api.fetch_roster(token, cfg["activity_id"], cfg["post_id"])
            log(f"roster loaded: {len(roster)}")
        except Exception as exc:
            roster = []
            log(f"roster load failed; falling back to org search only: {exc}")

        uid_by_index: dict[int, str] = {}
        roster_uid_indexes: set[int] = set()
        for i, row in enumerate(rows):
            set_cell(i, "组织搜索", "搜索中")
            try:
                users = api.search_org_user(token, row.name, pk, cfg["activity_id"], cfg["post_id"], cfg["org_id"])
                if not users and row.cert_no:
                    set_cell(i, "组织搜索", "尝试加入组织")
                    ok, message = api.add_org_member(token, row.name, row.cert_no, pk)
                    log(f"{row.name} addMember：{message}")
                    if ok:
                        users = api.search_org_user(token, row.name, pk, cfg["activity_id"], cfg["post_id"], cfg["org_id"])
                if users:
                    uid_by_index[i] = str(users[0]["uid"])
                    set_cell(i, "组织搜索", "完成")
                    set_cell(i, "状态", f"uid={users[0]['uid']}")
                elif roster:
                    roster_user, reason = match_roster_user(row, roster)
                    if roster_user:
                        uid_by_index[i] = str(roster_user["uid"])
                        roster_uid_indexes.add(i)
                        set_cell(i, "组织搜索", "岗位名单")
                        set_cell(i, "状态", f"uid={roster_user['uid']} (岗位名单)")
                    else:
                        set_cell(i, "组织搜索", "未找到")
                        set_cell(i, "状态", f"未找到，跳过 ({reason})")
                else:
                    set_cell(i, "组织搜索", "未找到")
                    set_cell(i, "状态", "未找到，跳过")
            except Exception as exc:
                set_cell(i, "组织搜索", "失败")
                set_cell(i, "状态", str(exc))

        valid = sorted(uid_by_index)
        if not valid:
            log("没有可处理人员")
            return

        for i in sorted(roster_uid_indexes):
            set_cell(i, "入岗", "已在岗")

        add_indexes = [i for i in valid if i not in roster_uid_indexes]
        if add_indexes:
            try:
                api.add_post_members(token, cfg["activity_id"], cfg["post_id"], cfg["org_id"], [uid_by_index[i] for i in add_indexes])
                for i in add_indexes:
                    set_cell(i, "入岗", "完成")
                log(f"已加入岗位：{len(add_indexes)} 人")
            except Exception as exc:
                for i in add_indexes:
                    set_cell(i, "入岗", "失败")
                log(f"加入岗位失败，跳过这些人员：{exc}")
                valid = sorted(roster_uid_indexes)
                if not valid:
                    return
        elif roster_uid_indexes:
            log(f"岗位名单已在岗：{len(roster_uid_indexes)} 人")

        for i in valid:
            row = rows[i]
            set_cell(i, "时数录入", "录入中")
            try:
                file_path = upload_proof(token)
                plan = allocate(row.hours, start, max_hours)
                biz = {
                    "activityId": cfg["activity_id"],
                    "postId": cfg["post_id"],
                    "orgId": cfg["org_id"],
                    "notes": "",
                    "uids": [uid_by_index[i]],
                    "times": [{"time": day, "hour": hours} for day, hours in plan],
                    "filePath": file_path,
                }
                unwrap(call("activityTiming-batchAdd", biz, access_token=token))
                set_cell(i, "时数录入", "完成")
                set_cell(i, "状态", "完成")
                log(f"{row.name} {row.hours:g}h 录入完成")
            except Exception as exc:
                set_cell(i, "时数录入", "失败")
                set_cell(i, "状态", str(exc))
        self.ui(lambda: self.notify("全流程完成"))


class HelpPanel:
    @staticmethod
    def show(parent: tk.Misc) -> None:
        win = tk.Toplevel(parent)
        win.title("帮助与关于")
        win.geometry("780x660")
        win.minsize(640, 480)
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="bv2008 志愿者管理工具", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="由中国人民大学商学院青年志愿者协会开发",
            font=("Microsoft YaHei UI", 10, "bold"),
            foreground="#8A1F1F",
        ).pack(anchor="w", pady=(4, 12))
        text = ScrolledText(outer, height=28)
        text.pack(fill="both", expand=True)
        text.write_line(
            """一、推荐流程

1. 登录页扫码登录或手动保存 token。
2. 配置活动页依次获取组织、项目、活动、岗位，选中岗位后保存配置。
3. 查看名单页确认岗位名单是否正确。
4. Excel 批量导入页先预览，再开始全流程。

二、Excel 文件要求

表头至少包含：学生姓名、认定时数。
推荐包含：身份证号。这样未加入组织的成员可由程序尝试 addMember。

"""
            + recommended_template_text()
            + """
三、重要注意事项

1. 录入服务时数前必须先加入岗位。本工具会先 addList，再 batchAdd。
2. accessToken 会过期；遇到未登录或权限异常时请重新扫码。
3. 项目列表默认只展示可直接查询活动的项目，隐藏容易误选的一级项目。
4. 证明图片每个人都会重新上传，避免 filePath 复用失败。

四、开发与维护

开发单位：中国人民大学商学院青年志愿者协会
适用场景：中国人民大学商学院青年志愿者协会内部志愿服务项目管理
"""
        )
        ttk.Button(outer, text="关闭", command=win.destroy).pack(anchor="e", pady=(12, 0))


class BVGuiApp(tk.Tk):
    NAV_ITEMS = (
        ("login", "登录"),
        ("config", "配置活动"),
        ("import", "Excel 批量导入"),
        ("roster", "查看名单"),
    )
    PANELS: dict[str, type[BasePanel]] = {
        "login": LoginPanel,
        "config": ConfigPanel,
        "import": ImportPanel,
        "roster": RosterPanel,
    }

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.import_rows: list[ImportRow] = []
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_panel: BasePanel | None = None
        self.title("bv2008 志愿者管理工具")
        self.geometry("1120x720")
        self.minsize(960, 600)
        self.setup_style()
        self.build_menu()
        self.build_layout()
        self.show_panel("login")
        self.after(80, self.process_ui)

    def setup_style(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "xpnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        self.option_add("*Font", ("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Nav.TButton", anchor="w", padding=(12, 8))
        style.configure("Active.Nav.TButton", anchor="w", padding=(12, 8))
        style.configure("Treeview", rowheight=26)

    def build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="退出", command=self.destroy, accelerator="Ctrl+Q")
        menu.add_cascade(label="文件", menu=file_menu)
        view_menu = tk.Menu(menu, tearoff=False)
        for key, label in self.NAV_ITEMS:
            view_menu.add_command(label=label, command=lambda k=key: self.show_panel(k))
        menu.add_cascade(label="视图", menu=view_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="帮助与关于", command=lambda: HelpPanel.show(self))
        menu.add_cascade(label="帮助", menu=help_menu)
        self.configure(menu=menu)
        self.bind_all("<Control-q>", lambda _event: self.destroy())

    def build_layout(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)
        toolbar = ttk.Frame(root, padding=(8, 6))
        toolbar.pack(side="top", fill="x")
        ttk.Label(toolbar, text="bv2008 · 中国人民大学商学院青年志愿者协会", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        ttk.Separator(root, orient="horizontal").pack(side="top", fill="x")
        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(side="top", fill="both", expand=True)
        sidebar = ttk.Frame(body, padding=(10, 12))
        body.add(sidebar, weight=0)
        ttk.Label(sidebar, text="bv2008", style="Title.TLabel").pack(anchor="w", pady=(0, 16))
        for key, label in self.NAV_ITEMS:
            button = ttk.Button(sidebar, text=label, style="Nav.TButton", command=lambda k=key: self.show_panel(k))
            button.pack(fill="x", pady=3)
            self.nav_buttons[key] = button
        self.content = ttk.Frame(body)
        body.add(self.content, weight=1)
        self.status = StatusBar(root)
        self.status.pack(side="bottom", fill="x")

    def show_panel(self, name: str) -> None:
        panel_class = self.PANELS[name]
        for child in self.content.winfo_children():
            child.destroy()
        self.current_panel = panel_class(self.content, self)
        self.current_panel.pack(fill="both", expand=True)
        for key, button in self.nav_buttons.items():
            button.configure(style="Active.Nav.TButton" if key == name else "Nav.TButton")
        self.status.set(dict(self.NAV_ITEMS).get(name, "就绪"))

    def ui(self, func: Callable[[], None]) -> None:
        self.ui_queue.put(func)

    def process_ui(self) -> None:
        try:
            while True:
                func = self.ui_queue.get_nowait()
                try:
                    func()
                except tk.TclError as exc:
                    if "invalid command name" in str(exc):
                        continue
                    messagebox.showerror("UI update failed", str(exc))
                except Exception as exc:
                    messagebox.showerror("UI update failed", str(exc))
        except queue.Empty:
            pass
        self.after(80, self.process_ui)

    def run_bg(self, target: Callable[[], None]) -> None:
        threading.Thread(target=target, daemon=True).start()


def run() -> None:
    BVGuiApp().mainloop()

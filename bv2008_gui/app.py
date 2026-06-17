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

PALETTE = {
    "bg": "#F3F6FB",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "line": "#DDE5EF",
    "text": "#18202F",
    "muted": "#607086",
    "accent": "#B91C1C",
    "accent_dark": "#8A1F1F",
    "accent_soft": "#FFF1F2",
    "sidebar": "#142033",
    "sidebar_alt": "#1E2A3E",
    "sidebar_text": "#E6EDF7",
    "sidebar_muted": "#9AA8BC",
    "success": "#047857",
    "success_soft": "#DFF7EA",
    "warning": "#B45309",
    "warning_soft": "#FEF3C7",
    "busy": "#2563EB",
    "ready": "#94A3B8",
    "error": "#DC2626",
    "log_bg": "#111827",
    "log_text": "#E5E7EB",
}

FONT_UI = ("Microsoft YaHei UI", 9)
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SECTION = ("Microsoft YaHei UI", 11, "bold")
FONT_HEADER = ("Microsoft YaHei UI", 13, "bold")

NAV_INDEX = {"login": "01", "config": "02", "import": "03", "roster": "04"}


def enable_high_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            awareness_context = ctypes.c_void_p(-4)  # Per-monitor v2 on Windows 10+
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(awareness_context):
                return
        except Exception:
            pass
        try:
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


def app_px(widget: tk.Misc, value: int | float) -> int:
    top = widget.winfo_toplevel()
    scale = float(getattr(top, "ui_scale", 1.0))
    return max(1, int(round(float(value) * scale)))


def setup_dialog_window(win: tk.Toplevel, parent: tk.Misc, width: int, height: int, min_width: int, min_height: int) -> None:
    px = lambda value: app_px(parent, value)
    screen_w = parent.winfo_screenwidth()
    screen_h = parent.winfo_screenheight()
    dialog_w = min(px(width), max(px(min_width), screen_w - px(80)))
    dialog_h = min(px(height), max(px(min_height), screen_h - px(100)))
    win.geometry(f"{dialog_w}x{dialog_h}")
    win.minsize(min(px(min_width), dialog_w), min(px(min_height), dialog_h))
    try:
        parent.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - dialog_w) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - dialog_h) // 2)
        win.geometry(f"{dialog_w}x{dialog_h}+{x}+{y}")
    except Exception:
        pass


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


def is_auth_error(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    markers = (
        "access token",
        "accesstoken",
        "未登录",
        "重新登录",
        "登录超时",
        "登录已过期",
        "登录过期",
        "token expired",
        "token invalid",
        "token过期",
        "token 过期",
        "token失效",
        "token 失效",
        "请登录",
        "请先登录",
        "invalid token",
        "expired",
        "unauthorized",
        "401",
    )
    return any(marker in text for marker in markers)


class BasePanel(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, style="Content.TFrame", padding=(app.px(18), app.px(12)))
        self.app = app

    def page_header(self, title: str, subtitle: str = "") -> None:
        header = ttk.Frame(self, style="Content.TFrame")
        header.pack(fill="x", pady=(0, self.app.px(10)))
        ttk.Label(header, text=title, style="PageTitle.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(header, text=subtitle, style="Muted.TLabel", wraplength=self.app.px(820)).pack(anchor="w", pady=(self.app.px(4), 0))

    def section(self, title: str, subtitle: str = "") -> ttk.Frame:
        outer = ttk.Frame(self, style="Card.TFrame", padding=(self.app.px(16), self.app.px(14)))
        outer.pack(fill="x", pady=(0, self.app.px(12)))
        ttk.Label(outer, text=title, style="SectionTitle.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(outer, text=subtitle, style="CardMuted.TLabel", wraplength=self.app.px(780)).pack(anchor="w", pady=(self.app.px(5), 0))
        else:
            ttk.Frame(outer, height=1, style="CardBody.TFrame").pack()
        ttk.Frame(outer, height=1, style="Divider.TFrame").pack(fill="x", pady=(self.app.px(10), self.app.px(10)))
        content = ttk.Frame(outer, style="CardBody.TFrame")
        content.pack(fill="x")
        return content

    def ui(self, func: Callable[[], None]) -> None:
        def guarded() -> None:
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            func()

        self.app.ui(guarded)

    def notify(self, text: str, state: str | None = None) -> None:
        self.app.status.set(text, state)


class LoginPanel(BasePanel):
    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        self.qr_photo: Any = None
        self.token_var = tk.StringVar()

        self.page_header("登录", "扫码或粘贴 token 后即可开始配置活动。")
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var, style="Badge.TLabel").pack(anchor="w", pady=(0, self.app.px(14)))

        qr_box = self.section("扫码登录", "二维码有效时间约 2 分钟。手机确认后 token 会自动保存。")
        row = ttk.Frame(qr_box, style="CardBody.TFrame")
        row.pack(fill="x", pady=(0, self.app.px(10)))
        ttk.Button(row, text="生成二维码", style="Primary.TButton", command=self.start_qr).pack(side="left")
        ttk.Button(row, text="清空", style="Secondary.TButton", command=self.clear_qr).pack(side="left", padx=(self.app.px(8), 0))

        qr_area = ttk.Frame(qr_box, style="QrWell.TFrame", height=self.app.px(300), padding=self.app.px(12))
        qr_area.pack(fill="x")
        qr_area.pack_propagate(False)
        self.qr_label = ttk.Label(qr_area, text="点击“生成二维码”开始", anchor="center", justify="center", style="Qr.TLabel")
        self.qr_label.pack(fill="both", expand=True)

        manual = self.section("手动 Token", "用于扫码不方便或需要复用已有登录态的情况。")
        ttk.Entry(manual, textvariable=self.token_var).pack(side="left", fill="x", expand=True)
        ttk.Button(manual, text="保存 Token", style="Primary.TButton", command=self.save_token).pack(side="left", padx=(self.app.px(8), 0))
        self.refresh_status()

    def refresh_status(self) -> None:
        token = self.app.cfg.get("token", "")
        self.status_var.set(f"已登录  token=...{token[-16:]}" if token else "未登录")
        style = "Success.Badge.TLabel" if token else "Warning.Badge.TLabel"
        for child in self.winfo_children():
            if isinstance(child, ttk.Label) and str(child.cget("textvariable")) == str(self.status_var):
                child.configure(style=style)
                break

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
        self.notify("Token 已保存，正在校验登录状态…", "busy")
        self.app.warm_cache()
        self.app.complete_tutorial_step("login")

    def start_qr(self) -> None:
        self.qr_label.configure(image="", text="正在创建二维码…")
        self.notify("正在创建二维码", "busy")
        self.app.run_bg(self._qr_worker)

    def _show_qr(self, url: str) -> None:
        qr = qrcode.QRCode(border=2, box_size=7)
        qr.add_data(url)
        qr.make(fit=True)
        try:
            from PIL import ImageTk

            image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            image.thumbnail((self.app.px(300), self.app.px(300)))
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
            message = str(exc)
            self.ui(lambda message=message: messagebox.showerror("创建二维码失败", message))
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
                    self.ui(lambda: self.notify("登录成功", "success"))
                    self.ui(self.app.warm_cache)
                    self.ui(lambda: self.app.complete_tutorial_step("login"))
                    return
            except Exception:
                pass
            time.sleep(3)
        self.ui(lambda: self.qr_label.configure(image="", text="登录超时，请重试", font=("Microsoft YaHei UI", 12)))


class ConfigPanel(BasePanel):
    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        self.page_header("配置活动", "按组织、项目、活动、岗位的顺序选择，最后保存配置。")
        self.orgs: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []
        self.loading: set[str] = set()
        self.vars = {key: tk.StringVar(value=self.app.cfg.get(key, "")) for key in ("org_id", "activity_id", "post_id")}

        picker = self.section("选择链路", "登录后会自动从组织一路加载到岗位；也可以随时手动刷新某一级。")
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

        form = self.section("当前 ID", "保留手动输入，用于接口异常或临时修正。")
        for row, (key, label) in enumerate((("org_id", "org_id"), ("activity_id", "activity_id"), ("post_id", "post_id"))):
            ttk.Label(form, text=label, style="FieldLabel.TLabel", width=16).grid(row=row, column=0, sticky="w", pady=self.app.px(6))
            ttk.Entry(form, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=self.app.px(6))
        form.grid_columnconfigure(1, weight=1)

        row = ttk.Frame(self, style="Content.TFrame")
        row.pack(fill="x", pady=(self.app.px(6), 0))
        ttk.Button(row, text="保存配置", style="Primary.TButton", command=self.save).pack(side="right")
        ttk.Button(row, text="刷新组织", style="Secondary.TButton", command=self.load_orgs).pack(side="right", padx=(0, 8))

        if self.app.cfg.get("token"):
            self.after(120, lambda: self.load_orgs(auto=True))

    def _combo_row(self, parent: tk.Misc, row: int, label: str, var: tk.StringVar, button: str, command: Callable[[], None]) -> ttk.Combobox:
        ttk.Label(parent, text=label, style="FieldLabel.TLabel", width=10).grid(row=row, column=0, sticky="w", pady=self.app.px(6))
        combo = ttk.Combobox(parent, textvariable=var, state="readonly", style="App.TCombobox")
        combo.configure(foreground=PALETTE["text"])
        combo.grid(row=row, column=1, sticky="ew", pady=self.app.px(6))
        ttk.Button(parent, text=button, style="Secondary.TButton", command=command).grid(row=row, column=2, padx=(self.app.px(10), 0), pady=self.app.px(6))
        return combo

    def token(self, show_error: bool = True) -> str | None:
        token = self.app.cfg.get("token", "")
        if not token and show_error:
            self.app.status.set("未登录，请先登录", "error")
            messagebox.showerror("缺少登录", "请先登录")
            return None
        return token

    @staticmethod
    def label(item: dict[str, Any], name_key: str, id_key: str = "iid") -> str:
        return f"{item.get(name_key) or ''} | {item.get(id_key) or ''}"

    def selected(self, combo: ttk.Combobox, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        idx = combo.current()
        return items[idx] if 0 <= idx < len(items) else None

    def cached(self, key: str, subkey: str | None = None) -> Any:
        with self.app.cache_lock:
            value = self.app.cache[key] if subkey is None else self.app.cache[key].get(subkey)
        return value

    def set_loading(self, key: str, is_loading: bool) -> None:
        if is_loading:
            self.loading.add(key)
        else:
            self.loading.discard(key)

    def fill_orgs(self, orgs: list[dict[str, Any]], default_id: str = "") -> None:
        self.orgs = orgs
        self.org_combo.configure(values=[self.label(o, "orgName", "orgId") for o in orgs])
        if orgs:
            idx = next((i for i, o in enumerate(orgs) if str(o.get("orgId") or "") == default_id), 0)
            self.org_combo.current(idx)
            self.on_org()
        self.notify(f"已载入 {len(orgs)} 个组织", "success")

    def fill_projects(self, projects: list[dict[str, Any]]) -> None:
        self.projects = projects
        self.project_combo.configure(values=[self.label(p, "proName") for p in projects])
        if projects:
            self.project_combo.current(0)
            self.on_project()
        self.notify(f"已载入 {len(projects)} 个项目", "success")

    def fill_activities(self, activities: list[dict[str, Any]]) -> None:
        self.activities = activities
        self.activity_combo.configure(values=[self.label(a, "activityName") for a in activities])
        if activities:
            self.activity_combo.current(0)
            self.on_activity()
        self.notify(f"已载入 {len(activities)} 个活动", "success")

    def fill_posts(self, posts: list[dict[str, Any]]) -> None:
        self.posts = posts
        self.post_combo.configure(values=[self.label(p, "postName") for p in posts])
        if posts:
            self.post_combo.current(0)
            self.on_post()
        self.notify(f"已载入 {len(posts)} 个岗位", "success")

    def load_orgs(self, auto: bool = False) -> None:
        token = self.token(show_error=not auto)
        if token:
            orgs = self.cached("orgs")
            if orgs is not None:
                self.fill_orgs(orgs, str(self.cached("default_org_id") or ""))
                return
            if "orgs" in self.loading:
                return
            self.set_loading("orgs", True)
            self.notify("正在获取组织", "busy")
            self.app.run_bg(lambda: self._load_orgs(token, auto=auto))

    def _load_orgs(self, token: str, auto: bool = False) -> None:
        try:
            data = api.fetch_current_orgs(token)
            orgs = data["orgs"]
            default_id = data["defaultOrgId"]

            with self.app.cache_lock:
                self.app.cache["orgs"] = orgs
                self.app.cache["default_org_id"] = default_id

            def fill() -> None:
                self.set_loading("orgs", False)
                self.fill_orgs(orgs, default_id)

            self.ui(fill)
        except Exception as exc:
            message = str(exc)
            self.ui(lambda: self.set_loading("orgs", False))
            if is_auth_error(exc):
                self.ui(lambda exc=exc: self.app.handle_auth_error(exc))
                return
            if auto:
                self.ui(lambda message=message: self.notify(f"自动获取组织失败：{message}", "warning"))
            else:
                self.ui(lambda message=message: messagebox.showerror("获取组织失败", message))

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
        cached_projects = self.cached("projects_by_org", self.vars["org_id"].get().strip())
        if cached_projects is not None:
            self.fill_projects(cached_projects)
        else:
            self.after(80, lambda: self.load_projects(auto=True))

    def load_projects(self, auto: bool = False) -> None:
        token = self.token(show_error=not auto)
        org_id = self.vars["org_id"].get().strip()
        if not token:
            return
        if not org_id:
            if not auto:
                messagebox.showwarning("缺少组织", "请先选择组织")
            return
        cached_projects = self.cached("projects_by_org", org_id)
        if cached_projects is not None:
            self.fill_projects(cached_projects)
            return
        loading_key = f"projects:{org_id}"
        if loading_key in self.loading:
            return
        self.set_loading(loading_key, True)
        self.notify("正在获取项目", "busy")
        self.app.run_bg(lambda: self._load_projects(token, org_id, loading_key, auto=auto))

    def _load_projects(self, token: str, org_id: str, loading_key: str, auto: bool = False) -> None:
        try:
            projects = api.fetch_selectable_projects(token, org_id)
            with self.app.cache_lock:
                self.app.cache["projects_by_org"][org_id] = projects

            def fill() -> None:
                self.set_loading(loading_key, False)
                if self.vars["org_id"].get().strip() != org_id:
                    return
                self.fill_projects(projects)

            self.ui(fill)
        except Exception as exc:
            message = str(exc)
            self.ui(lambda: self.set_loading(loading_key, False))
            if is_auth_error(exc):
                self.ui(lambda exc=exc: self.app.handle_auth_error(exc))
                return
            if auto:
                self.ui(lambda message=message: self.notify(f"自动获取项目失败：{message}", "warning"))
            else:
                self.ui(lambda message=message: messagebox.showerror("获取项目失败", message))

    def on_project(self) -> None:
        self.activities = []
        self.posts = []
        self.activity_combo.configure(values=[])
        self.post_combo.configure(values=[])
        self.activity_var.set("")
        self.post_var.set("")
        project = self.selected(self.project_combo, self.projects)
        if project:
            cached_activities = self.cached("activities_by_project", str(project.get("iid") or ""))
            if cached_activities is not None:
                self.fill_activities(cached_activities)
            else:
                self.after(80, lambda: self.load_activities(auto=True))

    def load_activities(self, auto: bool = False) -> None:
        token = self.token(show_error=not auto)
        project = self.selected(self.project_combo, self.projects)
        if not token:
            return
        if not project:
            if not auto:
                messagebox.showwarning("缺少项目", "请先选择项目")
            return
        project_id = str(project.get("iid") or "")
        cached_activities = self.cached("activities_by_project", project_id)
        if cached_activities is not None:
            self.fill_activities(cached_activities)
            return
        loading_key = f"activities:{project_id}"
        if loading_key in self.loading:
            return
        self.set_loading(loading_key, True)
        self.notify("正在获取活动", "busy")
        self.app.run_bg(lambda: self._load_activities(token, project_id, loading_key, auto=auto))

    def _load_activities(self, token: str, project_id: str, loading_key: str, auto: bool = False) -> None:
        try:
            activities = api.fetch_activities(token, project_id)
            with self.app.cache_lock:
                self.app.cache["activities_by_project"][project_id] = activities

            def fill() -> None:
                self.set_loading(loading_key, False)
                project = self.selected(self.project_combo, self.projects)
                if str((project or {}).get("iid") or "") != project_id:
                    return
                self.fill_activities(activities)

            self.ui(fill)
        except Exception as exc:
            message = str(exc)
            self.ui(lambda: self.set_loading(loading_key, False))
            if is_auth_error(exc):
                self.ui(lambda exc=exc: self.app.handle_auth_error(exc))
                return
            if auto:
                self.ui(lambda message=message: self.notify(f"自动获取活动失败：{message}", "warning"))
            else:
                self.ui(lambda message=message: messagebox.showerror("获取活动失败", message))

    def on_activity(self) -> None:
        activity = self.selected(self.activity_combo, self.activities)
        if activity:
            self.vars["activity_id"].set(str(activity.get("iid") or ""))
        self.posts = []
        self.post_combo.configure(values=[])
        self.post_var.set("")
        if activity:
            cached_posts = self.cached("posts_by_activity", str(activity.get("iid") or ""))
            if cached_posts is not None:
                self.fill_posts(cached_posts)
            else:
                self.after(80, lambda: self.load_posts(auto=True))

    def load_posts(self, auto: bool = False) -> None:
        token = self.token(show_error=not auto)
        activity_id = self.vars["activity_id"].get().strip()
        if not token:
            return
        if not activity_id:
            if not auto:
                messagebox.showwarning("缺少活动", "请先选择活动")
            return
        cached_posts = self.cached("posts_by_activity", activity_id)
        if cached_posts is not None:
            self.fill_posts(cached_posts)
            return
        loading_key = f"posts:{activity_id}"
        if loading_key in self.loading:
            return
        self.set_loading(loading_key, True)
        self.notify("正在获取岗位", "busy")
        self.app.run_bg(lambda: self._load_posts(token, activity_id, loading_key, auto=auto))

    def _load_posts(self, token: str, activity_id: str, loading_key: str, auto: bool = False) -> None:
        try:
            posts = api.fetch_posts(token, activity_id)
            with self.app.cache_lock:
                self.app.cache["posts_by_activity"][activity_id] = posts

            def fill() -> None:
                self.set_loading(loading_key, False)
                if self.vars["activity_id"].get().strip() != activity_id:
                    return
                self.fill_posts(posts)

            self.ui(fill)
        except Exception as exc:
            message = str(exc)
            self.ui(lambda: self.set_loading(loading_key, False))
            if is_auth_error(exc):
                self.ui(lambda exc=exc: self.app.handle_auth_error(exc))
                return
            if auto:
                self.ui(lambda message=message: self.notify(f"自动获取岗位失败：{message}", "warning"))
            else:
                self.ui(lambda message=message: messagebox.showerror("获取岗位失败", message))

    def on_post(self) -> None:
        post = self.selected(self.post_combo, self.posts)
        if post:
            self.vars["post_id"].set(str(post.get("iid") or ""))

    def save(self) -> None:
        for key, var in self.vars.items():
            self.app.cfg[key] = var.get().strip()
        save_config(self.app.cfg)
        self.notify("配置已保存", "success")
        self.app.complete_tutorial_step("config")
        messagebox.showinfo("完成", "配置已保存")


class RosterPanel(BasePanel):
    COLUMNS = ("序号", "脱敏姓名", "uid", "userNumber", "审核时间")

    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        self.page_header("岗位名单", "用于核对当前岗位成员，也为批量导入提供已在岗人员兜底。")
        actions = ttk.Frame(self, style="Content.TFrame")
        actions.pack(fill="x", pady=(0, self.app.px(10)))
        ttk.Button(actions, text="刷新名单", style="Primary.TButton", command=self.refresh).pack(side="left")
        self.table = TreeFrame(self, self.COLUMNS, height=14)
        self.table.pack(fill="both", expand=True)

    def refresh(self) -> None:
        cfg = self.app.cfg
        if not cfg.get("token") or not cfg.get("activity_id") or not cfg.get("post_id"):
            messagebox.showerror("缺少配置", "请先登录并配置活动和岗位")
            return
        self.notify("正在加载名单", "busy")
        self.app.run_bg(self._load)

    def _load(self) -> None:
        cfg = self.app.cfg
        try:
            roster = api.fetch_roster(cfg["token"], cfg["activity_id"], cfg["post_id"])

            def fill() -> None:
                self.table.clear()
                for i, user in enumerate(roster, 1):
                    self.table.add_row((i, user.get("nameSensitive", ""), user.get("uid", ""), user.get("userNumber", ""), user.get("approvedTime", "")))
                self.notify(f"已加载 {len(roster)} 人", "success")
                self.app.complete_tutorial_step("roster")

            self.ui(fill)
        except Exception as exc:
            message = str(exc)
            if is_auth_error(exc):
                self.ui(lambda exc=exc: self.app.handle_auth_error(exc))
                return
            self.ui(lambda message=message: messagebox.showerror("加载失败", message))


class ImportPanel(BasePanel):
    COLUMNS = ("姓名", "时数", "证件号", "组织搜索", "入岗", "时数录入", "状态")

    def __init__(self, master: tk.Misc, app: "BVGuiApp") -> None:
        super().__init__(master, app)
        self.page_header("Excel 批量导入", "加载表格后先预览分配计划，再执行入岗和时数录入。")
        self.path_var = tk.StringVar()
        file_box = self.section("1. 选择文件", "支持 .xls 和 .xlsx；表头至少包含姓名和时数。")
        ttk.Entry(file_box, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(file_box, text="浏览", style="Secondary.TButton", command=self.browse).pack(side="left", padx=(self.app.px(8), 0))
        ttk.Button(file_box, text="加载", style="Primary.TButton", command=self.load_file).pack(side="left", padx=(self.app.px(8), 0))

        options = self.section("2. 录入参数", "时数超过每日上限时，会自动顺延到后续日期。")
        self.start_var = tk.StringVar(value=date.today().isoformat())
        self.max_var = tk.StringVar(value="8")
        ttk.Label(options, text="起始日期", style="FieldLabel.TLabel").pack(side="left")
        ttk.Entry(options, textvariable=self.start_var, width=14).pack(side="left", padx=(self.app.px(6), self.app.px(16)))
        ttk.Label(options, text="每日最大小时", style="FieldLabel.TLabel").pack(side="left")
        ttk.Entry(options, textvariable=self.max_var, width=8).pack(side="left", padx=(self.app.px(6), 0))

        actions = ttk.Frame(self, style="Content.TFrame")
        actions.pack(side="bottom", fill="x", pady=(self.app.px(8), 0))
        ttk.Button(actions, text="仅预览", style="Secondary.TButton", command=self.preview).pack(side="left")
        ttk.Button(actions, text="开始全流程", style="Primary.TButton", command=self.run_pipeline).pack(side="left", padx=(self.app.px(8), 0))

        work_area = ttk.Frame(self, style="Content.TFrame")
        work_area.pack(fill="both", expand=True)
        self.table = TreeFrame(work_area, self.COLUMNS, height=7)
        self.table.pack(fill="both", expand=True, pady=(0, self.app.px(8)))
        self.log = ScrolledText(work_area, height=5)
        self.log.pack(fill="x")

    def browse(self) -> None:
        path = filedialog.askopenfilename(title="选择 Excel 文件", filetypes=[("Excel files", "*.xls *.xlsx"), ("All files", "*.*")])
        if path:
            self.path_var.set(path)

    def load_file(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("提示", "请选择 Excel 文件")
            return
        self.notify("正在读取 Excel", "busy")
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
                self.notify(f"已加载 {len(rows)} 行", "success")
                self.app.complete_tutorial_step("import")

            self.ui(fill)
        except Exception as exc:
            message = str(exc)

            def show_error() -> None:
                self.app.import_rows = []
                self.table.clear()
                self.table.add_row(("", "", "", "-", "-", "-", f"读取失败：{message}"))
                self.log.clear()
                self.log.write_line(f"读取失败：{Path(path).name}")
                self.log.write_line(message)
                self.notify("Excel 读取失败", "error")
                messagebox.showerror("读取失败", message)

            self.ui(show_error)

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
        self.notify("预览完成", "success")
        self.app.complete_tutorial_step("import")

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
        self.notify("全流程执行中", "busy")
        self.app.run_bg(lambda: self._pipeline(self.app.import_rows, start, max_hours))

    def set_cell(self, row: int, column: str, value: str) -> None:
        self.table.update_cell(str(row), column, value)

    def _pipeline(self, rows: list[ImportRow], start: date, max_hours: float) -> None:
        cfg = self.app.cfg
        token = cfg["token"]

        def log(message: str) -> None:
            self.ui(lambda message=message: self.log.write_line(message))

        def set_cell(row: int, column: str, value: str) -> None:
            self.ui(lambda: self.set_cell(row, column, value))

        def abort_for_auth(exc: BaseException) -> bool:
            if not is_auth_error(exc):
                return False
            log("登录已过期，已停止全流程")
            self.ui(lambda exc=exc: self.app.handle_auth_error(exc))
            return True

        try:
            pk = api.get_public_key(token)
        except Exception as exc:
            if abort_for_auth(exc):
                return
            log(f"获取加密公钥失败：{exc}")
            return

        try:
            roster = api.fetch_roster(token, cfg["activity_id"], cfg["post_id"])
            log(f"roster loaded: {len(roster)}")
        except Exception as exc:
            if abort_for_auth(exc):
                return
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
                if abort_for_auth(exc):
                    return
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
                if abort_for_auth(exc):
                    return
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
                if abort_for_auth(exc):
                    return
                set_cell(i, "时数录入", "失败")
                set_cell(i, "状态", str(exc))
        self.ui(lambda: self.notify("全流程完成", "success"))
        self.ui(lambda: self.app.complete_tutorial_step("import"))


class TutorialWindow(tk.Toplevel):
    STEPS = (
        (
            "1. 登录",
            "扫码登录最省心；如果已有 accessToken，也可以在登录页手动粘贴保存。登录状态会保存在本机配置里。",
            "login",
        ),
        (
            "2. 配置活动",
            "按组织、项目、活动、岗位的顺序获取。选中岗位后，系统会自动回填 org_id、activity_id 和 post_id。",
            "config",
        ),
        (
            "3. 核对岗位名单",
            "名单页用于确认当前岗位已有成员。批量导入时，如果组织搜索找不到但人已在岗位里，会用这份名单兜底匹配。",
            "roster",
        ),
        (
            "4. 导入 Excel",
            "加载表格后先点“仅预览”，确认日期和每日上限无误，再点“开始全流程”。表格建议包含身份证号以减少重名风险。",
            "import",
        ),
        (
            "5. 看状态和日志",
            "表格列会显示组织搜索、入岗、时数录入的每一步结果。底部深色日志区保留更详细的处理记录。",
            "import",
        ),
    )

    def __init__(self, app: "BVGuiApp", auto: bool = False) -> None:
        super().__init__(app)
        self.app = app
        self.auto = auto
        self.index = 0
        self.title("快速引导")
        setup_dialog_window(self, app, width=760, height=520, min_width=680, min_height=460)
        self.transient(app)
        self.configure(background="#F8FAFC")
        self.protocol("WM_DELETE_WINDOW", self.dismiss)

        outer = ttk.Frame(self, style="Content.TFrame", padding=(app.px(20), app.px(18)))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="快速引导", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(outer, text="完成当前步骤后，向导会自动推进；也可以手动切换。", style="Muted.TLabel").pack(anchor="w", pady=(app.px(5), app.px(12)))

        controls = ttk.Frame(outer, style="Content.TFrame")
        controls.pack(side="bottom", fill="x", pady=(app.px(16), 0))
        self.back_btn = ttk.Button(controls, text="上一步", style="Secondary.TButton", command=self.prev_step)
        self.back_btn.pack(side="left")
        self.jump_btn = ttk.Button(controls, text="打开此页面", style="Secondary.TButton", command=self.jump_to_panel)
        self.jump_btn.pack(side="left", padx=(app.px(8), 0))
        self.next_btn = ttk.Button(controls, text="下一步", style="Primary.TButton", command=self.next_step)
        self.next_btn.pack(side="right")
        ttk.Button(controls, text="完成", style="Secondary.TButton", command=self.close).pack(side="right", padx=(0, app.px(8)))

        body = ttk.Frame(outer, style="Card.TFrame", padding=(app.px(18), app.px(14)))
        body.pack(fill="both", expand=True)
        self.step_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.body_var = tk.StringVar()
        self.hint_var = tk.StringVar()
        ttk.Label(body, textvariable=self.step_var, style="Badge.TLabel").pack(anchor="w")
        ttk.Label(body, textvariable=self.title_var, style="GuideTitle.TLabel").pack(anchor="w", pady=(app.px(14), app.px(8)))
        ttk.Label(body, textvariable=self.body_var, style="GuideBody.TLabel", wraplength=app.px(660), justify="left").pack(anchor="w", fill="x")
        ttk.Label(body, textvariable=self.hint_var, style="CardMuted.TLabel", wraplength=app.px(660), justify="left").pack(anchor="w", fill="x", pady=(app.px(14), 0))
        self.render()

    def render(self) -> None:
        title, body, _panel = self.STEPS[self.index]
        self.step_var.set(f"{self.index + 1} / {len(self.STEPS)}")
        self.title_var.set(title)
        self.body_var.set(body)
        self.hint_var.set("等待你完成这一步。完成后会自动进入下一步。")
        self.back_btn.configure(state="normal" if self.index else "disabled")
        self.next_btn.configure(text="完成" if self.index == len(self.STEPS) - 1 else "下一步")

    def prev_step(self) -> None:
        if self.index > 0:
            self.index -= 1
            self.render()
            self.jump_to_panel()

    def next_step(self) -> None:
        if self.index >= len(self.STEPS) - 1:
            self.close()
            return
        self.index += 1
        self.render()
        self.jump_to_panel()

    def jump_to_panel(self) -> None:
        _title, _body, panel = self.STEPS[self.index]
        self.app.show_panel(panel)

    def complete_panel(self, panel: str) -> None:
        _title, _body, expected_panel = self.STEPS[self.index]
        if panel != expected_panel:
            return
        self.hint_var.set("已完成，正在进入下一步…")
        if self.index >= len(self.STEPS) - 1:
            self.after(650, self.close)
        else:
            self.after(650, self.next_step)

    def close(self) -> None:
        self.app.cfg["tutorial_seen"] = "1"
        save_config(self.app.cfg)
        self.app.refresh_tutorial_button()
        self.dismiss()

    def dismiss(self) -> None:
        self.app.tutorial_window = None
        self.destroy()


class HelpPanel:
    @staticmethod
    def show(parent: tk.Misc) -> None:
        win = tk.Toplevel(parent)
        win.title("帮助与关于")
        setup_dialog_window(win, parent, width=920, height=760, min_width=760, min_height=620)
        win.configure(background=PALETTE["bg"])
        outer = ttk.Frame(win, style="Content.TFrame", padding=app_px(parent, 18))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="bv2008 志愿者管理工具", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="由中国人民大学商学院青年志愿者协会开发",
            font=("Microsoft YaHei UI", 10, "bold"),
            foreground="#8A1F1F",
        ).pack(anchor="w", pady=(app_px(parent, 4), app_px(parent, 12)))
        controls = ttk.Frame(outer, style="Content.TFrame")
        controls.pack(side="bottom", fill="x", pady=(app_px(parent, 12), 0))
        ttk.Button(controls, text="关闭", style="Secondary.TButton", command=win.destroy).pack(side="right")
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
        enable_high_dpi_awareness()
        super().__init__()
        self.ui_scale = self.setup_dpi_scaling()
        self.cfg = load_config()
        self.import_rows: list[ImportRow] = []
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_panel: BasePanel | None = None
        self.tutorial_window: TutorialWindow | None = None
        self.tutorial_button: ttk.Button | None = None
        self.page_title_var = tk.StringVar(value="登录")
        self.cache: dict[str, Any] = {
            "orgs": None,
            "default_org_id": "",
            "projects_by_org": {},
            "activities_by_project": {},
            "posts_by_activity": {},
        }
        self.cache_lock = threading.Lock()
        self.title("bv2008 志愿者管理工具")
        self.setup_window_size()
        self.setup_style()
        self.build_menu()
        self.build_layout()
        self.show_panel("login")
        if self.cfg.get("token"):
            self.status.set("正在校验登录状态…", "busy")
        else:
            self.status.set("未登录", "error")
        self.after(80, self.process_ui)
        self.after(250, self.warm_cache)
        self.after(550, self.maybe_show_tutorial)

    def setup_dpi_scaling(self) -> float:
        try:
            dpi = float(self.winfo_fpixels("1i"))
            scale = max(1.0, min(2.5, dpi / 96.0))
            self.tk.call("tk", "scaling", dpi / 72.0)
            return scale
        except Exception:
            return 1.0

    def px(self, value: int | float) -> int:
        return max(1, int(round(float(value) * self.ui_scale)))

    def setup_window_size(self) -> None:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(self.px(1180), max(self.px(940), screen_w - self.px(96)))
        height = min(self.px(740), max(self.px(620), screen_h - self.px(120)))
        self.geometry(f"{width}x{height}")
        self.minsize(min(self.px(940), width), min(self.px(600), height))

    def setup_style(self) -> None:
        style = ttk.Style(self)
        px = self.px
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.configure(background=PALETTE["bg"])
        self.option_add("*Font", FONT_UI)
        self.option_add("*TCombobox*Listbox.font", FONT_UI)
        self.option_add("*Menu.font", FONT_UI)

        style.configure(
            ".",
            background=PALETTE["bg"],
            foreground=PALETTE["text"],
            font=FONT_UI,
            bordercolor=PALETTE["line"],
            lightcolor=PALETTE["line"],
            darkcolor=PALETTE["line"],
        )
        style.configure("App.TFrame", background=PALETTE["bg"])
        style.configure("Content.TFrame", background=PALETTE["bg"])
        style.configure("Header.TFrame", background=PALETTE["surface"], relief="flat")
        style.configure("Sidebar.TFrame", background=PALETTE["sidebar"])
        style.configure("Card.TFrame", background=PALETTE["surface"], relief="solid", borderwidth=1)
        style.configure("CardBody.TFrame", background=PALETTE["surface"])
        style.configure("Panel.TFrame", background=PALETTE["surface"], relief="solid", borderwidth=1)
        style.configure("Table.TFrame", background=PALETTE["surface"], relief="solid", borderwidth=1)
        style.configure("Divider.TFrame", background=PALETTE["line"])
        style.configure("QrWell.TFrame", background=PALETTE["surface_alt"], relief="solid", borderwidth=1)
        style.configure("Status.TFrame", background=PALETTE["surface"], relief="solid", borderwidth=1)

        style.configure("Title.TLabel", font=FONT_HEADER, background=PALETTE["bg"], foreground=PALETTE["text"])
        style.configure("PageTitle.TLabel", font=FONT_TITLE, background=PALETTE["bg"], foreground=PALETTE["text"])
        style.configure("SectionTitle.TLabel", font=FONT_SECTION, background=PALETTE["surface"], foreground=PALETTE["text"])
        style.configure("Muted.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"])
        style.configure("CardMuted.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"])
        style.configure("FieldLabel.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"], font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Badge.TLabel", background=PALETTE["accent_soft"], foreground=PALETTE["accent_dark"], padding=(px(10), px(4)), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Success.Badge.TLabel", background=PALETTE["success_soft"], foreground=PALETTE["success"], padding=(px(10), px(4)), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Warning.Badge.TLabel", background=PALETTE["warning_soft"], foreground=PALETTE["warning"], padding=(px(10), px(4)), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Qr.TLabel", background=PALETTE["surface_alt"], foreground=PALETTE["muted"], font=("Microsoft YaHei UI", 12))
        style.configure("GuideTitle.TLabel", font=("Microsoft YaHei UI", 15, "bold"), background=PALETTE["surface"], foreground=PALETTE["text"])
        style.configure("GuideBody.TLabel", font=("Microsoft YaHei UI", 10), background=PALETTE["surface"], foreground="#334155")
        style.configure("Brand.TLabel", font=("Microsoft YaHei UI", 18, "bold"), background=PALETTE["sidebar"], foreground=PALETTE["sidebar_text"])
        style.configure("BrandSub.TLabel", background=PALETTE["sidebar"], foreground=PALETTE["sidebar_muted"], font=FONT_SMALL)
        style.configure("SidebarMuted.TLabel", background=PALETTE["sidebar"], foreground=PALETTE["sidebar_muted"], font=FONT_SMALL)
        style.configure("HeaderTitle.TLabel", font=FONT_HEADER, background=PALETTE["surface"], foreground=PALETTE["text"])
        style.configure("HeaderSub.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"], font=FONT_SMALL)
        style.configure("Status.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"])
        style.configure("StatusDot.Ready.TLabel", background=PALETTE["surface"], foreground=PALETTE["ready"], font=("Segoe UI", 10, "bold"))
        style.configure("StatusDot.Busy.TLabel", background=PALETTE["surface"], foreground=PALETTE["busy"], font=("Segoe UI", 10, "bold"))
        style.configure("StatusDot.Success.TLabel", background=PALETTE["surface"], foreground=PALETTE["success"], font=("Segoe UI", 10, "bold"))
        style.configure("StatusDot.Warning.TLabel", background=PALETTE["surface"], foreground=PALETTE["warning"], font=("Segoe UI", 10, "bold"))
        style.configure("StatusDot.Error.TLabel", background=PALETTE["surface"], foreground=PALETTE["error"], font=("Segoe UI", 10, "bold"))

        style.configure("TButton", padding=(px(12), px(7)), relief="flat", background=PALETTE["surface_alt"], foreground=PALETTE["text"], borderwidth=1)
        style.map(
            "TButton",
            background=[("disabled", "#E5EAF1"), ("pressed", "#E2E8F0"), ("active", "#EDF2F7")],
            foreground=[("disabled", "#94A3B8")],
        )
        style.configure("Primary.TButton", padding=(px(14), px(8)), background=PALETTE["accent"], foreground="#FFFFFF", borderwidth=0)
        style.map(
            "Primary.TButton",
            background=[("disabled", "#E5EAF1"), ("pressed", PALETTE["accent_dark"]), ("active", "#A31515")],
            foreground=[("disabled", "#94A3B8"), ("active", "#FFFFFF"), ("pressed", "#FFFFFF")],
        )
        style.configure("Secondary.TButton", padding=(px(12), px(7)), background=PALETTE["surface_alt"], foreground=PALETTE["text"], borderwidth=1)
        style.map(
            "Secondary.TButton",
            background=[("disabled", "#E5EAF1"), ("pressed", "#E2E8F0"), ("active", "#EEF2F7")],
            foreground=[("disabled", "#94A3B8"), ("active", PALETTE["text"]), ("pressed", PALETTE["text"])],
        )
        style.configure("Ghost.TButton", padding=(px(12), px(7)), background=PALETTE["surface"], foreground=PALETTE["accent"], borderwidth=1)
        style.map("Ghost.TButton", background=[("active", PALETTE["accent_soft"]), ("pressed", "#FEE2E2")])
        style.configure("Nav.TButton", anchor="w", padding=(px(12), px(10)), background=PALETTE["sidebar"], foreground=PALETTE["sidebar_muted"], borderwidth=0)
        style.configure("Active.Nav.TButton", anchor="w", padding=(px(12), px(10)), background=PALETTE["sidebar_alt"], foreground=PALETTE["sidebar_text"], borderwidth=0)
        style.configure("Sidebar.TButton", anchor="center", padding=(px(12), px(8)), background=PALETTE["sidebar_alt"], foreground=PALETTE["sidebar_text"], borderwidth=0)
        style.map(
            "Nav.TButton",
            background=[("active", PALETTE["sidebar_alt"]), ("pressed", PALETTE["sidebar_alt"])],
            foreground=[("active", PALETTE["sidebar_text"]), ("pressed", PALETTE["sidebar_text"])],
        )
        style.map(
            "Active.Nav.TButton",
            background=[("active", PALETTE["sidebar_alt"]), ("pressed", PALETTE["sidebar_alt"])],
            foreground=[("active", "#FFFFFF"), ("pressed", "#FFFFFF")],
        )
        style.map(
            "Sidebar.TButton",
            background=[("active", "#26354D"), ("pressed", "#26354D")],
            foreground=[("active", "#FFFFFF"), ("pressed", "#FFFFFF")],
        )

        style.configure("TEntry", padding=(px(8), px(6)), fieldbackground="#FFFFFF", foreground=PALETTE["text"], borderwidth=1)
        style.configure(
            "App.TCombobox",
            padding=(px(8), px(6)),
            fieldbackground="#FFFFFF",
            background="#FFFFFF",
            foreground=PALETTE["text"],
            arrowcolor=PALETTE["text"],
            arrowsize=px(14),
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", "#FFFFFF"), ("focus", "#FFFFFF"), ("active", "#FFFFFF")],
            background=[("readonly", "#FFFFFF"), ("focus", "#FFFFFF"), ("active", "#FFFFFF")],
            foreground=[("readonly", PALETTE["text"]), ("focus", PALETTE["text"]), ("active", PALETTE["text"])],
            selectbackground=[("readonly", "#FFFFFF"), ("focus", "#FFFFFF")],
            selectforeground=[("readonly", PALETTE["text"]), ("focus", PALETTE["text"])],
            arrowcolor=[("disabled", "#94A3B8"), ("pressed", PALETTE["accent"]), ("active", PALETTE["accent"]), ("readonly", PALETTE["text"])],
        )
        style.configure("Data.Treeview", rowheight=px(30), background="#FFFFFF", fieldbackground="#FFFFFF", borderwidth=0)
        style.configure("Data.Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#EEF2F7", foreground="#334155", padding=(px(8), px(7)))
        style.map("Data.Treeview", background=[("selected", PALETTE["accent_soft"])], foreground=[("selected", PALETTE["accent_dark"])])

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
        help_menu.add_command(label="重放引导教程", command=lambda: self.show_tutorial(auto=False))
        help_menu.add_command(label="帮助与关于", command=lambda: HelpPanel.show(self))
        menu.add_cascade(label="帮助", menu=help_menu)
        self.configure(menu=menu)
        self.bind_all("<Control-q>", lambda _event: self.destroy())

    def build_layout(self) -> None:
        root = ttk.Frame(self, style="App.TFrame")
        root.pack(fill="both", expand=True)
        body = ttk.Frame(root, style="App.TFrame")
        body.pack(side="top", fill="both", expand=True)
        sidebar = ttk.Frame(body, style="Sidebar.TFrame", padding=(self.px(18), self.px(16)), width=self.px(210))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="bv2008", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="批量录入 · 名单核对 · 活动配置", style="BrandSub.TLabel").pack(anchor="w", pady=(self.px(5), self.px(20)))
        for key, label in self.NAV_ITEMS:
            text = f"   {NAV_INDEX[key]}  {label}"
            button = ttk.Button(sidebar, text=text, style="Nav.TButton", command=lambda k=key: self.show_panel(k))
            button.pack(fill="x", pady=self.px(3))
            self.nav_buttons[key] = button
        self.tutorial_button = ttk.Button(sidebar, text="重放教程", style="Sidebar.TButton", command=lambda: self.show_tutorial(auto=False))
        self.refresh_tutorial_button()
        ttk.Label(sidebar, text="建议流程\n登录 -> 配置 -> 导入 -> 核对", style="SidebarMuted.TLabel", wraplength=self.px(180), justify="left").pack(side="bottom", anchor="w", pady=(self.px(20), 0))
        self.content = ttk.Frame(body, style="Content.TFrame")
        self.content.pack(side="left", fill="both", expand=True)
        self.status = StatusBar(root)
        self.status.pack(side="bottom", fill="x")

    def show_panel(self, name: str) -> None:
        panel_class = self.PANELS[name]
        for child in self.content.winfo_children():
            child.destroy()
        self.current_panel = panel_class(self.content, self)
        self.current_panel.pack(fill="both", expand=True)
        labels = dict(self.NAV_ITEMS)
        for key, button in self.nav_buttons.items():
            button.configure(style="Active.Nav.TButton" if key == name else "Nav.TButton")
            marker = "●" if key == name else " "
            button.configure(text=f"{marker}  {NAV_INDEX[key]}  {labels[key]}")
        label = labels.get(name, "就绪")
        self.page_title_var.set(label)
        self.status.set(label)

    def reset_cache(self) -> None:
        with self.cache_lock:
            self.cache = {
                "orgs": None,
                "default_org_id": "",
                "projects_by_org": {},
                "activities_by_project": {},
                "posts_by_activity": {},
            }

    def logout_expired(self, message: str = "登录已过期，请重新扫码登录") -> None:
        self.cfg["token"] = ""
        save_config(self.cfg)
        self.reset_cache()
        if isinstance(self.current_panel, LoginPanel):
            self.current_panel.refresh_status()
        else:
            self.show_panel("login")
            if isinstance(self.current_panel, LoginPanel):
                self.current_panel.refresh_status()
        self.status.set(message, "error")

    def handle_auth_error(self, exc: BaseException | str) -> bool:
        if not is_auth_error(exc):
            return False
        self.logout_expired("登录已过期，已清除本地 token，请重新扫码登录")
        return True

    def maybe_show_tutorial(self) -> None:
        if self.cfg.get("tutorial_seen") != "1":
            self.show_tutorial(auto=True)

    def refresh_tutorial_button(self) -> None:
        if self.tutorial_button is None:
            return
        if self.cfg.get("tutorial_seen") == "1":
            self.tutorial_button.pack_forget()
        elif not self.tutorial_button.winfo_ismapped():
            self.tutorial_button.pack(fill="x", pady=(self.px(14), 0))

    def show_tutorial(self, auto: bool = False) -> None:
        if self.tutorial_window is not None and self.tutorial_window.winfo_exists():
            self.tutorial_window.lift()
            self.tutorial_window.focus_force()
            return
        self.tutorial_window = TutorialWindow(self, auto=auto)

    def complete_tutorial_step(self, panel: str) -> None:
        if self.tutorial_window is not None and self.tutorial_window.winfo_exists():
            self.tutorial_window.complete_panel(panel)

    def warm_cache(self) -> None:
        token = self.cfg.get("token", "")
        if token:
            self.status.set("正在校验登录状态…", "busy")
            self.run_bg(lambda: self._warm_cache_worker(token))
        else:
            self.status.set("未登录", "error")

    def _warm_cache_worker(self, token: str) -> None:
        try:
            self.ui(lambda: self.status.set("正在后台缓存组织和活动数据…", "busy"))
            data = api.fetch_current_orgs(token)
            orgs = data["orgs"]
            default_org_id = str(data.get("defaultOrgId") or "")
            with self.cache_lock:
                self.cache["orgs"] = orgs
                self.cache["default_org_id"] = default_org_id

            for org in orgs:
                org_id = str(org.get("orgId") or "")
                if not org_id:
                    continue
                projects = api.fetch_selectable_projects(token, org_id)
                with self.cache_lock:
                    self.cache["projects_by_org"][org_id] = projects
                for project in projects:
                    project_id = str(project.get("iid") or "")
                    if not project_id:
                        continue
                    activities = api.fetch_activities(token, project_id)
                    with self.cache_lock:
                        self.cache["activities_by_project"][project_id] = activities
                    for activity in activities:
                        activity_id = str(activity.get("iid") or "")
                        if not activity_id:
                            continue
                        posts = api.fetch_posts(token, activity_id)
                        with self.cache_lock:
                            self.cache["posts_by_activity"][activity_id] = posts
            self.ui(lambda: self.status.set("登录状态有效，后台缓存完成", "success"))
        except Exception as exc:
            message = str(exc)
            if is_auth_error(exc):
                self.ui(lambda exc=exc: self.handle_auth_error(exc))
            else:
                self.ui(lambda message=message: self.status.set(f"后台缓存失败：{message}", "warning"))

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

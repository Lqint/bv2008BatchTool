"""Desktop GUI for the bv2008 batch hour workflow."""
from __future__ import annotations

import io
import mimetypes
import shutil
import sys
import time
from datetime import date
from pathlib import Path

from openpyxl import Workbook
import qrcode
from PySide6.QtCore import QDate, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bv_api import BVApi, PostInfo
from bv_batch_runner import BatchConfig, REQUIRED_HEADERS, result_output_path, run_batch


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)


class LoginQrWorker(Worker):
    def run(self) -> None:
        try:
            code_id, url = BVApi().create_login_qr()
            self.finished.emit((code_id, url))
        except Exception as exc:
            self.failed.emit(str(exc))


class LoginPollWorker(Worker):
    status = Signal(str)

    def __init__(self, code_id: str):
        super().__init__()
        self.code_id = code_id

    def run(self) -> None:
        api = BVApi()
        deadline = time.time() + 120
        scanned = False
        try:
            while time.time() < deadline:
                status, token = api.check_login_status(self.code_id)
                if status == "2" and not scanned:
                    self.status.emit("已扫码，等待手机确认")
                    scanned = True
                if status == "3" and token:
                    self.finished.emit(token)
                    return
                time.sleep(3)
            self.failed.emit("二维码登录超时，请重新生成")
        except Exception as exc:
            self.failed.emit(str(exc))


class PostsWorker(Worker):
    def __init__(self, token: str, activity_id: str, org_id: str):
        super().__init__()
        self.token = token
        self.activity_id = activity_id
        self.org_id = org_id

    def run(self) -> None:
        try:
            posts = BVApi(self.token).find_posts(self.activity_id, self.org_id)
            self.finished.emit(posts)
        except Exception as exc:
            self.failed.emit(str(exc))


class BatchWorker(Worker):
    progress = Signal(str)

    def __init__(self, config: BatchConfig, posts: list[PostInfo]):
        super().__init__()
        self.config = config
        self.posts = posts

    def run(self) -> None:
        try:
            output = run_batch(self.config, self.posts, progress=self.progress.emit)
            self.finished.emit(output)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("志愿北京时长批量录入v2")
        self.resize(1100, 760)

        self.posts: list[PostInfo] = []
        self.xlsx_path: Path | None = None
        self.proof_path: Path | None = None
        self.active_threads: list[QThread] = []
        self.active_workers: list[Worker] = []

        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.Password)
        self.activity_input = QLineEdit()
        self.org_input = QLineEdit()
        self.xlsx_label = QLabel("未选择")
        self.proof_label = QLabel("未选择，将使用 1x1 PNG 占位图")
        self.date_input = QDateEdit(QDate.currentDate())
        self.date_input.setCalendarPopup(True)
        self.post_list = QListWidget()
        self.qr_label = QLabel("点击按钮生成二维码")
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumSize(220, 220)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.qr_button = QPushButton("生成登录二维码")
        self.start_button = QPushButton("开始批量录入")
        self.notice_button = QPushButton("查看须知")
        self.result_label = QLabel("")

        self.setup_ui()
        QTimer.singleShot(0, self.show_start_notice)

    def setup_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QGridLayout(root)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 1)

        left = QVBoxLayout()
        right = QVBoxLayout()
        layout.addLayout(left, 0, 0)
        layout.addLayout(right, 0, 1)

        login_box = QGroupBox("1. 登录")
        login_layout = QVBoxLayout(login_box)
        login_layout.addWidget(self.qr_label)
        self.qr_button.clicked.connect(self.create_qr)
        login_layout.addWidget(self.qr_button)
        login_layout.addWidget(QLabel("或手动粘贴 TOKEN（调试用）"))
        login_layout.addWidget(self.token_input)
        left.addWidget(login_box)

        activity_box = QGroupBox("2. 活动与岗位")
        activity_layout = QVBoxLayout(activity_box)
        activity_layout.addWidget(QLabel("活动 ID"))
        activity_layout.addWidget(self.activity_input)
        activity_layout.addWidget(QLabel("组织 ID"))
        activity_layout.addWidget(self.org_input)
        fetch_btn = QPushButton("获取岗位信息")
        fetch_btn.clicked.connect(self.fetch_posts)
        activity_layout.addWidget(fetch_btn)
        left.addWidget(activity_box)

        upload_box = QGroupBox("3-5. 文件与日期")
        upload_layout = QVBoxLayout(upload_box)
        xlsx_btn = QPushButton("选择 xlsx 表格")
        xlsx_btn.clicked.connect(self.select_xlsx)
        template_btn = QPushButton("下载模板")
        template_btn.clicked.connect(self.download_template)
        xlsx_buttons = QHBoxLayout()
        xlsx_buttons.addWidget(xlsx_btn, 7)
        xlsx_buttons.addWidget(template_btn, 3)
        proof_btn = QPushButton("选择 jpg/png 证明材料（可选）")
        proof_btn.clicked.connect(self.select_proof)
        upload_layout.addLayout(xlsx_buttons)
        upload_layout.addWidget(self.xlsx_label)
        upload_layout.addWidget(proof_btn)
        upload_layout.addWidget(self.proof_label)
        upload_layout.addWidget(QLabel("志愿录入起始日期（请对齐活动日期）"))
        upload_layout.addWidget(self.date_input)
        left.addWidget(upload_box)

        self.start_button.clicked.connect(self.start_batch)
        left.addWidget(self.start_button)
        left.addStretch(1)

        notice_bar = QHBoxLayout()
        notice_bar.addStretch(1)
        self.notice_button.clicked.connect(self.show_start_notice)
        notice_bar.addWidget(self.notice_button)
        right.addLayout(notice_bar)

        posts_box = QGroupBox("岗位列表（表格中的“岗位”列需匹配这里的名称）")
        posts_layout = QVBoxLayout(posts_box)
        posts_layout.addWidget(self.post_list)
        right.addWidget(posts_box, 2)

        log_box = QGroupBox("处理日志")
        log_layout = QVBoxLayout(log_box)
        log_layout.addWidget(self.log)
        log_layout.addWidget(self.result_label)
        right.addWidget(log_box, 3)

        self.setStyleSheet(
            """
            QWidget { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 13px; }
            QMainWindow { background: rgb(248, 244, 238); }
            QWidget { background: rgb(248, 244, 238); }
            QGroupBox { border: 1px solid #d8cfc4; border-radius: 6px; margin-top: 10px; padding: 10px; background: #fffdfa; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #2f2523; font-weight: 600; }
            QLineEdit, QDateEdit, QPlainTextEdit, QListWidget { border: 1px solid #d1c7bb; border-radius: 4px; padding: 6px; background: #ffffff; color: #2f2523; }
            QPushButton { border: 1px solid rgb(174, 11, 42); border-radius: 4px; padding: 8px 10px; color: #ffffff; background: rgb(174, 11, 42); font-weight: 600; }
            QPushButton:hover { background: #8f0924; border-color: #8f0924; }
            QPushButton:disabled { background: #b8aaa9; border-color: #b8aaa9; }
            QLabel { color: #3d302d; }
            """
        )

    def run_worker(self, worker: Worker, on_finished, on_failed=None, progress=None, status=None) -> None:
        thread = QThread()
        self.active_workers.append(worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(on_failed or self.show_error)
        worker.failed.connect(thread.quit)
        worker.failed.connect(worker.deleteLater)
        if progress and hasattr(worker, "progress"):
            worker.progress.connect(progress)
        if status and hasattr(worker, "status"):
            worker.status.connect(status)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self.active_threads.remove(thread) if thread in self.active_threads else None)
        thread.finished.connect(lambda: self.active_workers.remove(worker) if worker in self.active_workers else None)
        self.active_threads.append(thread)
        thread.start()

    def create_qr(self) -> None:
        self.qr_button.setEnabled(False)
        self.qr_label.setText("正在向服务器申请二维码...")
        self.append_log("正在生成登录二维码...")
        self.run_worker(LoginQrWorker(), self.on_qr_created, on_failed=self.on_qr_failed)

    def on_qr_failed(self, message: str) -> None:
        self.qr_button.setEnabled(True)
        self.qr_label.setText("二维码生成失败，可重试")
        self.show_error(message)

    def on_qr_created(self, payload) -> None:
        code_id, url = payload
        image = qrcode.make(url)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue(), "PNG")
        self.qr_label.setPixmap(pixmap.scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.qr_button.setText("重新生成二维码")
        self.qr_button.setEnabled(True)
        self.append_log("二维码已生成，请扫码确认")
        self.run_worker(LoginPollWorker(code_id), self.on_token_received, on_failed=self.on_login_failed, status=self.append_log)

    def on_token_received(self, token: str) -> None:
        self.token_input.setText(token)
        self.append_log("登录成功，TOKEN 已自动填入")

    def on_login_failed(self, message: str) -> None:
        self.qr_button.setEnabled(True)
        self.show_error(message)

    def fetch_posts(self) -> None:
        token = self.token_input.text().strip()
        activity_id = self.activity_input.text().strip()
        org_id = self.org_input.text().strip()
        if not token or not activity_id or not org_id:
            self.show_error("请先填写 TOKEN、活动 ID 和组织 ID")
            return
        self.append_log("正在获取岗位列表...")
        self.run_worker(PostsWorker(token, activity_id, org_id), self.on_posts_loaded)

    def on_posts_loaded(self, posts: list[PostInfo]) -> None:
        self.posts = posts
        self.post_list.clear()
        for post in posts:
            self.post_list.addItem(post.name)
        self.append_log(f"已获取 {len(posts)} 个岗位")

    def select_xlsx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 xlsx 表格", "", "Excel Workbook (*.xlsx)")
        if path:
            self.xlsx_path = Path(path)
            self.xlsx_label.setText(str(self.xlsx_path))

    def download_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存模板", "模板.xlsx", "Excel Workbook (*.xlsx)")
        if not path:
            return
        output = Path(path)
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        wb = Workbook()
        ws = wb.active
        ws.title = "志愿时长导入模板"
        ws.append(REQUIRED_HEADERS)
        ws.append(["张三", "110101199001011234", "示例岗位", 10, "示例备注"])
        ws.append(["李四", "", "示例岗位", 5, ""])
        ws.column_dimensions["B"].width = 24
        wb.save(output)
        QMessageBox.information(self, "模板已保存", f"模板已保存至：\n{output}")

    def resource_path(self, filename: str) -> Path:
        cwd_path = Path.cwd() / filename
        if cwd_path.exists():
            return cwd_path
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            return Path(bundle_dir) / filename
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / filename
        return Path(__file__).resolve().parent / filename

    def download_support_doc(self) -> None:
        source = self.resource_path("配套文档.png")
        if not source.exists():
            source = self.resource_path("support_doc.png")
        if not source.exists():
            QMessageBox.warning(self, "未找到配套文档", f"未找到：\n{source}")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存配套文档", "配套文档.png", "PNG Image (*.png)")
        if not path:
            return
        output = Path(path)
        if output.suffix.lower() != ".png":
            output = output.with_suffix(".png")
        shutil.copyfile(source, output)
        QMessageBox.information(self, "配套文档已保存", f"配套文档已保存至：\n{output}")

    def show_start_notice(self) -> None:
        notice = (
            "使用本工具前，请您仔细阅读下面的通知：\n\n"
            "1. 本工具用于“志愿北京”新版平台的志愿时长批量录入，旨在方便青协同学处理工作。请仅在自己负责的组织、合规的志愿者管理流程中使用；因未授权、误用或不当使用造成的后果，由使用者自行承担，开发者不承担相关责任。\n\n"
            "2. 本工具为 v2 版本，后续可能仍会更新。请在使用前确认当前程序为最新版本。\n\n"
            "3. 本工具链路：根据姓名和身份证号(如有)在团体内查询志愿者id->若未查到且已填写身份证号，将其加入团体->使用uid加入对应岗位->录入志愿时长。\n\n"
            "4. 请按左侧操作区的顺序依次完成登录、填写活动参数、获取岗位、上传表格、选择起始日期、启动批量录入。\n\n"
            "5. 您仍需手动在“志愿北京”网站上完成创建项目、创建子项目、创建活动流程。本工具只用于提高招募志愿者和录入时长的效率。\n\n"
            "6. 活动 ID、组织 ID 需自行从“志愿北京”网站获取，获取方式请参考配套文档。\n\n"
            "7. 表格需包含列：姓名、身份证号（选填）、岗位、时长、备注（选填）。您可使用提供的模板.xlsx。岗位列内容必须与“志愿北京”平台一致，也就是与右侧自动获取的岗位列表一致。\n\n"
            "8. 图片证明材料仅支持 jpg/png 格式；未选择证明材料时，程序会自动使用 1x1 PNG 占位图。\n\n"
            "9. 每日最多录入 10 小时，程序会自动计算可行性，超出可录入范围的记录将会跳过并写入原因。目前仅支持连续日期录入。\n\n"
            "10. 录入结果将保存至 *_result.xlsx。本程序为个人开发，未经过全面测试，请在程序执行成功后检查结果文件，并到“志愿北京”平台核查，防止出现错误。\n\n"
            "11. API 逆向与网关接口由 GitHub @Lqint 实现；重构与可视化界面由 GitHub @xiaoyuer5126 实现。"
        )
        while True:
            box = QMessageBox(self)
            box.setWindowTitle("重要使用须知")
            box.setIcon(QMessageBox.Information)
            box.setText(notice)
            box.setMinimumWidth(860)
            box.layout().setColumnMinimumWidth(1, 720)
            doc_button = box.addButton("下载配套文档", QMessageBox.ActionRole)
            box.addButton("我已知晓", QMessageBox.AcceptRole)
            box.exec()
            if box.clickedButton() == doc_button:
                self.download_support_doc()
                continue
            break

    def select_proof(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 jpg/png 证明材料", "", "Images (*.jpg *.jpeg *.png)")
        if path:
            self.proof_path = Path(path)
            self.proof_label.setText(str(self.proof_path))

    def start_batch(self) -> None:
        token = self.token_input.text().strip()
        activity_id = self.activity_input.text().strip()
        org_id = self.org_input.text().strip()
        if not token or not activity_id or not org_id:
            self.show_error("请先填写 TOKEN、活动 ID 和组织 ID")
            return
        if not self.posts:
            self.show_error("请先获取岗位信息")
            return
        if not self.xlsx_path:
            self.show_error("请选择 xlsx 表格")
            return

        proof_name = None
        proof_bytes = None
        proof_mime = None
        if self.proof_path:
            if self.proof_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                self.show_error("证明材料必须是 jpg 或 png 图片")
                return
            proof_name = self.proof_path.name
            proof_bytes = self.proof_path.read_bytes()
            proof_mime = mimetypes.guess_type(proof_name)[0] or "application/octet-stream"

        qdate = self.date_input.date()
        start_date = date(qdate.year(), qdate.month(), qdate.day())
        config = BatchConfig(
            token=token,
            activity_id=activity_id,
            org_id=org_id,
            start_date=start_date,
            xlsx_path=self.xlsx_path,
            output_path=result_output_path(self.xlsx_path),
            proof_name=proof_name,
            proof_bytes=proof_bytes,
            proof_mime=proof_mime,
        )

        self.start_button.setEnabled(False)
        self.result_label.setText("")
        self.append_log("开始批量录入...")
        worker = BatchWorker(config, self.posts)
        self.run_worker(worker, self.on_batch_finished, on_failed=self.on_batch_failed, progress=self.append_log)

    def on_batch_finished(self, output: Path) -> None:
        self.start_button.setEnabled(True)
        self.result_label.setText(f"结果文件：{output}")
        self.append_log("批量录入完成")
        QMessageBox.information(self, "完成", f"结果文件已生成：\n{output}")

    def on_batch_failed(self, message: str) -> None:
        self.start_button.setEnabled(True)
        self.show_error(message)

    def append_log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def show_error(self, message: str) -> None:
        self.append_log(f"错误：{message}")
        QMessageBox.warning(self, "错误", message)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

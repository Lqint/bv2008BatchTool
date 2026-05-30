"""Desktop GUI for the bv2008 batch hour workflow."""
from __future__ import annotations

import io
import sys
import time
from datetime import date
from pathlib import Path

import qrcode
from PySide6.QtCore import QDate, QObject, Qt, QThread, Signal
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
from bv_batch_runner import BatchConfig, result_output_path, run_batch

try:
    from config import ACTIVITY_ID, ORG_ID, TOKEN
except ImportError:
    ACTIVITY_ID = ""
    ORG_ID = ""
    TOKEN = ""


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
        self.setWindowTitle("bv2008 志愿时长批量录入")
        self.resize(1100, 760)

        self.posts: list[PostInfo] = []
        self.xlsx_path: Path | None = None
        self.proof_path: Path | None = None
        self.active_threads: list[QThread] = []
        self.active_workers: list[Worker] = []

        self.token_input = QLineEdit(TOKEN)
        self.token_input.setEchoMode(QLineEdit.Password)
        self.activity_input = QLineEdit(ACTIVITY_ID)
        self.org_input = QLineEdit(ORG_ID)
        self.xlsx_label = QLabel("未选择")
        self.proof_label = QLabel("未选择，将使用 1x1 PNG 占位图")
        self.date_input = QDateEdit(QDate.currentDate())
        self.date_input.setCalendarPopup(True)
        self.post_list = QListWidget()
        self.qr_label = QLabel("点击生成二维码")
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumSize(220, 220)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.qr_button = QPushButton("生成扫码二维码")
        self.start_button = QPushButton("开始批量录入")
        self.result_label = QLabel("")

        self.setup_ui()

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
        login_layout.addWidget(QLabel("或手动粘贴 TOKEN"))
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
        proof_btn = QPushButton("选择 png 证明材料（可选）")
        proof_btn.clicked.connect(self.select_proof)
        upload_layout.addWidget(xlsx_btn)
        upload_layout.addWidget(self.xlsx_label)
        upload_layout.addWidget(proof_btn)
        upload_layout.addWidget(self.proof_label)
        upload_layout.addWidget(QLabel("志愿录入起始日期"))
        upload_layout.addWidget(self.date_input)
        left.addWidget(upload_box)

        self.start_button.clicked.connect(self.start_batch)
        left.addWidget(self.start_button)
        left.addStretch(1)

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
            QMainWindow { background: #f6f7f9; }
            QGroupBox { border: 1px solid #d7dce2; border-radius: 6px; margin-top: 10px; padding: 10px; background: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #26313d; font-weight: 600; }
            QLineEdit, QDateEdit, QPlainTextEdit, QListWidget { border: 1px solid #cfd6de; border-radius: 4px; padding: 6px; background: #ffffff; }
            QPushButton { border: 1px solid #2364aa; border-radius: 4px; padding: 8px 10px; color: #ffffff; background: #2f76c2; font-weight: 600; }
            QPushButton:disabled { background: #9aa8b5; border-color: #9aa8b5; }
            QLabel { color: #354251; }
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

    def select_proof(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 png 证明材料", "", "PNG Image (*.png)")
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
        if self.proof_path:
            if self.proof_path.suffix.lower() != ".png":
                self.show_error("证明材料必须是 png 图片")
                return
            proof_name = self.proof_path.name
            proof_bytes = self.proof_path.read_bytes()

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

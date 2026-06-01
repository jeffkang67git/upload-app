"""
ReportRow - 单个报告的展示行（预览按钮 + 进度 + 状态）
"""
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QProgressBar
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

import subprocess, os


class ReportRow(QWidget):
    preview_clicked = pyqtSignal(object)   # 发送 ReportRecord

    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.init_ui()
        self.update_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 缩略图占位
        self.thumb_label = QLabel("📄")
        self.thumb_label.setFixedSize(80, 100)
        self.thumb_label.setStyleSheet(
            "border: 1px solid #ccc; background: #f9f9f9; "
            "font-size: 32px; text-align: center;"
        )
        self.thumb_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.thumb_label)

        # 报告信息
        info = QVBoxLayout()
        info.setSpacing(2)

        self.name_label = QLabel(self.report.display_name)
        self.name_label.setFont(QFont("Arial", 11, QFont.Bold))
        info.addWidget(self.name_label)

        self.file_label = QLabel(self.report.pdf_path.name)
        self.file_label.setStyleSheet("color: #666; font-size: 10px;")
        info.addWidget(self.file_label)

        self.ord_label = QLabel(f"医嘱号: {self.report.ord_no or '待获取'}")
        self.ord_label.setStyleSheet("color: #333; font-size: 10px;")
        info.addWidget(self.ord_label)

        # 预览按钮
        self.btn_preview = QPushButton("预览")
        self.btn_preview.setFixedWidth(70)
        self.btn_preview.clicked.connect(self.on_preview)
        info.addWidget(self.btn_preview)

        info.addStretch()
        layout.addLayout(info, 1)

        # 状态 + 进度
        right = QVBoxLayout()
        right.setSpacing(4)

        self.status_label = QLabel("待上传")
        self.status_label.setFont(QFont("Arial", 10))
        self.status_label.setAlignment(Qt.AlignRight)
        right.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        right.addWidget(self.progress_bar)

        layout.addLayout(right, 1)

        self.setStyleSheet("""
            QWidget { background: white; border-radius: 4px; }
            QWidget:hover { background: #F0F7FF; }
        """)

    def update_ui(self):
        rep = self.report
        self.ord_label.setText(f"医嘱号: {rep.ord_no or '待获取'}")

        if rep.status == 'done':
            self.status_label.setText("✓ 已上传")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.progress_bar.setValue(100)
            self.btn_preview.setText("已预览")
            self.btn_preview.setEnabled(False)
        elif rep.status == 'fail':
            self.status_label.setText(f"✗ 失败: {rep.error_msg[:20]}")
            self.status_label.setStyleSheet("color: red;")
            self.progress_bar.setValue(0)
        elif rep.status == 'previewed':
            self.status_label.setText("○ 已预览")
            self.status_label.setStyleSheet("color: #888;")
            self.btn_preview.setText("已预览")
        elif rep.status == 'uploading':
            self.status_label.setText("上传中...")
            self.status_label.setStyleSheet("color: #0066CC;")
            self.progress_bar.setValue(rep.progress)
        else:
            self.status_label.setText("待上传")
            self.status_label.setStyleSheet("color: #666;")
            self.progress_bar.setValue(0)

    def on_preview(self):
        # 用 macOS Preview 打开 PDF
        subprocess.run(["open", str(self.report.pdf_path)])
        self.preview_clicked.emit(self.report)
        self.btn_preview.setText("已预览")
        self.btn_preview.setStyleSheet("background-color: #ccc; color: #666;")
        self.report.status = 'previewed'
        self.status_label.setText("○ 已预览")
        self.status_label.setStyleSheet("color: #888;")
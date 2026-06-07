"""
SZU体检报告批量上传应用 - MainWindow
"""
import sys, os, time, json, re, subprocess, shutil as _shutil
from pathlib import Path
from datetime import datetime

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    from PyPDF2 import PdfReader, PdfWriter

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QProgressBar, QMessageBox,
    QInputDialog, QScrollArea, QProgressDialog, QApplication,
    QMainWindow, QFrame, QDialog, QLineEdit, QGridLayout as QGL,
    QScrollBar
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QPixmap, QImage

from src.config_manager import ConfigManager

IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform == 'win32'

DEVICE_LIST = [
    "人体成分分析4楼", "人体成分分析9楼", "肺功能", "肺功能9楼",
    "airdoc", "Airdoc 4楼", "动脉硬化检测仪", "肝纤维化扫描", "动脉硬化检测仪9F"
]
URCODE_MAP = {
    "人体成分分析4楼": "SZU01", "人体成分分析9楼": "SZU02",
    "肺功能": "SZU04", "肺功能9楼": "SZU05",
    "airdoc": "SZU06", "Airdoc 4楼": "SZU15",
    "动脉硬化检测仪": "SZU07", "肝纤维化扫描": "SZU16", "动脉硬化检测仪9F": "SZU17",
}
ARCIM_MAP = {
    "SZU01": "6930||1^77||2", "SZU02": "6930||1^77||2",
    "SZU04": "592||1", "SZU05": "592||1",
    "SZU06": "8249||1^304||1", "SZU15": "8249||1^304||1",
    "SZU07": "7970||1", "SZU16": "35791||1", "SZU17": "7970||1",
}
CSP_PAGE_TPL = "https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode={urcode}&CurLocID=343"
FTP_CONFIG = {"host": "10.1.9.105", "port": 2121, "username": "dhccftp", "password": "Dhcc123!qwe"}

# Chrome 路径自动检测
if IS_MAC:
    CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
elif IS_WIN:
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    # 也检查 Program Files (x86)
    if not os.path.exists(CHROME_PATH):
        alt = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        if os.path.exists(alt):
            CHROME_PATH = alt
else:
    CHROME_PATH = "google-chrome"
# shutil.which 兜底
_detected = _shutil.which("chrome") or _shutil.which("google-chrome") or _shutil.which("chromium") or _shutil.which("google-chrome-stable")
if _detected:
    CHROME_PATH = _detected

LOG_FILE = Path.home() / 'Documents' / 'Projects' / '体检报告上传' / 'app' / 'upload_log.json'
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
CARDS_VISIBLE = 5  # 卡片行最多显示5张


def log_append(mrn, exam_type, user_id, status, ord_no=""):
    logs = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except Exception:
            logs = []
    logs.append({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "mrn": mrn, "exam_type": exam_type, "user_id": user_id,
                 "status": status, "ord_no": ord_no})
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


# ============================================================
# CSP 调用（复用 Chrome driver）
# ============================================================

def _make_chrome_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    opts.binary_location = CHROME_PATH
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")
    prefs = {"credentials_enable_service": False, "password_manager_enabled": False}
    opts.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(20)
    return driver


def _csp_call(driver, method, *args):
    """在页面 JS 上下文中调用 tkMakeServerCall"""
    args_js = ", ".join([f"'{str(a)}'" for a in args])
    script = f"""
    try {{
        var r = tkMakeServerCall('web.DHCPE.Interface.Main', '{method}', {args_js});
        return JSON.stringify({{ok: true, result: r}});
    }} catch(e) {{
        return JSON.stringify({{ok: false, error: e.message}});
    }}
    """
    data = json.loads(driver.execute_script(script))
    if not data.get("ok"):
        raise RuntimeError(f"CSP {method} 失败: {data.get('error')}")
    return data["result"]


def _get_user_id(driver, user_input, cur_loc_id="343", arcim=""):
    """
    调用 GetUserID 验证签字人，返回用户ID
    签名: GetUserID(userCode, curLocID, arcim)
    """
    raw = _csp_call(driver, "GetUserID", user_input, cur_loc_id, arcim)
    return raw.strip()


def _get_base_info(driver, pe_rec_no, arcim, cur_loc_id="343"):
    """
    调用 GetBaseInfo，返回 (ord_no, raw_response)
    签名: GetBaseInfo(examNo, arcim, "HPNo", curLocID)
    """
    raw = _csp_call(driver, "GetBaseInfo", pe_rec_no, arcim, "HPNo", cur_loc_id)
    parts = raw.split("^")
    if len(parts) < 8:
        raise RuntimeError(f"GetBaseInfo 返回格式异常: {raw}")
    ord_no = parts[7].strip()
    return ord_no, raw


def _save_result(driver, pe_rec_no, user_id, arcim_main, arcim_sub, cur_loc_id="343"):
    """
    调用 SaveResult，通知医嘱被执行
    签名: SaveResult(peRecNo, resultText, userInternalID, arcimMain, arcimSub, curLocID)
    WARG_4 = 检查所见（PDF 填"参见报告"）
    """
    text = "参见报告"
    raw = _csp_call(driver, "SaveResult",
                    pe_rec_no, text, user_id, arcim_main, arcim_sub, cur_loc_id)
    return raw.strip()


def _save_upload_info(driver, ord_no, user_id, file_path):
    """
    调用 SaveUploadInfo，记录上传文件信息
    签名: SaveUploadInfo(ordNo, userInternalID, filePath, status)
    """
    raw = _csp_call(driver, "SaveUploadInfo", ord_no, user_id, file_path, "1")
    return raw.strip()


class ReportRecord:
    def __init__(self, pdf_path, device_name):
        self.pdf_path = Path(pdf_path)
        self.device_name = device_name
        self.urcode = URCODE_MAP.get(device_name, "")
        self.arcim = ARCIM_MAP.get(self.urcode, "")
        self.ord_no = ""
        self.arcim_sub = ""  # 从 GetBaseInfo 返回值 fields[8] 提取，用于 SaveResult
        self.user_id = ""     # GetUserID 返回的用户ID
        self.status = "pending"
        self.progress = 0
        self.error_msg = ""

    @property
    def display_device(self):
        name = self.device_name
        if name.endswith("4楼"): return name[:-2].rstrip() + " · 4楼"
        elif name.endswith("9楼"): return name[:-2].rstrip() + " · 9楼"
        elif name.endswith("9F"): return name[:-2].rstrip() + " · 9F"
        return name

    @property
    def display_name(self):
        name = self.device_name
        for suf in ["4楼", "9楼", "9F"]:
            name = name.replace(suf, "")
        return name


class PatientRecord:
    def __init__(self, mrn):
        self.mrn = mrn
        self.reports = []

    def add_report(self, pdf_path, device_name):
        self.reports.append(ReportRecord(pdf_path, device_name))


class ScanWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(list)

    def __init__(self, base_dir):
        super().__init__()
        self.base_dir = Path(base_dir)

    def run(self):
        patients = {}
        for i, device in enumerate(DEVICE_LIST):
            device_dir = self.base_dir / device
            if not device_dir.exists():
                continue
            self.progress.emit(f"扫描: {device}", int(i / len(DEVICE_LIST) * 100))
            for pdf in sorted(device_dir.glob("*.pdf")):
                mrn = pdf.stem
                if mrn not in patients:
                    patients[mrn] = PatientRecord(mrn)
                patients[mrn].add_report(pdf, device)
        self.finished.emit(sorted(patients.values(), key=lambda p: p.mrn))


class ConfirmDialog(QDialog):
    def __init__(self, patient_mrn, total_reports, parent=None):
        super().__init__(parent)
        self.user_id = ""
        self.setWindowTitle("确认上传")
        self.setModal(True)
        self.setFixedSize(420, 360)
        self._build_ui(patient_mrn, total_reports)

    def _build_ui(self, mrn, total):
        self.setStyleSheet("""
            QDialog { background: white; }
            .header { background: #FAFAFA; border-bottom: 1px solid #E5E5EA; border-radius: 14px 14px 0 0; }
            .body { padding: 20px 24px; }
            .footer { padding: 0 24px 20px; }
            .info-box { background: #F5F7FF; border: 1px solid #DDE8FF; border-radius: 10px; padding: 14px 16px; margin-bottom: 16px; }
            .info-row { display: flex; justify-content: space-between; margin-bottom: 6px; }
            .info-row:last-child { margin-bottom: 0; }
            .lbl { font-size: 13px; color: #555; }
            .val { font-size: 13px; font-weight: 600; }
            .val-blue { color: #0066CC; font-size: 15px; }
            .input-label { font-size: 12px; color: #555; margin-bottom: 6px; display: block; }
            .input { padding: 9px 12px; border: 1px solid #D1D1D6; border-radius: 8px; font-size: 13px; }
            .input:focus { border-color: #0066CC; }
            .hint { font-size: 11px; color: #86868B; margin-top: 6px; }
            .btn-cancel { padding: 9px 20px; border: 1px solid #D1D1D6; border-radius: 8px; background: white; color: #333; font-size: 13px; }
            .btn-cancel:hover { background: #F5F5F7; }
            .btn-confirm { padding: 9px 22px; border: none; border-radius: 8px; background: #0066CC; color: white; font-size: 13px; font-weight: 600; }
            .btn-confirm:hover { background: #0055AA; }
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setObjectName("header")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 16, 0)
        if IS_MAC:
            dots = QWidget()
            dl = QHBoxLayout(dots)
            dl.setSpacing(6)
            for c in ["#FF5F57", "#FEBC2E", "#28C840"]:
                d = QLabel()
                d.setFixedSize(12, 12)
                d.setStyleSheet(f"background: {c}; border-radius: 6px;")
                dl.addWidget(d)
            hl.addWidget(dots)
            hl.addSpacing(8)
        hl.addWidget(QLabel("确认上传"))
        hl.addStretch()
        lay.addWidget(hdr)

        # Body
        body = QWidget()
        body.setObjectName("body")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 20, 24, 16)

        bl.addWidget(QLabel("请核对签字并确认上传"))

        info = QWidget()
        info.setObjectName("info-box")
        il = QVBoxLayout(info)
        il.setContentsMargins(14, 14, 14, 14)
        il.setSpacing(6)
        for lbl, val, blue in [
            ("患者编号", mrn, True),
            ("报告数量", f"{total} 份报告", False),
            ("上传至", "东华HIS · 10.1.9.105:1443", False),
            ("PDF合并保存至", "桌面", False),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(lbl))
            row.addStretch()
            v = QLabel(val)
            v.setStyleSheet("font-weight: 600;" + ("color: #0066CC; font-size: 15px;" if blue else "color: #1D1D1F;"))
            row.addWidget(v)
            il.addLayout(row)
        bl.addWidget(info)

        bl.addWidget(QLabel("签字（填写用户编号）"))
        self.input = QLineEdit()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("例如：zhangsan01")
        bl.addWidget(self.input)
        hint = QLabel("签字人将记录在操作日志中")
        hint.setObjectName("hint")
        bl.addWidget(hint)
        bl.addStretch()
        lay.addWidget(body)

        # Footer
        footer = QWidget()
        footer.setObjectName("footer")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("btn-cancel")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("确认上传")
        confirm.setObjectName("btn-confirm")
        confirm.clicked.connect(self._do_confirm)
        fl.addWidget(cancel)
        fl.addWidget(confirm)
        lay.addWidget(footer)

    def _do_confirm(self):
        text = self.input.text().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请填写签字（用户编号）")
            return
        self.user_id = text
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_mgr = ConfigManager()
        self.config = self.config_mgr.load()
        self.patients = []
        self.current_idx = 0
        self._chrome_driver = None
        # 新增状态
        self._current_report_idx = 0   # 当前患者中选中的报告索引
        self._preview_visible = True    # PDF 预览展开/收起
        self._card_scroll_offset = 0    # 卡片行滚动偏移
        self._pixmap_cache = {}         # path -> QPixmap 缓存
        self.init_ui()
        self.start_scan()

    def init_ui(self):
        self.setWindowTitle("SZU体检报告批量上传+合并应用")
        self.resize(1100, 680)
        self.setMinimumWidth(900)
        self.setWindowFlags(Qt.Window)

        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet("background: #FFFFFF;")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Body (flex)
        body = QWidget()
        body.setStyleSheet("background: white;")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # Left panel (190px, scrollable)
        left = QWidget()
        left.setFixedWidth(190)
        left.setStyleSheet("background: #FAFAFA; border-right: 1px solid #F0F0F0;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        lt = QLabel("患者列表")
        lt.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #86868B; "
            "text-transform: uppercase; letter-spacing: 0.6px; padding: 10px 16px 8px;")
        ll.addWidget(lt)
        self.patient_list = QListWidget()
        self.patient_list.setStyleSheet("""
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget::item { padding: 9px 14px; }
            QListWidget::item:selected { background: #E8F4FF; border-left: 3px solid #0066CC; }
            QListWidget::item:hover { background: #F0F0F5; }
        """)
        self.patient_list.itemClicked.connect(self._on_patient_clicked)
        ll.addWidget(self.patient_list)
        bl.addWidget(left)

        # ============================================================
        # Right panel — 卡片行 + PDF 预览区
        # ============================================================
        right = QWidget()
        right.setStyleSheet("background: white;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 12, 16, 0)
        rl.setSpacing(0)

        # Patient header
        ph = QWidget()
        ph.setStyleSheet("background: transparent;")
        phl = QHBoxLayout(ph)
        phl.setContentsMargins(0, 0, 0, 8)
        phl.addWidget(QLabel("患者"))
        self.lbl_mrn = QLabel("---")
        self.lbl_mrn.setStyleSheet("font-size: 13px; font-weight: 600;")
        phl.addWidget(self.lbl_mrn)
        phl.addWidget(QLabel("的报告"))
        phl.addStretch()
        rl.addWidget(ph)

        # --- Card row (horizontal scroll) ---
        card_row_container = QWidget()
        card_row_container.setStyleSheet("background: transparent;")
        crc_layout = QHBoxLayout(card_row_container)
        crc_layout.setContentsMargins(0, 0, 0, 8)
        crc_layout.setSpacing(4)

        self.btn_card_left = QPushButton("◀")
        self.btn_card_left.setFixedSize(28, 28)
        self.btn_card_left.setStyleSheet(
            "QPushButton { border: 1px solid #D1D1D6; border-radius: 14px; background: white; font-size: 12px; color: #555; }"
            "QPushButton:hover { background: #F0F0F5; border-color: #0066CC; }")
        self.btn_card_left.clicked.connect(self._on_card_scroll_left)
        crc_layout.addWidget(self.btn_card_left)

        self.card_scroll_area = QScrollArea()
        self.card_scroll_area.setStyleSheet("background: transparent; border: none;")
        self.card_scroll_area.setFixedHeight(72)
        self.card_scroll_area.setWidgetResizable(True)
        self.card_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.card_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.card_row_widget = QWidget()
        self.card_row_widget.setStyleSheet("background: transparent;")
        self.card_row_layout = QHBoxLayout(self.card_row_widget)
        self.card_row_layout.setContentsMargins(0, 0, 0, 0)
        self.card_row_layout.setSpacing(8)
        self.card_row_layout.addStretch()
        self.card_scroll_area.setWidget(self.card_row_widget)
        crc_layout.addWidget(self.card_scroll_area, 1)

        self.btn_card_right = QPushButton("▶")
        self.btn_card_right.setFixedSize(28, 28)
        self.btn_card_right.setStyleSheet(
            "QPushButton { border: 1px solid #D1D1D6; border-radius: 14px; background: white; font-size: 12px; color: #555; }"
            "QPushButton:hover { background: #F0F0F5; border-color: #0066CC; }")
        self.btn_card_right.clicked.connect(self._on_card_scroll_right)
        crc_layout.addWidget(self.btn_card_right)

        rl.addWidget(card_row_container)

        # --- PDF preview area (collapsible) ---
        self.preview_area = QWidget()
        self.preview_area.setStyleSheet("background: #F8F9FA; border-top: 1px solid #F0F0F0;")
        preview_layout = QVBoxLayout(self.preview_area)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)

        # PDF header bar
        self.pdf_header_bar = QWidget()
        self.pdf_header_bar.setStyleSheet("background: #FAFAFA; border-bottom: 1px solid #EEE;")
        self.pdf_header_bar.setFixedHeight(28)
        phb_layout = QHBoxLayout(self.pdf_header_bar)
        phb_layout.setContentsMargins(12, 0, 12, 0)
        self.lbl_pdf_filename = QLabel("")
        self.lbl_pdf_filename.setStyleSheet("font-size: 11px; color: #86868B;")
        phb_layout.addWidget(self.lbl_pdf_filename)
        phb_layout.addStretch()
        preview_layout.addWidget(self.pdf_header_bar)

        # Scrollable PDF image area
        self.pdf_scroll_area = QScrollArea()
        self.pdf_scroll_area.setStyleSheet("background: #F8F9FA; border: none;")
        self.pdf_scroll_area.setWidgetResizable(True)
        self.pdf_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        pdf_container = QWidget()
        pdf_container.setStyleSheet("background: transparent;")
        self.pdf_image_layout = QVBoxLayout(pdf_container)
        self.pdf_image_layout.setContentsMargins(16, 12, 16, 12)
        self.lbl_pdf_page = QLabel()
        self.lbl_pdf_page.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.lbl_pdf_page.setStyleSheet("background: transparent;")
        self.pdf_image_layout.addWidget(self.lbl_pdf_page)
        self.pdf_scroll_area.setWidget(pdf_container)
        preview_layout.addWidget(self.pdf_scroll_area, 1)

        # Left/Right nav buttons (overlay on preview)
        self.btn_prev_report = QPushButton("◀")
        self.btn_prev_report.setFixedSize(40, 40)
        self.btn_prev_report.setStyleSheet(
            "QPushButton { border: 1px solid #D1D1D6; border-radius: 20px; background: white; font-size: 16px; color: #333; }"
            "QPushButton:hover { background: #0066CC; color: white; border-color: #0066CC; }")
        self.btn_prev_report.clicked.connect(self._on_prev_report)
        self.btn_prev_report.setParent(self.preview_area)
        self.btn_prev_report.raise_()

        self.btn_next_report = QPushButton("▶")
        self.btn_next_report.setFixedSize(40, 40)
        self.btn_next_report.setStyleSheet(
            "QPushButton { border: 1px solid #D1D1D6; border-radius: 20px; background: white; font-size: 16px; color: #333; }"
            "QPushButton:hover { background: #0066CC; color: white; border-color: #0066CC; }")
        self.btn_next_report.clicked.connect(self._on_next_report)
        self.btn_next_report.setParent(self.preview_area)
        self.btn_next_report.raise_()

        rl.addWidget(self.preview_area, 1)

        bl.addWidget(right, 1)
        root.addWidget(body, 1)

        # Bottom bar (52px)
        btm = QWidget()
        btm.setFixedHeight(52)
        btm.setStyleSheet("background: #FAFAFA; border-top: 1px solid #F0F0F0;")
        btml = QHBoxLayout(btm)
        btml.setContentsMargins(24, 0, 24, 0)

        self.lbl_last_upload = QLabel("今日未上传")
        self.lbl_last_upload.setStyleSheet("font-size: 11px; color: #86868B;")
        btml.addWidget(self.lbl_last_upload)

        # Nav group (centered) — patient-level navigation
        ng = QHBoxLayout()
        ng.setSpacing(8)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedSize(34, 34)
        self.btn_prev.setStyleSheet(
            "border: 1px solid #D1D1D6; border-radius: 8px; background: white; "
            "font-size: 14px; color: #333;")
        self.btn_prev.clicked.connect(self._on_prev_patient)
        ng.addWidget(self.btn_prev)
        self.lbl_counter = QLabel("0 / 0")
        self.lbl_counter.setStyleSheet(
            "font-size: 13px; font-weight: 500; min-width: 80px; text-align: center;")
        self.lbl_counter.setAlignment(Qt.AlignCenter)
        ng.addWidget(self.lbl_counter)
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedSize(34, 34)
        self.btn_next.setStyleSheet(
            "border: 1px solid #D1D1D6; border-radius: 8px; background: white; "
            "font-size: 14px; color: #333;")
        self.btn_next.clicked.connect(self._on_next_patient)
        ng.addWidget(self.btn_next)
        btml.addLayout(ng)

        self.btn_upload = QPushButton("批量上传到东华 + 合并PDF")
        self.btn_upload.setStyleSheet(
            "background: #0066CC; color: white; border: none; border-radius: 8px; "
            "padding: 9px 20px; font-size: 13px; font-weight: 600;")
        self.btn_upload.clicked.connect(self._on_batch_upload)
        btml.addWidget(self.btn_upload)
        root.addWidget(btm)

        # Version bar
        ver = QLabel("V2.0 developed by Jeffrey Kang")
        ver.setStyleSheet(
            "background: #FAFAFA; text-align: center; font-size: 10px; color: #C7C7CC; padding: 4px;")
        ver.setAlignment(Qt.AlignCenter)
        root.addWidget(ver)

    def start_scan(self):
        self.statusBar().showMessage("正在扫描...")
        self.scan_worker = ScanWorker(self.config['base_dir'])
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.start()

    def _on_scan_progress(self, msg, pct):
        self.statusBar().showMessage(f"扫描中... {msg}")

    def _on_scan_finished(self, patients):
        self.patients = patients
        self.statusBar().showMessage(f"共 {len(patients)} 位患者，已扫描完成")
        self._refresh_patient_list()
        if patients:
            self.current_idx = 0
            self._current_report_idx = 0
            self._card_scroll_offset = 0
            self._show_patient(0)

    def _refresh_patient_list(self):
        self.patient_list.clear()
        for i, p in enumerate(self.patients):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, i)
            item.setText(f"{i+1}  {p.mrn}  {len(p.reports)}份")
            self.patient_list.addItem(item)

    def _on_patient_clicked(self, item):
        idx = item.data(Qt.UserRole)
        if idx is None:
            idx = self.patient_list.row(item)
        if 0 <= idx < len(self.patients):
            self.current_idx = idx
            self._current_report_idx = 0
            self._card_scroll_offset = 0
            self._show_patient(idx)

    # ============================================================
    # 患者级别导航 (底部栏 ◀ ▶)
    # ============================================================
    def _on_prev_patient(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self._current_report_idx = 0
            self._card_scroll_offset = 0
            self._show_patient(self.current_idx)

    def _on_next_patient(self):
        if self.current_idx < len(self.patients) - 1:
            self.current_idx += 1
            self._current_report_idx = 0
            self._card_scroll_offset = 0
            self._show_patient(self.current_idx)

    def _show_patient(self, idx):
        if idx < 0 or idx >= len(self.patients):
            return
        patient = self.patients[idx]
        self.current_idx = idx
        self._current_report_idx = 0
        self._card_scroll_offset = 0
        self._preview_visible = True
        self.preview_area.setVisible(True)

        self.patient_list.setCurrentRow(idx)
        self.lbl_mrn.setText(patient.mrn)

        total_patients = len(self.patients)
        self.lbl_counter.setText(f"{idx + 1} / {total_patients}")

        self._render_card_row()
        self._render_pdf_preview()

    # ============================================================
    # 卡片行渲染 + 横向滚动
    # ============================================================
    def _max_card_scroll(self):
        """卡片行最多可滚动偏移量"""
        patient = self.patients[self.current_idx]
        total = len(patient.reports)
        return max(0, total - CARDS_VISIBLE)

    def _on_card_scroll_left(self):
        if self._card_scroll_offset > 0:
            self._card_scroll_offset -= 1
            self._render_card_row()

    def _on_card_scroll_right(self):
        if self._card_scroll_offset < self._max_card_scroll():
            self._card_scroll_offset += 1
            self._render_card_row()

    def _render_card_row(self):
        """渲染卡片行（最多显示 CARDS_VISIBLE 张）"""
        patient = self.patients[self.current_idx]
        reports = patient.reports

        # 清除旧卡片
        while self.card_row_layout.count():
            child = self.card_row_layout.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()

        start = self._card_scroll_offset
        visible = reports[start:start + CARDS_VISIBLE]

        for i, rep in enumerate(visible):
            actual_idx = start + i
            is_active = (actual_idx == self._current_report_idx and self._preview_visible)

            card = QFrame()
            card.setFixedSize(140, 64)
            card.setCursor(Qt.PointingHandCursor)

            # 状态配色
            if rep.status == 'done':
                border = "#28C840" if is_active else "#C7E8C7"
                bg = "#F0FFF0" if is_active else "#FAFFFA"
            elif rep.status == 'fail':
                border = "#FF3B30" if is_active else "#FFD5D5"
                bg = "#FFFAFA"
            else:
                border = "#0066CC" if is_active else "#E8E8ED"
                bg = "#F5F9FF" if is_active else "white"

            if is_active:
                card.setStyleSheet(
                    f"background: {bg}; border: 2px solid {border}; border-radius: 10px;")
            else:
                card.setStyleSheet(
                    f"background: {bg}; border: 2px solid {border}; border-radius: 10px;")

            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(3)

            # 设备名
            dv = QLabel(rep.display_device)
            dv.setStyleSheet("font-size: 12px; font-weight: 600; color: #1D1D1F;")
            dv.setWordWrap(False)
            cl.addWidget(dv)

            # 医嘱号
            ord_text = rep.ord_no if rep.ord_no else "待获取"
            is_err = rep.ord_no and str(rep.ord_no).startswith("ERR")
            ol = QLabel(ord_text)
            ol.setStyleSheet(
                "font-size: 11px; color: #FF3B30;" if is_err else
                "font-size: 11px; color: #86868B;")
            cl.addWidget(ol)

            # 上传状态
            if rep.status == 'done':
                st_text, st_color = "✓ 已上传", "#28C840"
            elif rep.status == 'fail':
                st_text, st_color = "✗ 上传失败", "#FF3B30"
            else:
                st_text, st_color = "待上传", "#86868B"
            st = QLabel(st_text)
            st.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {st_color};")
            cl.addWidget(st)

            # 点击事件
            card.mousePressEvent = lambda ev, r=rep, aidx=actual_idx: self._on_card_clicked(r, aidx)
            self.card_row_layout.addWidget(card)

        self.card_row_layout.addStretch()

        # 更新左右箭头状态
        self.btn_card_left.setEnabled(self._card_scroll_offset > 0)
        self.btn_card_right.setEnabled(self._card_scroll_offset < self._max_card_scroll())

    def _on_card_clicked(self, rep, idx):
        """卡片点击：同卡片+预览可见 → 收起；否则 → 切换并展开"""
        if idx == self._current_report_idx and self._preview_visible:
            # 收起预览
            self._preview_visible = False
            self.preview_area.setVisible(False)
            self._render_card_row()
        else:
            self._current_report_idx = idx
            self._preview_visible = True
            self.preview_area.setVisible(True)
            self._render_card_row()
            self._render_pdf_preview()
            # 确保卡片行滚动到可见
            self._ensure_card_visible(idx)

    def _ensure_card_visible(self, idx):
        """确保索引 idx 的卡片在可见范围内"""
        if idx < self._card_scroll_offset:
            self._card_scroll_offset = idx
        elif idx >= self._card_scroll_offset + CARDS_VISIBLE:
            self._card_scroll_offset = idx - CARDS_VISIBLE + 1
        else:
            return
        self._render_card_row()

    # ============================================================
    # 报告级别导航 (预览区 ◀ ▶ 悬浮按钮)
    # ============================================================
    def _on_prev_report(self):
        if self._current_report_idx > 0:
            self._current_report_idx -= 1
            self._ensure_card_visible(self._current_report_idx)
            self._render_pdf_preview()
            self._render_card_row()

    def _on_next_report(self):
        patient = self.patients[self.current_idx]
        if self._current_report_idx < len(patient.reports) - 1:
            self._current_report_idx += 1
            self._ensure_card_visible(self._current_report_idx)
            self._render_pdf_preview()
            self._render_card_row()

    # ============================================================
    # PDF 首页渲染
    # ============================================================
    def _render_pdf_preview(self):
        """渲染当前选中报告的 PDF 首页"""
        patient = self.patients[self.current_idx]
        reports = patient.reports

        if not reports or self._current_report_idx >= len(reports):
            self.lbl_pdf_filename.setText("")
            self.lbl_pdf_page.clear()
            self.btn_prev_report.setVisible(False)
            self.btn_next_report.setVisible(False)
            return

        rep = reports[self._current_report_idx]
        pdf_path = str(rep.pdf_path)

        # 更新 header
        self.lbl_pdf_filename.setText(f"📄 {rep.pdf_path.name} — 第1页")

        # 更新导航按钮可见性
        self.btn_prev_report.setVisible(self._current_report_idx > 0)
        self.btn_next_report.setVisible(self._current_report_idx < len(reports) - 1)

        # 渲染 PDF 首页为 QPixmap
        pixmap = self._get_pdf_pixmap(pdf_path)
        if pixmap:
            self.lbl_pdf_page.setPixmap(pixmap)
        else:
            self.lbl_pdf_page.setText("无法渲染 PDF 预览")
            self.lbl_pdf_page.setStyleSheet("font-size: 14px; color: #C7C7CC; padding: 40px;")

        # 定位悬浮导航按钮
        self._position_nav_buttons()
        # 滚动到顶部
        self.pdf_scroll_area.verticalScrollBar().setValue(0)

    def _get_pdf_pixmap(self, pdf_path):
        """获取 PDF 首页的 QPixmap，带缓存"""
        cache_key = pdf_path
        if cache_key in self._pixmap_cache:
            return self._pixmap_cache[cache_key]

        if fitz is None:
            return None

        try:
            doc = fitz.open(pdf_path)
            if len(doc) == 0:
                doc.close()
                return None
            page = doc[0]

            # 按预览区宽度渲染（减去 padding）
            target_width = self.pdf_scroll_area.viewport().width() - 32
            if target_width < 100:
                target_width = 600  # 默认宽度

            zoom = target_width / page.rect.width
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            # 转换为 QPixmap
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(img)
            self._pixmap_cache[cache_key] = pixmap
            doc.close()
            return pixmap
        except Exception:
            return None

    def _position_nav_buttons(self):
        """将 ◀ ▶ 导航按钮定位到预览区垂直居中位置"""
        area_h = self.preview_area.height()
        if area_h > 0:
            btn_y = (area_h - 40) // 2
            self.btn_prev_report.move(12, btn_y)
            self.btn_next_report.move(self.preview_area.width() - 52, btn_y)

    def resizeEvent(self, event):
        """窗口大小变化时重新定位导航按钮 + 刷新 PDF"""
        super().resizeEvent(event)
        if hasattr(self, 'btn_prev_report'):
            self._position_nav_buttons()
        # 清除缓存以适配新宽度
        if hasattr(self, '_pixmap_cache'):
            self._pixmap_cache.clear()
        if hasattr(self, '_preview_visible') and self._preview_visible and hasattr(self, 'patients') and self.patients:
            QTimer.singleShot(100, self._render_pdf_preview)

    def _on_batch_upload(self):
        if not self.patients:
            QMessageBox.warning(self, "提示", "没有报告可上传")
            return
        total = sum(len(p.reports) for p in self.patients)
        dlg = ConfirmDialog(self.patients[self.current_idx].mrn, total, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        user_id = dlg.user_id
        self._fetch_and_upload(user_id)

    def _fetch_and_upload(self, user_id_input):
        """获取当前选中患者的医嘱号阶段"""
        current = self.patients[self.current_idx] if 0 <= self.current_idx < len(self.patients) else None
        if not current:
            QMessageBox.warning(self, "提示", "没有选中的患者")
            return
        patient_reports = [(current, rep) for rep in current.reports]
        all_reports = patient_reports
        total = len(all_reports)

        dlg = QProgressDialog("正在获取医嘱号...", "取消", 0, total, self)
        dlg.setWindowTitle("准备上传")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.show()
        QApplication.processEvents()

        driver = None
        for idx, (patient, rep) in enumerate(all_reports):
            if dlg.wasCanceled():
                break
            dlg.setValue(idx)
            dlg.setLabelText(f"获取 {patient.mrn} - {rep.device_name}...")
            QApplication.processEvents()

            # 已有有效 ord_no 的跳过
            if rep.ord_no and not str(rep.ord_no).startswith("ERR"):
                continue
            if not rep.urcode:
                rep.ord_no = "ERR_NO_URCODE"
                continue

            if driver is None:
                try:
                    driver = _make_chrome_driver()
                    driver.set_page_load_timeout(15)
                    driver.get(CSP_PAGE_TPL.format(urcode=rep.urcode))
                    time.sleep(5)
                except Exception as e:
                    rep.ord_no = f"ERR_CHROME:{e}"
                    continue

            try:
                result = driver.execute_script(f"""
                try {{
                    var r = tkMakeServerCall('web.DHCPE.Interface.Main', 'GetBaseInfo',
                                  '{patient.mrn}', '{rep.arcim}', 'HPNo', '343');
                    return r;
                }} catch(e) {{
                    return 'ERR:' + e.message;
                }}
                """)
                if result and not str(result).startswith("ERR") and result != "NoHP":
                    fields = result.split("^")
                    if len(fields) >= 9:
                        rep.ord_no = fields[7].strip()
                        rep.arcim_sub = fields[8].strip()  # 用于 SaveResult 的 arcim 子ID
                        log_append(patient.mrn, rep.device_name, user_id_input, "ord_found", rep.ord_no)
                    else:
                        rep.ord_no = "ERR_PARSE"
                else:
                    rep.ord_no = f"ERR_{result or 'NoHP'}"
            except Exception as e:
                rep.ord_no = f"ERR:{e}"
                log_append(patient.mrn, rep.device_name, user_id_input, "ord_fail", str(e))

        if driver:
            try:
                driver.quit()
            except Exception:
                pass

        dlg.close()
        self._do_upload(user_id_input)

    def _do_upload(self, user_id_input):
        """仅上传当前选中的患者报告"""
        current = self.patients[self.current_idx] if 0 <= self.current_idx < len(self.patients) else None
        if not current:
            QMessageBox.warning(self, "提示", "没有选中的患者")
            return
        patient_reports = [(current, rep) for rep in current.reports
                           if rep.ord_no and not str(rep.ord_no).startswith("ERR")]
        valid = patient_reports
        total = len(valid)
        if total == 0:
            QMessageBox.warning(self, "提示", "没有有效报告可上传")
            return

        # 创建独立 driver 用于 _do_upload 阶段
        dlg = QProgressDialog("正在上传...", "取消", 0, 100, self)
        dlg.setWindowTitle("上传进度")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.show()
        QApplication.processEvents()

        driver = None
        try:
            driver = _make_chrome_driver()
            driver.set_page_load_timeout(15)
            # 加载任意一个 urcode 页面（以第一个有效 report 的 urcode 为准）
            first_urcode = valid[0][1].urcode or "SZU06"
            driver.get(CSP_PAGE_TPL.format(urcode=first_urcode))
            time.sleep(5)

            # Step 1: GetUserID 验证签字人
            user_orditem_id = _get_user_id(driver, user_id_input, "343", valid[0][1].arcim)
            log_append("SYSTEM", "GetUserID", user_id_input, "user_verified", user_orditem_id)

            # Step 2: 遍历有效报告，上传
            for done, (patient, rep) in enumerate(valid):
                dlg.setLabelText(f"{rep.pdf_path.name} ({done + 1}/{total})")
                QApplication.processEvents()

                try:
                    # 2a. SaveResult 通知医嘱被执行（用 GetBaseInfo 返回的 arcim_sub）
                    arcim_parts = rep.arcim.split("^")
                    arcim_main = arcim_parts[0] if len(arcim_parts) >= 1 else rep.arcim
                    arcim_sub = rep.arcim_sub or (arcim_parts[1] if len(arcim_parts) >= 2 else "")
                    _save_result(driver, patient.mrn, user_orditem_id, arcim_main, arcim_sub, "343")

                    # 2b. FTPS 上传
                    self._upload_single(rep)

                    # 2c. SaveUploadInfo 记录文件
                    file_path = f"dhcpeftp/images/{rep.ord_no}/{rep.ord_no}_1.pdf"
                    _save_upload_info(driver, rep.ord_no, user_orditem_id, file_path)

                    rep.status = 'done'
                    log_append(patient.mrn, rep.device_name, user_id_input, "uploaded", rep.ord_no)
                except Exception as e:
                    rep.status = 'fail'
                    rep.error_msg = str(e)
                    log_append(patient.mrn, rep.device_name, user_id_input, "upload_fail", str(e))

                pct = int((done + 1) / total * 100) if total > 0 else 100
                dlg.setValue(pct)
                QApplication.processEvents()

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            dlg.close()

        self.lbl_last_upload.setText(f"今日已上传: {datetime.now().strftime('%H:%M')}")
        self._merge_to_desktop(user_id_input)
        QMessageBox.information(self, "完成", "上传并合并完成！")
        self._show_patient(self.current_idx)

    def _upload_single(self, report):
        import ftplib, ssl, shutil

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ftps = ftplib.FTP_TLS(context=ctx)
        ftps.connect(FTP_CONFIG["host"], FTP_CONFIG["port"], timeout=10)
        ftps.login(FTP_CONFIG["username"], FTP_CONFIG["password"])
        ftps.prot_p()

        # Navigate to /dhcpeftp/images/{ord_no}/
        ord_no = report.ord_no
        for segment in ["dhcpeftp", "images", str(ord_no)]:
            try:
                ftps.cwd(segment)
            except ftplib.error_perm:
                try:
                    ftps.mkd(segment)
                    ftps.cwd(segment)
                except Exception:
                    pass

        # Upload as {ord_no}_1.pdf
        remote_name = f"{ord_no}_1.pdf"
        tmp = f"/tmp/{remote_name}"
        shutil.copy(str(report.pdf_path), tmp)

        if not os.path.exists(tmp):
            raise RuntimeError(f"临时文件创建失败: {tmp}")

        file_size = os.path.getsize(tmp)
        if file_size == 0:
            raise RuntimeError(f"PDF文件为空: {report.pdf_path}")

        with open(tmp, 'rb') as f:
            resp = ftps.storbinary(f"STOR {remote_name}", f)

        if not resp.startswith("226"):
            raise RuntimeError(f"FTPS上传失败: {resp}")

        try:
            ftps.quit()
        except Exception:
            pass
        try:
            os.remove(tmp)
        except Exception:
            pass

    def _merge_to_desktop(self, user_id):
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        for patient in self.patients:
            valid = [r for r in patient.reports if r.status == 'done' and r.ord_no]
            if not valid:
                continue
            parts = []
            for r in valid:
                n = r.display_name
                if n not in parts:
                    parts.append(n)
            merge_name = patient.mrn + "_" + "_".join(parts) + ".pdf"
            out_path = desktop / merge_name
            writer = PdfWriter()
            for rep in valid:
                try:
                    reader = PdfReader(str(rep.pdf_path))
                    for page in reader.pages:
                        writer.add_page(page)
                except Exception:
                    pass
            with open(out_path, 'wb') as f:
                writer.write(f)
            log_append(patient.mrn, "MERGE", user_id, "merged", str(out_path))

    def closeEvent(self, event):
        if self._chrome_driver:
            try:
                self._chrome_driver.quit()
            except Exception:
                pass
        event.accept()
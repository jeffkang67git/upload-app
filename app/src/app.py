"""
SZU体检报告批量上传应用 - MainWindow
"""
import sys, os, time, json, re, subprocess
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
    QMainWindow, QFrame, QDialog, QLineEdit, QGridLayout as QGL
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from src.config_manager import ConfigManager

IS_MAC = sys.platform == 'darwin'

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
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
LOG_FILE = Path.home() / 'Documents' / 'Projects' / '体检报告上传' / 'app' / 'upload_log.json'
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
CARDS_PER_PAGE = 10  # 5 rows × 2 cols


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
        self.current_page = 0
        self._chrome_driver = None
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

        # Right panel
        right = QWidget()
        right.setStyleSheet("background: white;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 16, 20, 16)
        rl.setSpacing(10)

        # Patient header
        ph = QWidget()
        ph.setStyleSheet("background: transparent;")
        phl = QHBoxLayout(ph)
        phl.setContentsMargins(0, 0, 0, 0)
        phl.addWidget(QLabel("患者"))
        self.lbl_mrn = QLabel("---")
        self.lbl_mrn.setStyleSheet("font-size: 13px; font-weight: 600;")
        phl.addWidget(self.lbl_mrn)
        phl.addWidget(QLabel("的报告"))
        phl.addStretch()
        rl.addWidget(ph)

        # Cards scroll area (fixed height inside right panel)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setFixedHeight(480)
        self.cards_scroll.setStyleSheet("background: transparent; border: none;")
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Container widget for cards
        self.cards_container = QWidget()
        self.cards_grid = QGridLayout(self.cards_container)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(8)
        self.cards_scroll.setWidget(self.cards_container)
        rl.addWidget(self.cards_scroll)

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

        # Nav group (centered)
        ng = QHBoxLayout()
        ng.setSpacing(8)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedSize(34, 34)
        self.btn_prev.setStyleSheet(
            "border: 1px solid #D1D1D6; border-radius: 8px; background: white; "
            "font-size: 14px; color: #333;")
        self.btn_prev.clicked.connect(self._on_prev)
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
        self.btn_next.clicked.connect(self._on_next)
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
        ver = QLabel("V1.0 developed by Jeffrey Kang")
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
            self.current_page = 0
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
            self.current_page = 0
            self._show_patient(idx)

    def _max_page(self, idx):
        total = len(self.patients[idx].reports)
        return max(0, (total - 1) // CARDS_PER_PAGE) if total > 0 else 0

    def _on_prev(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._render_cards()
        elif self.current_idx > 0:
            self.current_idx -= 1
            self.current_page = self._max_page(self.current_idx)
            self._show_patient(self.current_idx)

    def _on_next(self):
        total = len(self.patients[self.current_idx].reports)
        pages = max(1, (total + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
        if self.current_page < pages - 1:
            self.current_page += 1
            self._render_cards()
        elif self.current_idx < len(self.patients) - 1:
            self.current_idx += 1
            self.current_page = 0
            self._show_patient(self.current_idx)

    def _show_patient(self, idx):
        if idx < 0 or idx >= len(self.patients):
            return
        patient = self.patients[idx]
        self.current_idx = idx
        self.current_page = 0

        self.patient_list.setCurrentRow(idx)
        self.lbl_mrn.setText(patient.mrn)

        total_reports = len(patient.reports)
        total_patients = len(self.patients)
        start = idx * CARDS_PER_PAGE + 1
        end = min(start + CARDS_PER_PAGE - 1, total_patients * CARDS_PER_PAGE)
        self.lbl_counter.setText(f"{start} - {end} / {total_patients * CARDS_PER_PAGE}")

        self._render_cards()

    def _render_cards(self):
        patient = self.patients[self.current_idx]
        reports = patient.reports

        # Clear grid
        while self.cards_grid.count():
            child = self.cards_grid.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()

        start = self.current_page * CARDS_PER_PAGE
        page_reports = reports[start:start + CARDS_PER_PAGE]

        # Pad to CARDS_PER_PAGE
        padded = page_reports + [None] * (CARDS_PER_PAGE - len(page_reports))

        for i, rep in enumerate(padded):
            card = QFrame()
            card.setFixedHeight(88)
            if rep is None:
                # Empty slot
                card.setStyleSheet("background: transparent; border: none;")
                el = QVBoxLayout(card)
                el.setContentsMargins(0, 0, 0, 0)
            else:
                if rep.status == 'previewed':
                    card.setStyleSheet(
                        "background: #FAFCFF; border: 1px solid #CCE4FF; border-radius: 10px;")
                else:
                    card.setStyleSheet(
                        "background: white; border: 1px solid #E8E8ED; border-radius: 10px;")
                cl = QHBoxLayout(card)
                cl.setContentsMargins(12, 10, 12, 10)
                cl.setSpacing(10)

                # Thumb
                thumb = QLabel("📄")
                thumb.setFixedSize(46, 60)
                thumb.setStyleSheet(
                    "background: #F5F5F7; border: 1px solid #E0E0E4; border-radius: 5px; "
                    "font-size: 22px;")
                thumb.setAlignment(Qt.AlignCenter)
                cl.addWidget(thumb)

                # Info
                info = QVBoxLayout()
                info.setSpacing(2)
                dv = QLabel(rep.display_device)
                dv.setStyleSheet("font-size: 13px; font-weight: 600; color: #1D1D1F;")
                info.addWidget(dv)

                ord_text = rep.ord_no if rep.ord_no else "待获取"
                if rep.ord_no and str(rep.ord_no).startswith("ERR"):
                    ol = QLabel(f"医嘱号: {ord_text}")
                    ol.setStyleSheet(
                        "font-size: 11px; color: #FF3B30; background: #FFE8E8; "
                        "padding: 2px 8px; border-radius: 10px;")
                    info.addWidget(ol)
                else:
                    ol = QLabel(f"医嘱号: {ord_text}")
                    ol.setStyleSheet("font-size: 11px; color: #555; margin-top: 4px;")
                    info.addWidget(ol)
                info.addStretch()
                cl.addLayout(info, 1)

                # Actions
                right = QVBoxLayout()
                right.setSpacing(4)

                btn = QPushButton("已预览" if rep.status == 'previewed' else "预览")
                btn.setStyleSheet(
                    "padding: 5px 14px; border-radius: 6px; "
                    "border: 1px solid #0066CC; color: #0066CC; background: white; "
                    "font-size: 11px; font-weight: 500;" if rep.status != 'previewed' else
                    "padding: 5px 14px; border-radius: 6px; border: 1px solid #C7C7CC; "
                    "color: #86868B; background: #F0F0F5; font-size: 11px;")
                btn.rep = rep
                btn.clicked.connect(lambda _, r=rep: self._on_preview(r))
                right.addWidget(btn)

                if rep.status == 'done':
                    st = QLabel("✓ 已上传")
                    st.setStyleSheet("font-size: 11px; color: #28C840; font-weight: 600;")
                elif rep.status == 'fail':
                    st = QLabel("✗ 上传失败")
                    st.setStyleSheet("font-size: 11px; color: #FF3B30;")
                elif rep.status == 'uploading':
                    st = QLabel("上传中...")
                    st.setStyleSheet("font-size: 11px; color: #0066CC;")
                else:
                    st = QLabel("待上传")
                    st.setStyleSheet("font-size: 11px; color: #86868B;")
                right.addWidget(st)

                bar = QProgressBar()
                bar.setFixedHeight(3)
                bar.setRange(0, 100)
                bar.setValue(100 if rep.status == 'done' else 0)
                bar.setStyleSheet(
                    "QProgressBar { background: #F0F0F5; border-radius: 2px; height: 3px; } "
                    "QProgressBar::chunk { background: #28C840; border-radius: 2px; }"
                    if rep.status == 'done' else
                    "QProgressBar { background: #F0F0F5; border-radius: 2px; height: 3px; } "
                    "QProgressBar::chunk { background: #0066CC; border-radius: 2px; }")
                right.addWidget(bar)
                right.addStretch()
                cl.addLayout(right)

            self.cards_grid.addWidget(card, i // 2, i % 2)

    def _on_preview(self, rep):
        subprocess.run(["open", str(rep.pdf_path)])
        rep.status = 'previewed'
        self._render_cards()

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
"""
SZU体检报告批量上传应用 - MainWindow (JPG版)
"""
import sys, os, time, json, re, subprocess, tempfile, ftplib, ssl
from pathlib import Path
from datetime import datetime

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = PdfWriter = None

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QProgressBar, QMessageBox,
    QInputDialog, QScrollArea, QApplication,
    QMainWindow, QFrame, QDialog, QLineEdit, QGridLayout as QGL,
    QDialogButtonBox, QFileDialog, QSizePolicy, QTableWidget,
    QTableWidgetItem, QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer, QEvent
from PyQt5.QtGui import QFont, QPalette, QBrush, QColor
from PyQt5.QtWidgets import QDesktopServices
from PyQt5.QtCore import QUrl

from src.config_manager import ConfigManager

CARDS_PER_PAGE = 6
CSP_PAGE_TPL = "https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode={urcode}&CurLocID=343"

FTP_CONFIG = {
    "host": "10.1.9.105",
    "port": 2121,
    "username": "dhccftp",
    "password": "Dhcc123!qwe",
}

DEVICE_LIST = [
    "人体成分分析4楼", "人体成分分析9楼",
    "肺功能", "肺功能9楼",
    "airdoc", "Airdoc 4楼",
    "动脉硬化检测仪", "动脉硬化检测仪9F",
    "肝纤维化扫描",
]

def _make_chrome_driver():
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium import webdriver
    opts = ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")
    prefs = {"download.prompt_for_behavior": False}
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

def _get_user_id(driver, user_id, loc, arcim):
    driver.get(CSP_PAGE_TPL.format(urcode="SZU06"))
    # 等 tkMakeServerCall 就绪，不盲等5秒
    import time as _t
    for _ in range(15):
        try:
            ready = driver.execute_script(
                "try { return typeof tkMakeServerCall === 'function'; } catch(e) { return false; }")
            if ready:
                break
        except Exception:
            pass
        _t.sleep(1)
    else:
        raise RuntimeError("页面加载超时")
    script = f"""
    try {{
        var r = tkMakeServerCall('web.DHCPE.Interface.Main','GetUserID','{user_id}','{loc}','{arcim}');
        return r;
    }} catch(e) {{
        return 'ERR:'+e.message;
    }}
    """
    result = driver.execute_script(script)
    if result and str(result).startswith("ERR"):
        raise RuntimeError(result)
    return result.strip()

def _save_upload_info(driver, ord_no, user_orditem_id, file_path):
    script = f"""
    try {{
        var r = tkMakeServerCall('web.DHCPE.Interface.Main','SaveUploadInfo',
            '{ord_no}','{user_orditem_id}','{file_path}');
        return r;
    }} catch(e) {{
        return 'ERR:'+e.message;
    }}
    """
    result = driver.execute_script(script)
    return result

def _save_result(driver, mrn, user_id, arcim_main, arcim_sub, cur_loc_id="343"):
    """
    调用 SaveResult，通知医嘱被执行
    签名: SaveResult(peRecNo, resultText, userInternalID, arcimMain, arcimSub, curLocID)
    """
    script = f"""
    try {{
        var r = tkMakeServerCall('web.DHCPE.Interface.Main','SaveResult',
            '{mrn}', '参见报告', '{user_id}', '{arcim_main}', '{arcim_sub}', '{cur_loc_id}');
        return r;
    }} catch(e) {{
        return 'ERR:'+e.message;
    }}
    """
    result = driver.execute_script(script)
    if result and str(result).startswith("ERR"):
        raise RuntimeError(result)
    return result.strip()

def _ensure_ftp_dir(ftps, *parts):
    """逐段创建并切换FTP目录，parts为"dhcpeftp","images","{ord_no}"等"""
    for seg in parts:
        try:
            ftps.cwd(seg)
        except ftplib.error_perm:
            try:
                ftps.mkd(seg)
                ftps.cwd(seg)
            except Exception:
                pass

def _do_ftps_clear_folder(ord_no):
    """清空 FTP 服务器上 dhcpeftp/images/{ord_no}/ 目录下的所有文件"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ftps = ftplib.FTP_TLS(context=ctx)
        ftps.connect(FTP_CONFIG["host"], FTP_CONFIG["port"], timeout=8)
        ftps.login(FTP_CONFIG["username"], FTP_CONFIG["password"])
        ftps.prot_p()
        _ensure_ftp_dir(ftps, "dhcpeftp", "images", str(ord_no))
        files = ftps.nlst()
        for f in files:
            if f not in ('.', '..'):
                try:
                    ftps.voidcmd(f"DELE {f}")
                except Exception:
                    pass
        ftps.quit()
    except Exception:
        pass

def _do_ftps_upload_single(ord_no, local_file, remote_name):
    """FTP上传单个文件（二进制 STOR），不清空目录"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftps = ftplib.FTP_TLS(context=ctx)
    ftps.connect(FTP_CONFIG["host"], FTP_CONFIG["port"], timeout=8)
    ftps.login(FTP_CONFIG["username"], FTP_CONFIG["password"])
    ftps.prot_p()
    _ensure_ftp_dir(ftps, "dhcpeftp", "images", str(ord_no))
    with open(local_file, "rb") as fp:
        ftps.storbinary(f"STOR {remote_name}", fp)
    ftps.quit()
    return True

def _do_ftps_clear_and_upload(ord_no, local_file, remote_name):
    """FTP上传（STODB二进制），目录已存在则先NLST再DELE再STOR"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftps = ftplib.FTP_TLS(context=ctx)
    ftps.connect(FTP_CONFIG["host"], FTP_CONFIG["port"], timeout=8)
    ftps.login(FTP_CONFIG["username"], FTP_CONFIG["password"])
    ftps.prot_p()
    _ensure_ftp_dir(ftps, "dhcpeftp", "images", str(ord_no))
    # 清空已有文件
    try:
        files = ftps.nlst()
        for f in files:
            if f not in ('.', '..'):
                try:
                    ftps.voidcmd(f"DELE {f}")
                except Exception:
                    pass
    except Exception:
        pass
    # 上传
    with open(local_file, "rb") as fp:
        ftps.storbinary(f"STOR {remote_name}", fp)
    ftps.quit()
    return True

def log_append(mrn, device, user_id, status, extra=""):
    try:
        from pathlib import Path
        log_path = Path(__file__).parent.parent / "upload_log.json"
        logs = []
        if log_path.exists():
            logs = json.loads(log_path.read_text())
        logs.append({
            "mrn": mrn, "device": device, "user_id": user_id,
            "status": status, "extra": extra,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        log_path.write_text(json.dumps(logs, ensure_ascii=False))
    except Exception:
        pass

# ============================================================
# 每日上传记录（仅记录当日成功上传）
# ============================================================
_daily_uploads = {}  # {(mrn, device_name): {"ord_no": ..., "user_id": ..., "time": ...}}

def _load_today_uploads():
    """启动时加载今日上传记录"""
    global _daily_uploads
    try:
        from pathlib import Path
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = Path(__file__).parent.parent / f"uploads_{today}.json"
        if log_path.exists():
            data = json.loads(log_path.read_text())
            for rec in data.get("records", []):
                key = (rec.get("mrn", ""), rec.get("device", ""))
                _daily_uploads[key] = rec
    except Exception:
        pass

def _save_today_upload(mrn, device, ord_no, user_id):
    """上传成功后写入当日记录"""
    try:
        from pathlib import Path
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = Path(__file__).parent.parent / f"uploads_{today}.json"
        try:
            data = json.loads(log_path.read_text()) if log_path.exists() else {"records": []}
        except Exception:
            data = {"records": []}
        # 覆盖同一患者+同一设备的记录
        key = (mrn, device)
        new_rec = {"mrn": mrn, "device": device, "ord_no": ord_no,
                   "user_id": user_id, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        # 移除旧的同key记录
        data["records"] = [r for r in data.get("records", [])
                          if (r.get("mrn"), r.get("device")) != key]
        data["records"].append(new_rec)
        log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        _daily_uploads[key] = new_rec
    except Exception:
        pass


# ============================================================
# 设置对话框
# ============================================================

class SettingsDialog(QDialog):
    def __init__(self, current_base_dir, current_output_dir, current_devices, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setFixedSize(480, 260)
        self._devices = list(current_devices)  # 复制

        gl = QGL(self)
        gl.setContentsMargins(24, 24, 24, 24)
        gl.setSpacing(16)

        # PDF目录
        gl.addWidget(QLabel("PDF报告文件夹:"), 0, 0)
        self.le_base = QLineEdit(current_base_dir)
        self.le_base.setPlaceholderText("/Users/jeffreykang/Documents/体检报告上传")
        gl.addWidget(self.le_base, 0, 1)
        btn_base = QPushButton("浏览...")
        btn_base.setFixedWidth(80)
        btn_base.clicked.connect(self._browse_base)
        gl.addWidget(btn_base, 0, 2)

        # 输出目录
        gl.addWidget(QLabel("合并PDF保存位置:"), 1, 0)
        self.le_out = QLineEdit(current_output_dir)
        self.le_out.setPlaceholderText("默认桌面")
        gl.addWidget(self.le_out, 1, 1)
        btn_out = QPushButton("浏览...")
        btn_out.setFixedWidth(80)
        btn_out.clicked.connect(self._browse_out)
        gl.addWidget(btn_out, 1, 2)

        # 设备配置
        gl.addWidget(QLabel("设备映射:"), 2, 0)
        dev_row = QHBoxLayout()
        dev_label = QLabel(f"{len(current_devices)} 个设备")
        dev_label.setStyleSheet("color: #666; font-size: 12px;")
        dev_row.addWidget(dev_label)
        dev_row.addStretch()
        btn_dev = QPushButton("设备配置...")
        btn_dev.setFixedWidth(90)
        btn_dev.clicked.connect(self._open_device_config)
        dev_row.addWidget(btn_dev)
        w = QWidget()
        w.setLayout(dev_row)
        gl.addWidget(w, 2, 1, 1, 2)

        # 按钮
        row3 = QHBoxLayout()
        row3.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("padding: 8px 20px; border: 1px solid #D1D1D6; border-radius: 8px; background: white; color: #333; font-size: 13px;")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("padding: 8px 20px; border: none; border-radius: 8px; background: #0066CC; color: white; font-size: 13px; font-weight: 600;")
        save_btn.clicked.connect(self._on_save)
        row3.addWidget(cancel_btn)
        row3.addWidget(save_btn)
        gl.addLayout(row3, 3, 0, 1, 3)

    def _browse_base(self):
        d = QFileDialog.getExistingDirectory(self, "选择PDF报告文件夹", self.le_base.text())
        if d:
            self.le_base.setText(d)

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存位置", self.le_out.text())
        if d:
            self.le_out.setText(d)

    def _open_device_config(self):
        dlg = DeviceConfigDialog(self._devices, self)
        if dlg.exec_() == QDialog.Accepted:
            self._devices = dlg.result_devices
            # 更新显示的设备数量
            for child in self.findChildren(QLabel):
                if "个设备" in child.text():
                    child.setText(f"{len(self._devices)} 个设备")
                    break

    def _on_save(self):
        self._base_dir = self.le_base.text().strip()
        self._output_dir = self.le_out.text().strip()
        self.accept()

    @property
    def base_dir(self):
        return getattr(self, '_base_dir', '')

    @property
    def output_dir(self):
        return getattr(self, '_output_dir', '')

    @property
    def devices(self):
        return getattr(self, '_devices', [])


# ============================================================
# 设备配置对话框
# ============================================================

class DeviceConfigDialog(QDialog):
    def __init__(self, devices, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设备配置")
        self.setModal(True)
        self.setFixedSize(560, 420)
        self.devices = devices  # [{"name": "", "urcode": "", "arcim": ""}]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # 表头
        header = QLabel("设备名称（需与PDF报告文件夹子文件夹名称一致）")
        layout.addWidget(header)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["设备名称", "URCode", "ARCIM"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 200)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._populate()
        layout.addWidget(self.table)

        # 添加/删除按钮
        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ 添加设备")
        add_btn.clicked.connect(self._add_row)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 确定/取消
        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("padding: 8px 20px; border: 1px solid #D1D1D6; border-radius: 8px; background: white; color: #333; font-size: 13px;")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("padding: 8px 20px; border: none; border-radius: 8px; background: #0066CC; color: white; font-size: 13px; font-weight: 600;")
        save_btn.clicked.connect(self._on_save)
        bottom.addWidget(cancel_btn)
        bottom.addWidget(save_btn)
        layout.addLayout(bottom)

    def _populate(self):
        self.table.setRowCount(len(self.devices))
        for i, dev in enumerate(self.devices):
            self.table.setItem(i, 0, QTableWidgetItem(dev.get("name", "")))
            self.table.setItem(i, 1, QTableWidgetItem(dev.get("urcode", "")))
            self.table.setItem(i, 2, QTableWidgetItem(dev.get("arcim", "")))

    def _add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)

    def _delete_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _on_save(self):
        self.devices = []
        for i in range(self.table.rowCount()):
            name = (self.table.item(i, 0) or QTableWidgetItem("")).text().strip()
            urcode = (self.table.item(i, 1) or QTableWidgetItem("")).text().strip()
            arcim = (self.table.item(i, 2) or QTableWidgetItem("")).text().strip()
            if name:
                self.devices.append({"name": name, "urcode": urcode, "arcim": arcim})
        self.accept()

    @property
    def result_devices(self):
        return self.devices


# ============================================================
# 医嘱号获取线程（无阻塞，在卡片上显示进度）
# ============================================================

class OrdFetchWorker(QThread):
    """
    后台获取 ord_no，不弹窗，实时更新 rep.ord_status / rep.ord_no
    信号: progress(mrn, device, ord_no, status_msg)
          done(mrn)
    """
    progress = pyqtSignal(str, str, str, str)  # mrn, device, ord_no, status_msg
    done = pyqtSignal(str)  # mrn

    def __init__(self, driver, patient, user_id, parent=None):
        super().__init__(parent)
        self.driver = driver
        self.patient = patient
        self.user_id = user_id
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        mrn = self.patient.mrn
        reports = self.patient.reports

        for rep in reports:
            if self._cancelled:
                rep.ord_status = 'cancelled'
                self.progress.emit(mrn, rep.device_name, rep.ord_no or '', '已取消')
                continue

            # 已有有效ord_no则跳过
            if rep.ord_no and not str(rep.ord_no).startswith('ERR') and not str(rep.ord_no).startswith('ERR'):
                rep.ord_status = 'found'
                self.progress.emit(mrn, rep.device_name, rep.ord_no, f'医嘱号: {rep.ord_no}')
                continue

            if not rep.urcode:
                rep.ord_status = 'error'
                rep.ord_no = 'ERR_NO_URCODE'
                self.progress.emit(mrn, rep.device_name, rep.ord_no, '无URCODE')
                continue

            rep.ord_status = 'fetching'
            self.progress.emit(mrn, rep.device_name, '', '获取中...')

            try:
                if self.driver is None:
                    self.driver = _make_chrome_driver()
                    self.driver.set_page_load_timeout(15)
                    self.driver.get(CSP_PAGE_TPL.format(urcode=rep.urcode))

                # Wait until tkMakeServerCall is actually callable (not just declared)
                import time as _time
                for _ in range(15):  # up to 15s
                    try:
                        ready = self.driver.execute_script(
                            "try { return typeof tkMakeServerCall === 'function'; } catch(e) { return false; }"
                        )
                        if ready:
                            break
                    except Exception:
                        pass
                    _time.sleep(1)
                else:
                    rep.ord_no = "ERR_TIMEOUT"
                    rep.ord_status = 'error'
                    self.progress.emit(mrn, rep.device_name, rep.ord_no, '页面加载超时')
                    self.done.emit(mrn)
                    return

                script = f"""
                try {{
                    var arcim_main = '{rep.arcim}'.split('^')[0];
                    var r = tkMakeServerCall('web.DHCPE.Interface.Main', 'GetBaseInfo',
                                  '{mrn}', arcim_main, 'HPNo', '343');
                    return r;
                }} catch(e) {{
                    return 'ERR:' + e.message;
                }}
                """
                result = self.driver.execute_script(script)
                if result and not str(result).startswith("ERR") and result != "NoHP":
                    fields = result.split("^")
                    if len(fields) >= 9:
                        rep.ord_no = fields[7].strip()
                        rep.arcim_sub = fields[8].strip()
                        rep.ord_status = 'found'
                        self.progress.emit(mrn, rep.device_name, rep.ord_no, f'医嘱号: {rep.ord_no}')
                        log_append(mrn, rep.device_name, self.user_id, "ord_found", rep.ord_no)
                    else:
                        rep.ord_no = "ERR_PARSE"
                        rep.ord_status = 'error'
                        self.progress.emit(mrn, rep.device_name, rep.ord_no, '解析失败')
                else:
                    rep.ord_no = f"ERR_{result or 'NoHP'}"
                    rep.ord_status = 'error'
                    self.progress.emit(mrn, rep.device_name, rep.ord_no, '无医嘱')
            except Exception as e:
                rep.ord_no = f"ERR:{e}"
                rep.ord_status = 'error'
                self.progress.emit(mrn, rep.device_name, rep.ord_no, '获取失败')
                log_append(mrn, rep.device_name, self.user_id, "ord_fail", str(e))

        self.done.emit(mrn)

    def cleanup(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.driver = None


# ============================================================
# 已上传确认对话框
# ============================================================

class AlreadyUploadedDialog(QDialog):
    def __init__(self, mrn, device_name, ord_no, parent=None):
        super().__init__(parent)
        self.setWindowTitle("医嘱已上传")
        self.setModal(True)
        self.setFixedSize(420, 200)

        fl = QVBoxLayout(self)
        fl.setContentsMargins(28, 24, 28, 20)
        fl.setSpacing(12)

        lbl = QLabel(f'患者 <b>{mrn}</b> 的检查项目 <b>{device_name}</b><br>'
                     f'医嘱号: {ord_no}<br><br>'
                     f'服务器上已有上传记录，是否删除已上传的报告重新上传？')
        lbl.setStyleSheet("font-size: 13px; color: #1D1D1F; line-height: 1.6;")
        lbl.setWordWrap(True)
        fl.addWidget(lbl)

        fl.addStretch()

        btns = QHBoxLayout()
        btns.setSpacing(12)
        skip_btn = QPushButton("跳过这份")
        skip_btn.setStyleSheet("padding: 9px 20px; border: 1px solid #D1D1D6; border-radius: 8px; "
                               "background: white; color: #333; font-size: 13px;")
        skip_btn.clicked.connect(self.reject)
        replace_btn = QPushButton("确定删除并重新上传")
        replace_btn.setStyleSheet("padding: 9px 20px; border: none; border-radius: 8px; "
                                  "background: #FF3B30; color: white; font-size: 13px; font-weight: 600;")
        replace_btn.clicked.connect(self.accept)
        btns.addWidget(skip_btn)
        btns.addWidget(replace_btn)
        fl.addLayout(btns)


# ============================================================
# 主窗口
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SZU体检检查报告批量上传")
        self.resize(900, 700)
        self.setMinimumWidth(900)
        self.setMinimumHeight(700)
        self.setWindowFlags(Qt.Window)

        self.config = ConfigManager().load()
        # 兼容旧路径
        if not os.path.exists(self.config.get('base_dir', '')):
            alt = '/Users/jeffreykang/Documents/体检报告上传'
            if os.path.exists(alt):
                self.config['base_dir'] = alt
        self._apply_device_map()
        self.patients = []
        self.current_idx = 0
        self.current_page = 0
        self._ord_fetch_worker = None
        self._upload_workers = {}  # mrn -> UploadWorker
        self._pending_uploads = {}  # mrn -> user_id
        self._destroyed = False  # 防止窗口销毁后回调崩溃

        # 应用启动后自动扫描一次
        QTimer.singleShot(200, self.start_scan)
        _load_today_uploads()

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Body (flex) ----
        body = QWidget()
        body.setMinimumHeight(500)
        body.setStyleSheet("background: #F5F5F7;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 12)

        # 上部：左侧患者列表 + 右侧卡片区
        top = QWidget()
        top.setStyleSheet("background: transparent;")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(12)

        # 左侧患者列表
        left = QWidget()
        left.setFixedWidth(190)
        left.setStyleSheet("background: white; border-radius: 12px;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 8, 0, 8)

        lt = QLabel("患者列表")
        lt.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #86868B; "
            "text-transform: uppercase; letter-spacing: 0.6px; padding: 8px 16px 6px;")
        ll.addWidget(lt)

        self.patient_list = QListWidget()
        self.patient_list.setStyleSheet("""
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget::item { padding: 0; min-height: 44px; }
            QListWidget::item:selected { background: #E8F4FF; border-left: 3px solid #0066CC; }
            QListWidget::item:hover { background: #F0F0F5; }
        """)
        self.patient_list.itemClicked.connect(self._on_patient_clicked)
        self.patient_list.installEventFilter(self)
        ll.addWidget(self.patient_list)

        tl.addWidget(left)

        # 右侧卡片区
        right = QWidget()
        right.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        # 卡片网格（2列）
        cards_wrap = QWidget()
        cards_wrap.setStyleSheet("background: white; border-radius: 12px; padding: 8px;")
        cards_wrap.setMinimumHeight(400)
        cw = QVBoxLayout(cards_wrap)
        cw.setContentsMargins(10, 10, 10, 10)
        cw.setSpacing(8)

        self.cards_grid = QGridLayout()
        self.cards_grid.setSpacing(8)
        cw.addLayout(self.cards_grid)
        cw.addStretch()  # push cards to top

        # 患者标题
        self.patient_hdr = QLabel("")
        self.patient_hdr.setFixedHeight(24)
        self.patient_hdr.setStyleSheet("font-size: 14px; font-weight: 600; color: #1D1D1F; "
                                       "background: transparent;")
        rl.addWidget(self.patient_hdr)
        rl.addWidget(cards_wrap, 1)

        tl.addWidget(right, 1)
        bl.addWidget(top, 1)

        root.addWidget(body)

        # ---- Bottom bar ----
        btm = QWidget()
        btm.setFixedHeight(50)
        btm.setStyleSheet("background: white; border-top: 1px solid #F0F0F0;")
        btml = QHBoxLayout(btm)
        btml.setContentsMargins(16, 10, 16, 10)
        btml.setSpacing(12)

        # 设置按钮
        self.btn_settings = QPushButton("⚙ 设置")
        self.btn_settings.setStyleSheet(
            "padding: 8px 16px; border: 1px solid #D1D1D6; border-radius: 8px; "
            "background: white; color: #333; font-size: 12px;")
        self.btn_settings.clicked.connect(self._on_settings)
        btml.addWidget(self.btn_settings)

        self.lbl_last_upload = QLabel("")
        self.lbl_last_upload.setStyleSheet("font-size: 11px; color: #86868B;")
        btml.addWidget(self.lbl_last_upload)

        # 翻页导航 - 居中
        page_nav = QWidget()
        page_nav.setStyleSheet("background: transparent;")
        pnl = QHBoxLayout(page_nav)
        pnl.setContentsMargins(0, 0, 0, 0)
        pnl.setSpacing(4)
        self.btn_prev = QPushButton("‹")
        self.btn_prev.setFixedWidth(28)
        self.btn_prev.setStyleSheet(
            "border: 1px solid #D1D1D6; border-radius: 4px; "
            "font-size: 16px; color: #333; padding: 2px 6px; background: white;")
        self.btn_prev.clicked.connect(self._on_prev)
        pnl.addWidget(self.btn_prev)
        self.lbl_page = QLabel("")
        self.lbl_page.setStyleSheet("font-size: 12px; color: #86868B; min-width: 60px; text-align: center;")
        self.lbl_page.setAlignment(Qt.AlignCenter)
        pnl.addWidget(self.lbl_page)
        self.btn_next = QPushButton("›")
        self.btn_next.setFixedWidth(28)
        self.btn_next.setStyleSheet(
            "border: 1px solid #D1D1D6; border-radius: 4px; "
            "font-size: 16px; color: #333; padding: 2px 6px; background: white;")
        self.btn_next.clicked.connect(self._on_next)
        pnl.addWidget(self.btn_next)
        # 插入 stretch 把 page_nav 推到横向居中
        btml.insertStretch(1)
        btml.addWidget(page_nav)

        btml.addStretch()

        self.btn_upload = QPushButton("上传到东华 + 合并PDF")
        self.btn_upload.setStyleSheet(
            "background: #0066CC; color: white; border: none; border-radius: 8px; "
            "padding: 9px 20px; font-size: 13px; font-weight: 600;")
        self.btn_upload.clicked.connect(self._on_batch_upload)
        btml.addWidget(self.btn_upload)

        root.addWidget(btm)

        # ---- Version bar ----
        ver = QLabel("V1.0 - Proudly developed by Jeffrey Kang, Shenzhen New Frontier United Family Hospital")
        ver.setFixedHeight(24)
        ver.setStyleSheet("background: #FAFAFA; font-size: 10px; color: #C7C7CC; padding: 0;")
        ver.setAlignment(Qt.AlignCenter)
        root.addWidget(ver)

    # ---- scan ----

    def start_scan(self):
        self.statusBar().showMessage("正在扫描...")
        self.scan_worker = ScanWorker(self.config['base_dir'])
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.start()

    def _on_scan_progress(self, msg, pct):
        self.statusBar().showMessage(f"扫描中... {msg}")

    def _on_scan_finished(self, patients):
        if self._destroyed:
            return

        # 补充：今日日志里有上传记录但文件已删除的患者
        existing_mrns = {p.mrn for p in patients}
        for (mrn, device), rec in _daily_uploads.items():
            rr = ReportRecord.__new__(ReportRecord)
            rr.pdf_path = Path("")
            rr.device_name = rec.get("device", device)
            rr.ord_no = rec.get("ord_no")
            rr.arcim = None
            rr.arcim_sub = None
            rr.urcode = None
            rr.status = 'done'
            rr.progress = 100
            rr.error_msg = ''
            rr.log = []
            rr.ord_status = 'found'
            if mrn in existing_mrns:
                # 合并到已有患者
                for p in patients:
                    if p.mrn == mrn:
                        p.reports.append(rr)
                        break
            else:
                # 新建患者
                patient = PatientRecord(mrn)
                patient.reports.append(rr)
                patients.append(patient)
                existing_mrns.add(mrn)

        patients = sorted(patients, key=lambda p: p.mrn)
        self.patients = patients
        self.statusBar().showMessage(f"共 {len(patients)} 位患者，已扫描完成")
        try:
            self._refresh_patient_list()
            if patients:
                self.current_idx = 0
                self.current_page = 0
                self._show_patient(0)
        except RuntimeError:
            pass

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Up:
            if self.current_idx > 0:
                self.current_idx -= 1
                self.current_page = 0
                self._navigate_to_patient(self.current_idx)
        elif key == Qt.Key_Down:
            if self.current_idx + 1 < len(self.patients):
                self.current_idx += 1
                self.current_page = 0
                self._navigate_to_patient(self.current_idx)
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.patient_list and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Up:
                if self.current_idx > 0:
                    self.current_idx -= 1
                    self.current_page = 0
                    self._navigate_to_patient(self.current_idx)
                return True
            elif key == Qt.Key_Down:
                if self.current_idx + 1 < len(self.patients):
                    self.current_idx += 1
                    self.current_page = 0
                    self._navigate_to_patient(self.current_idx)
                return True
        return super().eventFilter(obj, event)

    def _navigate_to_patient(self, idx):
        """上下键切换患者：选中列表行 + 显示卡片"""
        self.patient_list.setCurrentRow(idx)
        self._show_patient(idx)

    def _refresh_patient_list(self):
        try:
            self.patient_list.clear()
            for i, p in enumerate(self.patients):
                item = QListWidgetItem()
                item.setData(Qt.UserRole, i)
                self.patient_list.addItem(item)
                self.patient_list.setItemWidget(item, self._patient_item_widget(p))
        except RuntimeError:
            pass

    def _patient_item_widget(self, patient):
        w = QWidget()
        w.setFixedHeight(52)
        w.setStyleSheet("background: transparent;")
        l = QHBoxLayout(w)
        l.setContentsMargins(14, 0, 36, 0)
        l.setSpacing(8)

        lbl = QLabel(patient.mrn)
        lbl.setStyleSheet("font-size: 13px; color: #1D1D1F; font-weight: 500;")
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        l.addWidget(lbl)

        spacer = QWidget()
        spacer.setFixedWidth(8)
        l.addWidget(spacer)
        l.addStretch()

        st = patient.aggregate_status
        # 如果所有报告都有今日上传记录（跨会话持久化），视为已完成
        all_done_today = all(
            (patient.mrn, rep.device_name) in _daily_uploads
            for rep in patient.reports
        )
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            "background: #34C759; border-radius: 4px;" if (st == "done" or all_done_today) else
            "background: #FF9500; border-radius: 4px;" if st == "uploading" else
            "background: #FF3B30; border-radius: 4px;" if st == "fail" else
            "background: #C7C7CC; border-radius: 4px;"
        )
        l.addWidget(dot)

        count_lbl = QLabel(f"{len(patient.reports)}份")
        count_lbl.setStyleSheet("font-size: 10px; color: #86868B;")
        count_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        l.addWidget(count_lbl)

        return w

    def _update_patient_list_items(self):
        try:
            for i in range(self.patient_list.count()):
                item = self.patient_list.item(i)
                idx = item.data(Qt.UserRole)
                if idx is not None and 0 <= idx < len(self.patients):
                    patient = self.patients[idx]
                    self.patient_list.setItemWidget(item, self._patient_item_widget(patient))
        except RuntimeError:
            pass

    def _on_patient_clicked(self, item):
        idx = item.data(Qt.UserRole)
        if idx is None:
            idx = self.patient_list.row(item)
        if 0 <= idx < len(self.patients):
            self.current_idx = idx
            self.current_page = 0
            self._show_patient(idx)

    def _show_patient(self, idx):
        if idx < 0 or idx >= len(self.patients) or self._destroyed:
            return
        try:
            patient = self.patients[idx]
            total_pages = (len(patient.reports) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE
            if self.current_page >= total_pages:
                self.current_page = 0
            self._render_cards()
            self._update_page_nav()
            self.patient_hdr.setText(f"患者: {patient.mrn}  ({len(patient.reports)}份报告)")
            # Highlight selected patient
            for i in range(self.patient_list.count()):
                item = self.patient_list.item(i)
                item.setBackground(QColor('transparent') if i != idx else QColor('#E8F4FF'))
        except RuntimeError as e:
            print(f"[_show_patient] RuntimeError: {e}")
            pass

    def _update_page_nav(self):
        if self._destroyed:
            return
        try:
            patient = self.patients[self.current_idx]
            total_cards = len(patient.reports)
            card_page = self.current_page
            card_total = max(1, (total_cards + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
            total_patients = len(self.patients)

            # 跨患者翻页：prev 在第一张卡片页且有上一个患者时也能点
            self.btn_prev.setEnabled(card_page > 0 or self.current_idx > 0)
            # 跨患者翻页：next 在最后一张卡片页且有下一个患者时也能点
            self.btn_next.setEnabled(card_page < card_total - 1 or self.current_idx + 1 < total_patients)

            if total_patients > 0:
                self.lbl_page.setText(f"{self.current_idx + 1}/{total_patients}")
            else:
                self.lbl_page.setText("")
        except RuntimeError:
            pass

    def _on_prev(self):
        if not self.patients:
            return
        total = len(self.patients[self.current_idx].reports)
        total_pages = max(1, (total + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
        if self.current_page > 0:
            self.current_page -= 1
        else:
            # 已在第一页，跳到上一个患者的最后一页
            if self.current_idx > 0:
                self.current_idx -= 1
                prev_total = len(self.patients[self.current_idx].reports)
                self.current_page = max(0, (prev_total - 1 + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
            else:
                return
        self._show_patient(self.current_idx)

    def _on_next(self):
        if not self.patients:
            return
        total = len(self.patients[self.current_idx].reports)
        total_pages = max(1, (total + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
        if self.current_page < total_pages - 1:
            self.current_page += 1
        else:
            # 已在最后一页，跳到下一个患者的第一页
            if self.current_idx + 1 < len(self.patients):
                self.current_idx += 1
                self.current_page = 0
            else:
                return
        self._show_patient(self.current_idx)

    def _render_cards(self):
        if not self.patients or self.current_idx >= len(self.patients):
            return
        patient = self.patients[self.current_idx]
        reports = patient.reports
        mrn = patient.mrn

        while self.cards_grid.count():
            child = self.cards_grid.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()

        start = self.current_page * CARDS_PER_PAGE
        page_reports = reports[start:start + CARDS_PER_PAGE]
        padded = page_reports + [None] * (CARDS_PER_PAGE - len(page_reports))

        for i, rep in enumerate(padded):
            card = QFrame()
            card.setFixedHeight(108)
            if rep is None:
                card.setStyleSheet("background: transparent; border: none;")
                el = QVBoxLayout(card)
                el.setContentsMargins(0, 0, 0, 0)
            else:
                if rep.status == 'previewed':
                    card.setStyleSheet("background: #FAFCFF; border: 1px solid #CCE4FF; border-radius: 8px;")
                elif rep.status == 'uploading':
                    card.setStyleSheet("background: #FFF9F0; border: 1px solid #FFE0A0; border-radius: 8px;")
                else:
                    card.setStyleSheet("background: white; border: 1px solid #E8E8ED; border-radius: 8px;")

                # Plan B: 3-column fixed layout, no stretch
                cl = QHBoxLayout(card)
                cl.setContentsMargins(10, 8, 10, 8)
                cl.setSpacing(8)

                # Left: thumbnail (fixed 34x48)
                thumb = QLabel("📄")
                thumb.setFixedSize(34, 48)
                thumb.setStyleSheet(
                    "background: #F5F5F7; border: 1px solid #E0E0E4; border-radius: 4px; font-size: 18px;")
                thumb.setAlignment(Qt.AlignCenter)
                cl.addWidget(thumb)

                # Middle: device + order (no stretch, top-aligned, expand to fill)
                mid = QVBoxLayout()
                mid.setSpacing(4)
                mid.setContentsMargins(0, 0, 0, 0)
                mid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

                dv = QLabel(rep.display_device)
                dv.setStyleSheet("font-size: 12px; font-weight: 600; color: #1D1D1F; padding: 0px;")
                dv.setTextInteractionFlags(Qt.TextSelectableByMouse)
                mid.addWidget(dv)

                if rep.ord_status == 'fetching':
                    ol = QLabel("获取中...")
                    ol.setStyleSheet("font-size: 11px; color: #FF9500; padding: 0px;")
                elif rep.ord_no and str(rep.ord_no).startswith("ERR"):
                    ol = QLabel(f"医嘱号: {rep.ord_no}")
                    ol.setStyleSheet(
                        "font-size: 11px; color: #FF3B30; background: #FFE8E8; "
                        "padding: 1px 6px; border-radius: 6px;")
                elif rep.ord_no:
                    ol = QLabel(f"医嘱号: {rep.ord_no}")
                    ol.setStyleSheet("font-size: 11px; color: #555555; padding: 0px;")
                else:
                    ol = QLabel("医嘱号: 待获取")
                    ol.setStyleSheet("font-size: 11px; color: #86868B; padding: 0px;")
                mid.addWidget(ol)

                # 今日上传信息
                key = (mrn, rep.device_name)
                info = _daily_uploads.get(key)
                if info:
                    il = QLabel(f"今日 {info['time'][11:]} {info['user_id']} {info['ord_no']}")
                    il.setStyleSheet("font-size: 10px; color: #34C759; padding: 0px;")
                    mid.addWidget(il)

                # Let mid stretch to fill remaining space (stays top-aligned)
                mid.addStretch()
                cl.addLayout(mid, 1)

                # Right: button + status + bar (fixed width, top-aligned)
                right = QVBoxLayout()
                right.setSpacing(3)
                right.setContentsMargins(0, 0, 0, 0)
                right.setAlignment(Qt.AlignTop | Qt.AlignLeft)

                btn = QPushButton("已预览" if rep.status == 'previewed' else "预览")
                if rep.status != 'previewed':
                    btn.setStyleSheet(
                        "padding: 3px 10px; border-radius: 5px; "
                        "border: 1px solid #0066CC; color: #0066CC; background: white; "
                        "font-size: 10px; font-weight: 500;")
                else:
                    btn.setStyleSheet(
                        "padding: 3px 10px; border-radius: 5px; border: 1px solid #C7C7CC; "
                        "color: #86868B; background: #F0F0F5; font-size: 10px;")
                btn.rep = rep
                btn.clicked.connect(lambda _, r=rep: self._on_preview(r))
                right.addWidget(btn)

                if rep.status == 'done':
                    st = QLabel("✓ 已上传")
                    st.setStyleSheet("font-size: 10px; color: #28C840; font-weight: 600; padding: 0px;")
                elif rep.status == 'fail':
                    st = QLabel("✗ 上传失败")
                    st.setStyleSheet("font-size: 10px; color: #FF3B30; padding: 0px;")
                elif rep.status == 'uploading':
                    st = QLabel("上传中...")
                    st.setStyleSheet("font-size: 10px; color: #FF9500; font-weight: 600; padding: 0px;")
                elif rep.status == 'cancel':
                    st = QLabel("已取消")
                    st.setStyleSheet("font-size: 10px; color: #86868B; padding: 0px;")
                else:
                    st = QLabel("待上传")
                    st.setStyleSheet("font-size: 10px; color: #86868B; padding: 0px;")
                right.addWidget(st)

                bar = QProgressBar()
                bar.setFixedHeight(2)
                bar.setRange(0, 100)
                if rep.status == 'done':
                    bar.setValue(100)
                elif rep.status == 'uploading':
                    bar.setValue(50)
                else:
                    bar.setValue(0)
                bar.setStyleSheet(
                    "QProgressBar { border: none; background: #F0F0F0; border-radius: 1px; } "
                    "QProgressBar::chunk { background: #0066CC; border-radius: 1px; }")
                right.addWidget(bar)

                cl.addLayout(right)

            self.cards_grid.addWidget(card, i // 2, i % 2)

    def _on_preview(self, rep):
        url = QUrl.fromLocalFile(str(rep.pdf_path))
        QDesktopServices.openUrl(url)
        if rep.status != 'done':
            rep.status = 'previewed'
        self._render_cards()

    # ---- settings ----

    def _apply_device_map(self):
        """将 config 中的 devices 转换为运行时映射格式并应用"""
        global _RUNTIME_DEVICE_MAP
        cfg = self.config.get('devices', [])
        if not cfg:
            _RUNTIME_DEVICE_MAP = []
            return
        # config 格式: [{"name": "肺功能", "urcode": "SZU04", "arcim": "592||1"}, ...]
        # 转为: [{"devices": ["肺功能"], "urcode": "SZU04", "arcim": "592||1"}, ...]
        _RUNTIME_DEVICE_MAP = [
            {"devices": [d["name"]], "urcode": d.get("urcode", ""), "arcim": d.get("arcim", "")}
            for d in cfg if d.get("name", "").strip()
        ]

    def _on_settings(self):
        dlg = SettingsDialog(
            self.config.get('base_dir', ''),
            self.config.get('desktop_output_dir', ''),
            self.config.get('devices', []),
            self
        )
        if dlg.exec_() == QDialog.Accepted:
            if dlg.base_dir:
                self.config['base_dir'] = dlg.base_dir
            if dlg.output_dir:
                self.config['desktop_output_dir'] = dlg.output_dir
            if dlg.devices:
                self.config['devices'] = dlg.devices
                self._apply_device_map()
            ConfigManager().save(self.config)
            QMessageBox.information(self, "提示", "设置已保存。下次扫描时会使用新的文件夹路径。")

    # ---- upload flow ----

    def _on_batch_upload(self):
        if not self.patients:
            QMessageBox.warning(self, "提示", "没有报告可上传")
            return
        total = len(self.patients[self.current_idx].reports)
        dlg = ConfirmDialog(self.patients[self.current_idx].mrn, total, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        user_id = dlg.user_id
        self._fetch_ord_and_upload(user_id)

    def _fetch_ord_and_upload(self, user_id_input):
        """启动上传worker（ord_no获取和上传都在worker内顺序执行，不阻塞主线程）"""
        current = self.patients[self.current_idx] if 0 <= self.current_idx < len(self.patients) else None
        if not current:
            QMessageBox.warning(self, "提示", "没有选中的患者")
            return

        if not any(r.urcode for r in current.reports):
            QMessageBox.warning(self, "提示", "没有可上传的报告（无URCODE）")
            return

        # 签名后立即刷新UI：所有报告立刻切到"上传中"
        for rep in current.reports:
            rep.status = "uploading"
            rep.progress = 0
        self._render_cards()
        QApplication.processEvents()

        # 每报告独立 worker，互相并行
        mrn = current.mrn
        worker = UploadWorker(current, user_id_input)
        worker.progress.connect(self._on_upload_progress)
        worker.report_done.connect(self._on_upload_report_done)
        worker.finished.connect(lambda m: self._on_upload_finished(m, user_id_input))
        self._upload_workers[mrn] = worker
        worker.start()
        self._update_patient_list_items()

        # 进度条脉冲动画
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._animate_progress_pulse)
        self._pulse_timer.start(400)

    def _on_ord_fetch_progress(self, mrn, device, ord_no, status_msg):
        """ord_fetch 实时更新卡片上的医嘱号显示"""
        self._render_cards()

    def _on_ord_fetch_done(self, mrn, user_id_input):
        """ord_fetch 完成后，开始上传流程"""
        if hasattr(self, '_ord_fetch_timer'):
            self._ord_fetch_timer.stop()
        driver = self._ord_fetch_worker.driver if self._ord_fetch_worker else None
        self._ord_fetch_worker = None
        self._do_upload(user_id_input)

    def _do_upload(self, user_id_input):
        """启动并行上传：每报告独立worker，签名后显示进度遮罩"""
        current = self.patients[self.current_idx] if 0 <= self.current_idx < len(self.patients) else None
        if not current:
            return

        if not any(r.urcode for r in current.reports):
            QMessageBox.warning(self, "提示", "没有可上传的报告（无URCODE）")
            return

        # 签名后立即刷新UI：所有报告立刻切到"上传中"，无需等待worker信号
        for rep in current.reports:
            rep.status = "uploading"
            rep.progress = 0
        self._render_cards()
        QApplication.processEvents()

        # 每报告独立 worker，互相并行
        mrn = current.mrn
        worker = UploadWorker(current, user_id_input)
        worker.progress.connect(self._on_upload_progress)
        worker.report_done.connect(self._on_upload_report_done)
        worker.finished.connect(lambda m: self._on_upload_finished(m, user_id_input))
        self._upload_workers[mrn] = worker
        worker.start()
        self._update_patient_list_items()

        # 进度条脉冲动画，让卡片有动态感
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._animate_progress_pulse)
        self._pulse_timer.start(400)

    def _animate_progress_pulse(self):
        """进度条样式脉冲：正在上传的报告进度条在蓝/橙之间切换，提示动态进行中"""
        if not hasattr(self, 'cards_wrap'):
            return
        if not hasattr(self, '_pulse_state'):
            self._pulse_state = False
        self._pulse_state = not self._pulse_state
        # 直接修改当前卡片页已有控件的样式，不重建
        cards = self.cards_wrap.findChildren(QWidget)
        # 进度条 chunk 颜色：蓝(活跃) / 橙(脉冲)
        color = "#FF9500" if self._pulse_state else "#0066CC"
        for card in cards:
            for child in card.findChildren(QProgressBar):
                child.setStyleSheet(
                    f"QProgressBar {{ border: none; background: #F0F0F0; border-radius: 1px; }} "
                    f"QProgressBar::chunk {{ background: {color}; border-radius: 1px; }}")
            for child in card.findChildren(QLabel):
                if child.text() == "上传中...":
                    # 文字脉冲：正常 / 加粗
                    child.setStyleSheet(
                        "font-size: 10px; color: #FF9500; "
                        + ("font-weight: 700; padding: 0px;" if self._pulse_state else "font-weight: 600; padding: 0px;"))

    def _show_upload_overlay(self):
        """签名后立即显示半透明进度遮罩"""
        if hasattr(self, '_upload_overlay') and self._upload_overlay:
            return
        overlay = QWidget(self.centralWidget())
        overlay.setStyleSheet("background: rgba(0,0,0,0.45);")
        overlay.setGeometry(0, 0, self.width(), self.height())
        lbl = QLabel(overlay)
        lbl.setStyleSheet("color: white; font-size: 16px; font-weight: 600;")
        lbl.setText("上传中，请稍候...")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.resize(300, 50)
        lbl.move(overlay.width() // 2 - 150, overlay.height() // 2 - 25)
        overlay.show()
        self._upload_overlay = overlay

    def _hide_upload_overlay(self):
        if hasattr(self, '_upload_overlay') and self._upload_overlay:
            self._upload_overlay.close()
            self._upload_overlay = None

    def _ftp_dir_exists(self, ord_no):
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ftps = ftplib.FTP_TLS(context=ctx)
            ftps.connect(FTP_CONFIG["host"], FTP_CONFIG["port"], timeout=8)
            ftps.login(FTP_CONFIG["username"], FTP_CONFIG["password"])
            ftps.prot_p()
            ftps.cwd("dhcpeftp")
            ftps.cwd("images")
            ftps.cwd(str(ord_no))
            files = ftps.nlst()
            ftps.quit()
            return len([f for f in files if f not in ('.', '..')]) > 0
        except Exception:
            return False

    def _on_upload_progress(self, ord_no, pct):
        for patient in self.patients:
            for rep in patient.reports:
                if rep.ord_no == ord_no:
                    rep.progress = pct
                    self._render_cards()
                    return

    def _on_upload_report_done(self, mrn, rep, success, err):
        self._render_cards()
        self._update_patient_list_items()

    def _on_upload_finished(self, mrn, user_id):
        if hasattr(self, '_pulse_timer') and self._pulse_timer:
            self._pulse_timer.stop()
            self._pulse_timer = None
        if mrn in self._upload_workers:
            del self._upload_workers[mrn]

        for patient in self.patients:
            if patient.mrn == mrn:
                patient.refresh_aggregate_status()
                break

        self._update_patient_list_items()
        self._render_cards()

        self.lbl_last_upload.setText(f"今日已上传: {datetime.now().strftime('%H:%M')}")
        self._merge_to_desktop(user_id)
        self.statusBar().showMessage(f"患者 {mrn} 上传完成")

    def _merge_to_desktop(self, user_id):
        output_dir = self.config.get('desktop_output_dir', '')
        desktop = Path(output_dir) if output_dir else Path.home() / "Desktop"
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
        self._destroyed = True
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.quit()
            self.scan_worker.wait(3000)
        if self._ord_fetch_worker:
            self._ord_fetch_worker.cancel()
            self._ord_fetch_worker.quit()
        for worker in list(self._upload_workers.values()):
            worker.cancel()
            worker.quit()
        if hasattr(self, '_active_driver') and self._active_driver:
            try:
                self._active_driver.quit()
            except Exception:
                pass
        event.accept()


# ============================================================
# 签名确认对话框（从 PDF 版 app 复制）
# ============================================================

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

        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setObjectName("header")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.addWidget(QLabel("确认上传"))
        hl.addStretch()
        lay.addWidget(hdr)

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


# ============================================================
# 扫描线程
# ============================================================

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


# ============================================================
# PatientRecord
# ============================================================

class PatientRecord:
    def __init__(self, mrn):
        self.mrn = mrn
        self.reports = []

    def add_report(self, pdf_path, device_name):
        self.reports.append(ReportRecord(pdf_path, device_name))

    @property
    def aggregate_status(self):
        if all(r.status == 'done' for r in self.reports):
            return 'done'
        if any(r.status == 'uploading' for r in self.reports):
            return 'uploading'
        if any(r.status == 'fail' for r in self.reports):
            return 'fail'
        return 'pending'

    def refresh_aggregate_status(self):
        pass


# ============================================================
# ReportRecord - 单个报告的数据类
# ============================================================

class ReportRecord:
    def __init__(self, pdf_path, device_name):
        self.pdf_path = Path(pdf_path)
        self.device_name = device_name
        self.ord_no = None        # 医嘱号（从GetBaseInfo获取）
        self.arcim = None         # 项目ID
        self.arcim_sub = None     # 子项目ID
        self.urcode = None        # URCODE（设备→URCODE映射得到）
        self.status = 'pending'   # pending | previewed | uploading | done | fail | cancel
        self.progress = 0         # 0~100
        self.error_msg = ''
        self.ord_status = ''      # fetching | found | error | ''
        self._resolve_device()

    def _resolve_device(self):
        # device_name → urcode / arcim（从运行时映射或默认映射）
        for row in _get_device_map():
            if self.device_name in row['devices']:
                self.urcode = row['urcode']
                self.arcim = row['arcim']
                break

    @property
    def display_device(self):
        if not self.device_name:
            return "未知设备"
        name = self.device_name
        for suf in ["4楼", "9楼", "9F"]:
            name = name.replace(suf, "")
        return name

    @property
    def display_name(self):
        return self.display_device


# ============================================================
# 设备 → URCODE/ARCIM 映射（与 batch_upload_jpg.py 一致）
# 默认值；设置里修改后会通过 _apply_device_map() 覆盖
# ============================================================

DEVICE_URCODE_MAP = [
    {"devices": ["人体成分分析4楼", "人体成分分析9楼"], "urcode": "SZU01", "arcim": "6930||1^77||2"},
    {"devices": ["尿流量3楼"], "urcode": "SZU03", "arcim": ""},
    {"devices": ["肺功能", "肺功能9楼"], "urcode": "SZU04", "arcim": "592||1"},
    {"devices": ["airdoc", "Airdoc 4楼"], "urcode": "SZU06", "arcim": "8249||1^304||1"},
    {"devices": ["动脉硬化检测仪", "动脉硬化检测仪9F"], "urcode": "SZU07", "arcim": "7970||1"},
    {"devices": ["PAP Smear", "PAP Smear 4楼护士站"], "urcode": "SZU08", "arcim": ""},
    {"devices": ["Stool DNA"], "urcode": "SZU09", "arcim": ""},
    {"devices": ["循环肿瘤DNA"], "urcode": "SZU10", "arcim": ""},
    {"devices": ["脑电图"], "urcode": "SZU11", "arcim": ""},
    {"devices": ["遗传性肿瘤"], "urcode": "SZU12", "arcim": ""},
    {"devices": ["食物特异性IgG抗体"], "urcode": "SZU14", "arcim": ""},
    {"devices": ["肝纤维化扫描"], "urcode": "SZU16", "arcim": "35791||1"},
]

# 运行时设备映射（设置保存后从此读取；空则回退到 DEVICE_URCODE_MAP）
_RUNTIME_DEVICE_MAP = []


def _get_device_map():
    """返回当前生效的设备映射：优先运行时覆盖，否则用默认值"""
    if _RUNTIME_DEVICE_MAP:
        return _RUNTIME_DEVICE_MAP
    return DEVICE_URCODE_MAP


# ============================================================
# 上传线程（独立worker，per-report信号）
# ============================================================

class SingleReportWorker(QThread):
    """
    单报告worker：独立browser，独立线程。
    处理一份报告的完整流程：GetUserID → GetBaseInfo → FTP上传 → SaveUploadInfo
    """
    progress = pyqtSignal(str, int)  # (ord_no, pct)
    report_done = pyqtSignal(str, object, object, str)  # (mrn, rep, success_or_None, err)
    finished = pyqtSignal(str, object)  # (mrn, rep)

    def __init__(self, patient, rep, user_id, user_orditem_id=None, parent=None):
        super().__init__(parent)
        self.patient = patient
        self.rep = rep
        self.user_id = user_id
        self.user_orditem_id = user_orditem_id  # 预取则跳过GetUserID
        self.driver = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        mrn = self.patient.mrn
        rep = self.rep

        # 创建独立浏览器
        try:
            self.driver = _make_chrome_driver()
            self.driver.set_page_load_timeout(15)
        except Exception as e:
            log_append(mrn, rep.device_name, self.user_id, "browser_fail", str(e))
            rep.status = "fail"
            rep.ord_no = f"ERR:{e}"
            self.report_done.emit(mrn, rep, False, str(e))
            self.finished.emit(mrn, rep)
            return

        # GetUserID（有预取结果则跳过，否则实时获取）
        try:
            if self.user_orditem_id:
                user_orditem_id = self.user_orditem_id
                log_append(mrn, rep.device_name, self.user_id, "user_verified", user_orditem_id)
            else:
                arcim = str(rep.arcim or "").split('^')[0]
                user_orditem_id = _get_user_id(self.driver, self.user_id, "343", arcim)
                log_append(mrn, rep.device_name, self.user_id, "user_verified", user_orditem_id)
        except Exception as e:
            log_append(mrn, rep.device_name, self.user_id, "user_fail", str(e))
            rep.status = "fail"
            rep.ord_no = f"ERR:{e}"
            self.report_done.emit(mrn, rep, False, str(e))
            self.driver = None
            self.finished.emit(mrn, rep)
            return

        # ord_no 已有效则跳过获取
        ord_no = rep.ord_no
        if not ord_no or str(ord_no).startswith("ERR"):
            try:
                self.driver.get(CSP_PAGE_TPL.format(urcode=rep.urcode))
                import time as _t
                for _ in range(15):
                    try:
                        ready = self.driver.execute_script(
                            "try { return typeof tkMakeServerCall === 'function'; } catch(e) { return false; }")
                        if ready:
                            break
                    except Exception:
                        pass
                    _t.sleep(1)
                else:
                    raise RuntimeError("页面加载超时")

                arcim_main = str(rep.arcim or "").split('^')[0]
                script = f"""
                try {{
                    var r = tkMakeServerCall('web.DHCPE.Interface.Main', 'GetBaseInfo',
                                  '{mrn}', '{arcim_main}', 'HPNo', '343');
                    return r;
                }} catch(e) {{
                    return 'ERR:' + e.message;
                }}
                """
                result = self.driver.execute_script(script)
                if not result or str(result).startswith("ERR") or result == "NoHP":
                    raise RuntimeError(f"GetBaseInfo: {result or 'NoHP'}")
                fields = str(result).split("^")
                if len(fields) < 8:
                    raise RuntimeError(f"解析失败: {result}")
                ord_no = fields[7].strip()
                rep.ord_no = ord_no
                rep.arcim_sub = fields[8].strip() if len(fields) >= 9 else ""
                log_append(mrn, rep.device_name, self.user_id, "ord_found", ord_no)
            except Exception as e:
                rep.ord_no = f"ERR:{e}"
                rep.status = "fail"
                self.report_done.emit(mrn, rep, False, str(e))
                log_append(mrn, rep.device_name, self.user_id, "ord_fail", str(e))
                self.driver = None
                self.finished.emit(mrn, rep)
                return

        # FTP上传 + SaveUploadInfo
        rep.status = "uploading"
        rep.progress = 0
        self.report_done.emit(mrn, rep, None, "")

        try:
            # SaveResult：通知服务器医嘱已执行
            arcim_main = str(rep.arcim or "").split("^")[0]
            arcim_sub = getattr(rep, "arcim_sub", "")
            _save_result(self.driver, mrn, user_orditem_id, arcim_main, arcim_sub, "343")
            jpg_count = self._upload_single(rep)
            self.progress.emit(rep.ord_no, 80)
            for pg in range(jpg_count):
                fp = f"dhcpeftp/images/{rep.ord_no}/{rep.ord_no}_{pg}.jpg"
                _save_upload_info(self.driver, rep.ord_no, user_orditem_id, fp)
            rep.status = "done"
            rep.progress = 100
            self.progress.emit(rep.ord_no, 100)
            self.report_done.emit(mrn, rep, True, "")
            log_append(mrn, rep.device_name, self.user_id, "uploaded", rep.ord_no)
            _save_today_upload(mrn, rep.device_name, rep.ord_no, self.user_id)
        except Exception as e:
            rep.status = "fail"
            rep.error_msg = str(e)
            rep.progress = 0
            self.progress.emit(rep.ord_no, 0)
            self.report_done.emit(mrn, rep, False, str(e))
            log_append(mrn, rep.device_name, self.user_id, "upload_fail", str(e))

        self.driver = None
        self.finished.emit(mrn, rep)

    def _upload_single(self, report):
        pdf_path = str(report.pdf_path)
        if not report.ord_no:
            raise RuntimeError("无医嘱号")
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            raise RuntimeError(f"PDF打开失败: {e}")
        jpg_paths = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            tmp = tempfile.NamedTemporaryFile(suffix=f"_p{page_num}.jpg", delete=False)
            pix.save(tmp.name)
            tmp.close()
            jpg_paths.append(tmp.name)
        doc.close()
        if not jpg_paths:
            raise RuntimeError("无可上传页")
        cleared = False
        for i, jpg_path in enumerate(jpg_paths):
            remote_name = f"{report.ord_no}_{i}.jpg"
            # 第一张JPG：先检测并清空已有文件，再上传
            if not cleared:
                _do_ftps_clear_folder(report.ord_no)
                cleared = True
            _do_ftps_upload_single(report.ord_no, jpg_path, remote_name)
            os.unlink(jpg_path)
            self.progress.emit(report.ord_no, int(30 + (i + 1) / len(jpg_paths) * 50))
        # 所有jpg上传成功后删除原PDF
        try:
            os.unlink(pdf_path)
        except Exception:
            pass
        return len(jpg_paths)


class UploadWorker(QThread):
    """
    协调者：每份报告起一个独立SingleReportWorker，互相并行。
    """
    # 类级别信号（供外部连接）
    progress = pyqtSignal(str, int)
    report_done = pyqtSignal(str, object, object, str)
    finished = pyqtSignal(str)
    finished_worker = pyqtSignal(str, object)  # (mrn, rep) 供协调者内部用

    def __init__(self, patient, user_id, parent=None):
        super().__init__(parent)
        self.patient = patient
        self.user_id = user_id
        self._workers = {}  # rep → SingleReportWorker
        self._completed = 0

    def run(self):
        reports = [r for r in self.patient.reports if r.urcode and r.status != "cancel"]
        if not reports:
            return

        # 预取 GetUserID（一次，全报告共用）
        prefetch_user_id = None
        try:
            driver = _make_chrome_driver()
            driver.set_page_load_timeout(15)
            # 统一用 SZU06 取 user_id
            prefetch_user_id = _get_user_id(driver, self.user_id, "343", "8249||1")
            driver.quit()
        except Exception as e:
            log_append(self.patient.mrn, "预取GetUserID", self.user_id, "user_fail", str(e))

        # 并行启动所有报告 worker
        for rep in reports:
            rep.status = "pending"
            worker = SingleReportWorker(self.patient, rep, self.user_id,
                                       user_orditem_id=prefetch_user_id)
            worker.progress.connect(lambda ord_no, pct: self.progress.emit(ord_no, pct))
            worker.report_done.connect(lambda m, r, s, e: self.report_done.emit(m, r, s, e))
            worker.finished.connect(self._on_report_finished)
            self._workers[rep] = worker
            worker.start()

    def _on_report_finished(self, mrn, rep):
        self._completed += 1
        self.finished_worker.emit(mrn, rep)
        if self._completed >= len(self._workers):
            self.finished.emit(mrn)




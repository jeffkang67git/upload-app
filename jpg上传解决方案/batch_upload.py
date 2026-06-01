"""
批量体检报告上传脚本
完整7步流程：
  1. GetBaseInfo        → 获取医嘱号
  2. GetUserID          → 用户签名验证，获取内部ID
  3. GetBaseInfo(复验)  → 再次确认医嘱
  4. SaveResult         → 告诉服务器医嘱已执行（带内部用户ID）
  5. DeleteBeforeFile   → 删除旧文件
  6. GetUploadInfo      → 获取FTPS路径 → FTPS上传文件
  7. SaveUploadInfo     → 保存上传记录

依赖：pip install requests pypdf pillow selenium
运行：python batch_upload.py [用户编号] [设备名]
"""
import os
import sys
import time
import re
import json
import base64
import tempfile
from pathlib import Path
from datetime import datetime

# SSL 警告抑制
import urllib3
urllib3.disable_warnings()

try:
    from pypdf import PdfReader
except ImportError:
    print("请先安装 pypdf：pip install pypdf")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("请先安装 pillow：pip install pillow")
    sys.exit(1)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
except ImportError:
    print("请先安装 selenium：pip install selenium")
    sys.exit(1)


# ============================================================
# 配置
# ============================================================
BASE_DIR = Path("/Users/jeffreykang/Documents/Projects/体检报告上传")

# CSP 页面地址
CSP_PAGE_URL = "https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode=SZU01&CurLocID=343"

# HIS 接口地址
CSP_BROKER_URL = "https://10.1.9.105:1443/imedical/web/csp/%25CSP.Broker.cls"

# Chrome 路径
if os.path.exists(r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
elif os.path.exists("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
    CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else:
    CHROME_PATH = None

# FTPS 配置
FTP_CONFIG = {
    "host": "10.1.9.105",
    "port": 2121,
    "username": "dhccftp",
    "password": "Dhcc123!qwe",
}

# 默认科室参数
DEFAULT_LOCID = "343"
DEFAULT_ARCIM = "6930||1"

# URCode → Arcim 映射
URCODE_ARCIM = {
    "SZU01": "6930||1^77||2",    # 人体成分分析4楼
    "SZU02": "6930||1^77||2",    # 人体成分分析9楼
    "SZU04": "592||1",           # 肺功能
    "SZU05": "592||1",           # 肺功能9楼
    "SZU06": "8249||1^304||1",   # airdoc
    "SZU15": "8249||1^304||1",   # Airdoc 4楼
    "SZU07": "7970||1",          # 动脉硬化检测仪
    "SZU16": "35791||1",         # 肝纤维化扫描
    "SZU17": "7970||1",          # 动脉硬化检测仪9F
}


# ============================================================
# Chrome Driver 管理
# ============================================================
_chrome_driver = None
_chrome_driver_urcode = None


def get_chrome_driver(ur_code="SZU01"):
    global _chrome_driver, _chrome_driver_urcode

    if _chrome_driver is not None and _chrome_driver_urcode == ur_code:
        return _chrome_driver

    if _chrome_driver is not None:
        try:
            _chrome_driver.quit()
        except Exception:
            pass
        _chrome_driver = None

    if CHROME_PATH is None:
        raise RuntimeError("未找到 Chrome 安装路径")

    opts = Options()
    opts.binary_location = CHROME_PATH
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--allow-insecure-localhost")
    opts.add_argument("--ignore-certificate-errors")

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(15)

    csp_url = f"https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode={ur_code}&CurLocID=343"
    driver.get(csp_url)
    time.sleep(8)  # 等 tkMakeServerCall 初始化

    _chrome_driver = driver
    _chrome_driver_urcode = ur_code
    return driver


def close_chrome_driver():
    global _chrome_driver, _chrome_driver_urcode
    if _chrome_driver is not None:
        try:
            _chrome_driver.quit()
        except Exception:
            pass
        _chrome_driver = None
        _chrome_driver_urcode = None


# ============================================================
# 通过页面 JS 执行 CSP 调用（免 WEVENT/Cookie）
# ============================================================
import urllib.parse

def _call_via_driver(ur_code, method, *args):
    """通过 Selenium 在页面 JS 上下文中执行 tkMakeServerCall"""
    driver = get_chrome_driver(ur_code)

    args_js = ", ".join([f"'{str(a)}'" for a in args])
    script = f"""
    try {{
        var r = tkMakeServerCall('web.DHCPE.Interface.Main', '{method}', {args_js});
        return JSON.stringify({{ok: true, result: r}});
    }} catch(e) {{
        return JSON.stringify({{ok: false, error: e.message}});
    }}
    """
    try:
        raw = driver.execute_script(script)
    except Exception as js_err:
        # Alert 弹窗或其他 JS 异常
        try:
            alert = driver.switch_to.alert
            alert.dismiss()
        except Exception:
            pass
        raise RuntimeError(f"CSP {method} JS执行异常: {js_err}")

    if raw is None:
        raise RuntimeError(f"CSP {method} 返回为空（页面可能弹出alert）")

    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(f"CSP {method} 失败: {data.get('error')}")
    return data["result"]


def _call_csp_via_url(method, *args):
    """通过 urllib + Chrome cookies 调用 %CSP.Broker.cls"""
    import urllib.request, ssl

    global _chrome_driver
    if _chrome_driver is None:
        raise RuntimeError("Chrome driver 未初始化，请先调用 get_chrome_driver()")

    params = [("WARGC", len(args) + 1), ("WEVENT", method)]
    for i, arg in enumerate(args, start=1):
        params.append((f"WARG_{i}", arg))
    qs = urllib.parse.urlencode(params)
    url = f"{CSP_BROKER_URL}?{qs}"

    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/x-csp-hyperevent")
    req.add_header("User-Agent", "Mozilla/5.0")

    cookies = "; ".join(
        f"{c['name']}={c['value']}"
        for c in _chrome_driver.get_cookies()
        if c['name'] in ('CSPSESSIONID-SP-1443-UP-', 'CSPWSERVERID')
    )
    req.add_header("Cookie", cookies)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    resp = urllib.request.urlopen(req, timeout=15, context=ctx)
    return resp.read().decode("utf-8", errors="replace")


def _decode_csp(text):
    """解析 CSP 响应（base64 混合文本）"""
    if not text.strip():
        return ""
    lines = text.strip().split("\n")
    parts = []
    for line in lines:
        line = line.strip()
        if line.startswith("data:"):
            idx = line.find(",")
            if idx >= 0:
                b64 = line[idx + 1:]
                try:
                    decoded = base64.b64decode(b64).decode("utf-8", errors="replace")
                    # 去掉 #R / #OK 等标记
                    decoded = re.sub(r"#R|#OK.*", "", decoded).strip()
                    parts.append(decoded)
                except Exception:
                    parts.append(line)
        elif line and not line.startswith("#"):
            parts.append(line)
    return "".join(parts)


# ============================================================
# 核心 API（通过页面JS调用 tkMakeServerCall）
# ============================================================
def get_ord_no_via_page(exam_no, ur_code="SZU01"):
    """Step 1: 通过页面JS获取医嘱号
    签名: GetBaseInfo(examNo, arcim, "HPNo", curLocID)
    """
    arcim = URCODE_ARCIM.get(ur_code, DEFAULT_ARCIM)
    raw = _call_via_driver(ur_code, "GetBaseInfo", exam_no, arcim, "HPNo", "343")
    # 格式: 0002860830^SZUP01664326^张寿桐^男^1952-05-28^0^0^39379-72^77||2^39379||72
    fields = raw.split("^")
    if len(fields) >= 8:
        return fields[7].strip(), raw, fields[2].strip()
    raise RuntimeError(f"无法解析医嘱响应: {raw}")


def get_user_id_via_page(user_id_str, ur_code="SZU01"):
    """Step 2: 验证用户签名，返回内部用户ID
    签名: GetUserID(userCode, curLocID, arcim)
    """
    arcim = URCODE_ARCIM.get(ur_code, DEFAULT_ARCIM)
    raw = _call_via_driver(ur_code, "GetUserID", user_id_str, "343", arcim)
    raw = raw.strip()
    if not raw:
        raise RuntimeError("GetUserID 返回空")
    return raw


# ============================================================
# CSP 调用（通过 urllib + Chrome cookies）
# ============================================================
def csp_call(method, *args):
    """通过 urllib CSP.Broker.cls 调用"""
    resp = _call_csp_via_url(method, *args)
    decoded = _decode_csp(resp)
    return decoded


# ============================================================
# FTPS 上传
# ============================================================
def ftps_upload_file(local_path, ord_no, remote_filename, ftp_cfg=FTP_CONFIG):
    """通过显式 FTPS 上传文件到 /dhcpeftp/images/{ord_no}/"""
    import ftplib

    host = ftp_cfg["host"]
    port = ftp_cfg["port"]
    username = ftp_cfg["username"]
    password = ftp_cfg["password"]

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    ftps = ftplib.FTP_TLS(context=ctx)
    ftps.connect(host, port)
    ftps.login(username, password)
    ftps.prot_p()

    # Navigate to /dhcpeftp/images/{ord_no}/
    for seg in ["dhcpeftp", "images", str(ord_no)]:
        try:
            ftps.cwd(seg)
        except ftplib.error_perm:
            try:
                ftps.mkd(seg)
                ftps.cwd(seg)
            except Exception:
                pass

    with open(local_path, "rb") as f:
        ftps.storbinary(f"STOR {remote_filename}", f)
    ftps.quit()
    return True


# ============================================================
# PDF → JPG
# ============================================================
def pdf_to_jpg_list(pdf_path, dpi=150):
    import fitz

    if not hasattr(fitz, "open"):
        raise RuntimeError("PyMuPDF(fitz) 未安装")

    doc = fitz.open(str(pdf_path))
    jpg_paths = []
    tmp_dir = tempfile.mkdtemp(prefix="pe_report_")

    for i, page in enumerate(doc):
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        out = os.path.join(tmp_dir, f"page_{i}.jpg")
        pix.save(out)
        jpg_paths.append(out)

    doc.close()
    return jpg_paths


# ============================================================
# 主流程：处理单份 PDF
# ============================================================
def process_single_pdf(pdf_path, user_id, ur_code="SZU01", cur_loc_id=DEFAULT_LOCID, arcim=None):
    """
    完整7步上传流程：
      1. GetBaseInfo    → ord_no
      2. GetUserID      → user_internal_id
      3. GetBaseInfo    → 复验
      4. SaveResult     → 告诉服务器医嘱已执行
      5. DeleteBeforeFile → 删除旧文件
      6. GetUploadInfo + FTPS上传
      7. SaveUploadInfo
    """
    import ssl

    pdf_name = os.path.basename(pdf_path)
    pe_rec_no = os.path.splitext(pdf_name)[0]  # 文件名去掉扩展名 = 体检号
    arcim = arcim or DEFAULT_ARCIM

    print(f"\n  [*] 处理：{pdf_name}，体检号：{pe_rec_no}，URCode：{ur_code}")

    # ── Step 1: GetBaseInfo → ord_no ──────────────────────────────────────
    try:
        ord_no, raw, name = get_ord_no_via_page(pe_rec_no, ur_code)
        print(f"      Step1 GetBaseInfo → 姓名: {name}，医嘱号: {ord_no}")
    except Exception as e:
        return False, f"Step1 获取医嘱号失败: {e}"

    # ── Step 2: GetUserID → user_internal_id ───────────────────────────────
    try:
        user_internal_id = get_user_id_via_page(user_id, ur_code)
        print(f"      Step2 GetUserID   → 用户内部ID: {user_internal_id}")
    except Exception as e:
        return False, f"Step2 获取用户ID失败: {e}"

    # ── Step 3: GetBaseInfo（复验）────────────────────────────────────────
    try:
        raw2 = _call_via_driver(ur_code, "GetBaseInfo", pe_rec_no, "HPNo", cur_loc_id)
        print(f"      Step3 GetBaseInfo(复验) → OK")
    except Exception as e:
        print(f"      Step3 GetBaseInfo(复验) 失败（可忽略）: {e}")

    # ── Step 4: SaveResult ──────────────────────────────────────────────────
    # WARG_3=pe_rec_no, WARG_4=result_text, WARG_5=user_internal_id,
    # WARG_6=arcim_main, WARG_7=arcim_sub（从 GetBaseInfo raw 中提取）, WARG_8=cur_loc_id
    result_text = "参见报告"
    arcim_main = arcim.split("^")[0] if "^" in arcim else arcim
    # 从 GetBaseInfo raw 提取 arcim_sub（raw 格式: ...^73||1^...）
    arcim_sub = ""
    if "^" in arcim:
        # arcim like "8249||1^304||1", arcim_sub is "304||1"
        arcim_sub = arcim.split("^")[1]
    else:
        # 从 raw 中提取 parts[8] 作为 arcim_sub
        if len(raw.split("^")) >= 9:
            arcim_sub = raw.split("^")[8]
        if not arcim_sub:
            arcim_sub = "0"  # fallback

    try:
        save_resp = csp_call(
            "SaveResult",
            "web.DHCPE.Interface.Main",
            "SaveResult",
            pe_rec_no,           # WARG_3
            result_text,         # WARG_4
            user_internal_id,    # WARG_5
            arcim_main,          # WARG_6
            arcim_sub,           # WARG_7
            cur_loc_id           # WARG_8
        )
        print(f"      Step4 SaveResult  → {save_resp[:80] if save_resp else '空响应'}")
    except Exception as e:
        return False, f"Step4 SaveResult 失败: {e}"

    # ── Step 5: DeleteBeforeFile ────────────────────────────────────────────
    try:
        del_resp = csp_call(
            "DeleteBeforeFile",
            "web.DHCPE.FTPManager",
            "DeleteBeforeFile",
            ord_no
        )
        print(f"      Step5 DeleteBeforeFile → {del_resp[:80] if del_resp else '空响应'}")
    except Exception as e:
        print(f"      Step5 DeleteBeforeFile 失败（可忽略）: {e}")

    # ── Step 6: GetUploadInfo + FTPS上传 ────────────────────────────────────
    # 根据文件类型决定是 JPG 还是 PDF
    is_pdf = pdf_path.lower().endswith(".pdf")
    ext = ".pdf" if is_pdf else ".jpg"
    suffix = "_1.pdf" if is_pdf else "_0.jpg"

    try:
        upload_resp = csp_call(
            "GetUploadInfo",
            "web.DHCPE.Interface.Main",
            "GetUploadInfo",
            ord_no,     # WARG_3
            ext,        # WARG_4
            cur_loc_id  # WARG_5
        )
        print(f"      Step6 GetUploadInfo → {upload_resp[:200] if upload_resp else '空响应'}")

        # 解析 GetUploadInfo 响应（JSON格式）
        try:
            upload_info = json.loads(upload_resp)
            server_path = upload_info.get("serverPath", "")
            file_name = upload_info.get("fileName", "")
        except Exception:
            server_path = ""
            file_name = ""

        remote_fname = f"{ord_no}{suffix}"

        # FTPS 上传
        ftps_upload_file(pdf_path, ord_no, remote_fname)
        print(f"      Step6 FTPS上传成功 → {remote_fname}")

    except Exception as e:
        return False, f"Step6 FTPS上传失败: {e}"

    # ── Step 7: SaveUploadInfo ──────────────────────────────────────────────
    # 格式: ord_no|user_internal_id|file_path|status
    ftp_relative_path = f"dhcpeftp/images/{ord_no}/{remote_fname}"

    try:
        save_upload_resp = csp_call(
            "SaveUploadInfo",
            "web.DHCPE.Interface.Main",
            "SaveUploadInfo",
            ord_no,
            user_internal_id,
            ftp_relative_path,
            "1"
        )
        print(f"      Step7 SaveUploadInfo → {save_upload_resp[:80] if save_upload_resp else '空响应'}")
    except Exception as e:
        return False, f"Step7 SaveUploadInfo 失败: {e}"

    return True, f"上传成功（医嘱号：{ord_no}，文件：{remote_fname}）"


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import urllib.parse

    # 1. 用户编号
    if len(sys.argv) >= 2:
        user_id = sys.argv[1]
    else:
        user_id = input("用户编号（操作员工号）：").strip()

    if not user_id:
        print("用户编号不能为空")
        sys.exit(1)

    # 2. 设备名
    if len(sys.argv) >= 3:
        device_name = sys.argv[2]
    else:
        device_name = input("设备名（如人体成分分析4楼）：").strip()

    # 3. 扫描文件夹
    device_dir = BASE_DIR / device_name
    if not device_dir.exists():
        print(f"目录不存在：{device_dir}")
        sys.exit(1)

    pdf_files = sorted(device_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"目录 {device_dir} 中没有找到 PDF 文件")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"设备：{device_name}")
    print(f"用户编号：{user_id}")
    print(f"PDF 文件数：{len(pdf_files)}")
    print(f"{'='*60}\n")

    # 4. 逐个处理
    success_count = 0
    fail_count = 0

    for i, pdf_path in enumerate(pdf_files):
        print(f"\n[{i+1}/{len(pdf_files)}]")
        ok, msg = process_single_pdf(pdf_path, user_id)
        if ok:
            success_count += 1
            print(f"  ✓ {msg}")
        else:
            fail_count += 1
            print(f"  ✗ {msg}")

        if i < len(pdf_files) - 1:
            inp = input("  → 下一份（回车继续，q 退出）：").strip()
            if inp.lower() == "q":
                print("用户退出")
                break

    close_chrome_driver()

    print(f"\n{'='*60}")
    print(f"完成！成功 {success_count}，失败 {fail_count}")
    print(f"{'='*60}")
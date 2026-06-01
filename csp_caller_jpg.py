#!/usr/bin/env python3
"""
CSP 方法调用器 - 免 WEVENT / 免 Cookie 版
原理：在 uploadchkresult.csp 页面的 JS 上下文中执行 tkMakeServerCall，
CSP 服务器认为请求来自合法页面，直接放行。

用法:
    python3 csp_caller.py GetBaseInfo 2860830
    python3 csp_caller.py GetBaseInfo 2860830 6930||1 343
"""
import sys
import time
import json
import urllib.parse
import urllib.request
import ssl

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
except ImportError:
    print("ERROR: selenium 未安装")
    print("  pip3 install selenium")
    sys.exit(1)

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CSP_URL = "https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode=SZU01&CurLocID=343"

# 默认参数（来自 uploadchkresult.csp 页面隐藏字段）
DEFAULT_LOCID = "343"


def make_driver():
    opts = Options()
    opts.binary_location = CHROME_PATH
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--allow-insecure-localhost")
    opts.add_argument("--ignore-certificate-errors")
    prefs = {"credentials_enable_service": False, "password_manager_enabled": False}
    opts.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(15)
    return driver


def call_csp_in_page(driver, method, *args):
    """
    在 uploadchkresult.csp 页面的 JS 上下文中调用 tkMakeServerCall。
    返回原始结果字符串（如 '0002860830^SZUP01664326^...'）
    """
    # 动态构建参数数组
    args_js = ", ".join([f"'{str(a)}'" for a in args])

    script = f"""
    try {{
        var result = tkMakeServerCall('web.DHCPE.Interface.Main', '{method}', {args_js});
        return JSON.stringify({{ ok: true, result: result }});
    }} catch(e) {{
        return JSON.stringify({{ ok: false, error: e.message }});
    }}
    """

    raw = driver.execute_script(script)
    data = json.loads(raw)

    if not data.get("ok"):
        raise RuntimeError(f"CSP 调用失败: {data.get('error')}")

    return data["result"]


def call_csp_via_url(cookies, method, *args):
    """
    通过 urllib 调 %CSP.Broker.cls（备用方案，需要正确 WEVENT）
    """
    # 这个方法已废弃（WEVENT 无法自动获取）
    raise NotImplementedError("WEVENT 方案废弃，使用 tkMakeServerCall 免 WEVENT 版")


def get_ord_no(exam_no):
    """
    通过体检号获取医嘱号
    exam_no: 体检号字符串，如 '2860830'
    返回: (ord_no, raw_result) 或抛出异常
    """
    driver = make_driver()
    try:
        print(f"加载页面: {CSP_URL}")
        driver.get(CSP_URL)
        time.sleep(4)

        # 调用 GetBaseInfo
        raw = call_csp_in_page(driver, "GetBaseInfo", exam_no, "6930||1", "HPNo", "343")
        print(f"原始响应: {raw}")

        # 解析：格式 0002860830^SZUP01664326^张寿桐^男^1952-05-28^0^0^39379-72^77||2^39379||72
        fields = raw.split("^")
        if len(fields) >= 8:
            ord_no = fields[7].strip()  # 39379-72
            return ord_no, raw
        else:
            raise RuntimeError(f"无法解析响应格式: {raw}")

    finally:
        driver.quit()


def main():
    if len(sys.argv) < 3:
        print("用法:")
        print("  python3 csp_caller.py GetBaseInfo <体检号>")
        print("  python3 csp_caller.py GetBaseInfo <体检号> <ARCIM_ID> <LOCID>")
        print("\n示例:")
        print("  python3 csp_caller.py GetBaseInfo 2860830")
        sys.exit(1)

    method = sys.argv[1]
    exam_no = sys.argv[2]

    driver = make_driver()
    try:
        print(f"加载 CSP 页面...")
        driver.get(CSP_URL)
        time.sleep(4)

        # 获取页面上隐藏字段的值
        hidden = {}
        for fid in ["H_LOCID", "H_ArcimID", "H_ODID"]:
            try:
                val = driver.execute_script(f"return document.getElementById('{fid}') ? document.getElementById('{fid}').value : null;")
                if val:
                    hidden[fid] = val
            except:
                pass
        print(f"页面隐藏字段: {hidden}")

        # 调用指定方法
        if method == "GetBaseInfo":
            # 位置参数: (体检号, ARCIM_ID, HPNo固定, LOCID)
            locid = sys.argv[3] if len(sys.argv) > 3 else (hidden.get("H_LOCID") or DEFAULT_LOCID)
            arcim = sys.argv[4] if len(sys.argv) > 4 else (hidden.get("H_ArcimID") or "6930||1")

            raw = call_csp_in_page(driver, "GetBaseInfo", exam_no, arcim, "HPNo", locid)
            print(f"\n结果: {raw}")

            # 解析医嘱号
            fields = raw.split("^")
            if len(fields) >= 8:
                ord_no = fields[7].strip()  # 39379-72
                name = fields[2].strip()    # 张寿桐
                gender = fields[3].strip()  # 男
                birth = fields[4].strip()   # 1952-05-28
                reg_code = fields[1].strip()  # SZUP01664326
                print(f"姓名: {name}")
                print(f"性别: {gender}")
                print(f"出生: {birth}")
                print(f"登记号: {reg_code}")
                print(f"医嘱号: {ord_no}")
            else:
                print(f"字段不足，无法解析: raw={raw}")

        else:
            # 其他方法，直接传所有参数
            other_args = sys.argv[3:] if len(sys.argv) > 3 else []
            raw = call_csp_in_page(driver, method, *other_args)
            print(f"\n结果: {raw}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
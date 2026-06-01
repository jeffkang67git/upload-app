#!/usr/bin/env python3
"""
CSP WEVENT & Session 获取器
使用 Selenium Headless Chrome 加载 uploadchkresult.csp，拦截 CSP Ajax 请求，
提取其中的 WEVENT 和 Cookie，用于后续 API 调用。

核心发现：
  WEVENT = 方法名（如 'GetBaseInfo'），不是随机 token
  来自 cspxmlhttp.js 的 cspIntHttpServerMethod: data = "WARGC=...&WEVENT=" + method

用法:
    python3 fetch_csp_token.py [体检号]
"""
import sys
import time
import re
import json
import urllib.parse
import urllib.request
import ssl

# -------------------------------------------------------
# 依赖检查
# -------------------------------------------------------
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
except ImportError:
    print("ERROR: selenium 未安装")
    print("  pip3 install selenium")
    sys.exit(1)

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CSP_URL = "https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp"
BROKER_URL = "https://10.1.9.105:1443/imedical/web/csp/%25CSP.Broker.cls"

def make_driver():
    opts = Options()
    opts.binary_location = CHROME_PATH
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--allow-insecure-localhost")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--disable-web-security")
    prefs = {"credentials_enable_service": False, "password_manager_enabled": False}
    opts.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(15)
    return driver


def fetch_csp_session():
    """
    启动 headless Chrome，加载 uploadchkresult.csp，
    用 XHR hook 拦截 CSP.Broker 请求，提取 WEVENT。
    """
    print("启动 Chrome headless...")
    driver = make_driver()

    try:
        # 注入 XHR hook（必须在页面加载前）
        script = """
        window._capturedCspUrls = [];
        var origXHR = window.XMLHttpRequest;
        window.XMLHttpRequest = function() {
            var xhr = new origXHR();
            var origOpen = xhr.open;
            xhr.open = function(method, url) {
                if (url && (url.includes('CSP.Broker') || url.includes('WEVENT'))) {
                    window._capturedCspUrls.push(method + ' ' + url);
                }
                return origOpen.apply(this, arguments);
            };
            return xhr;
        };
        return 'XHR hook injected';
        """
        driver.execute_script(script)

        print(f"加载: {CSP_URL}")
        driver.get(CSP_URL)
        time.sleep(4)

        # 触发页面 JS（点击按钮，诱使页面发 CSP 请求）
        trigger_script = """
        var inputs = document.querySelectorAll('input[type="button"], button');
        for (var i = 0; i < inputs.length; i++) {
            try { inputs[i].click(); } catch(e) {}
        }
        return inputs.length + ' buttons triggered';
        """
        driver.execute_script(trigger_script)
        time.sleep(2)

        # 读取被捕获的 URL
        captured = driver.execute_script(
            "return JSON.stringify(window._capturedCspUrls)"
        )
        print(f"捕获的 CSP URL: {captured}")

        # 提取 cookies
        cookies = {}
        for cookie in driver.get_cookies():
            if cookie['name'] in ('CSPSESSIONID-SP-1443-UP-', 'CSPWSERVERID'):
                cookies[cookie['name']] = cookie['value']

        print(f"Cookies: {cookies}")
        driver.quit()
        return cookies, captured

    except Exception as e:
        driver.quit()
        print(f"ERROR: {e}")
        return None, None


def call_csp_method(cookies, method, *args):
    """
    用 cookies 直接调 %CSP.Broker.cls
    method: 方法名 (如 'GetBaseInfo')
    args: WARG_1, WARG_2, ...
    """
    params = [("WARGC", len(args) + 1), ("WEVENT", method)]
    for i, arg in enumerate(args, start=1):
        params.append((f"WARG_{i}", arg))

    qs = urllib.parse.urlencode(params)
    url = f"{BROKER_URL}?{qs}"

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())

    req = urllib.request.Request(url, method="POST")
    req.add_header("Cookie", cookie_str)
    req.add_header("Content-Type", "application/x-csp-hyperevent")
    req.add_header("User-Agent", "Mozilla/5.0")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        body = resp.read().decode('utf-8', errors='replace')
        return parse_csp_response(body)
    except Exception as e:
        return {"error": str(e)}


def parse_csp_response(body):
    """解析 CSP 响应包"""
    lines = body.split("\r\n")
    result = {}
    if len(lines) > 0:
        result["session_id"] = lines[0].strip()
    for i, line in enumerate(lines):
        if line in ("#R", "#V"):
            result["flag"] = line
            if i + 1 < len(lines):
                result["data"] = lines[i + 1] if lines[i + 1] not in ("#OK", "") else None
            break
    if "#OK" in lines:
        result["status"] = "OK"
    return result


if __name__ == "__main__":
    cookies, captured = fetch_csp_session()
    if not cookies:
        sys.exit(1)

    print(f"\n已获取 Cookies:")
    for k, v in cookies.items():
        print(f"  {k} = {v}")

    # 分析捕获的 URL，提取 WEVENT
    wevent = None
    if captured:
        try:
            urls = json.loads(captured)
            for u in urls:
                # URL 格式: GET https://host/imedical/web/csp/%25CSP.Broker.cls?WARGC=...&WEVENT=...
                if "WEVENT=" in u:
                    parsed = urllib.parse.urlparse(u.split(" ", 1)[1])
                    q = urllib.parse.parse_qs(parsed.query)
                    if "WEVENT" in q:
                        wevent = q["WEVENT"][0]
                        print(f"\n从 URL 提取 WEVENT: {wevent}")
        except Exception as e:
            print(f"解析捕获 URL 失败: {e}")

    # 测试调用: GetBaseInfo
    exam_no = sys.argv[1] if len(sys.argv) > 1 else "2860830"
    print(f"\n测试 GetBaseInfo (体检号={exam_no})...")

    result = call_csp_method(
        cookies,
        "GetBaseInfo",
        "web.DHCPE.Interface.Main",
        "GetBaseInfo",
        exam_no,
        "6930||1",
        "HPNo",
        "343"
    )

    print("结果:", json.dumps(result, indent=2, ensure_ascii=False))
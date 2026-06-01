#!/usr/bin/env python3
"""
抓取 CSP WEVENT - 使用 Chrome 远程调试或 headless 模式
"""
import subprocess
import time
import re
import sys

def find_chrome():
    """找到 Chrome 路径"""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
    ]
    for p in candidates:
        if subprocess.call(["ls", "-f", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            return p
    return None

def start_chrome_debug():
    """启动 Chrome 远程调试模式"""
    chrome_path = find_chrome()
    if not chrome_path:
        print("ERROR: Chrome not found")
        return None

    # 启动带远程调试端口的 Chrome
    cmd = [
        chrome_path,
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
        "--user-data-dir=/tmp/chrome-debug",
        "https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode=SZU01&CurLocID=343"
    ]
    print(f"Starting Chrome: {' '.join(cmd[:3])} ...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc

def get_wevent_via_debug():
    """通过 Chrome 远程调试接口获取 WEVENT"""
    import json

    # 等待 Chrome 启动
    time.sleep(3)

    # 获取 CDP 端点
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:9222/json/version", timeout=5)
        data = json.loads(resp.read())
        ws_url = data.get("webSocketDebuggerUrl")
        print(f"WebSocket URL: {ws_url}")
    except Exception as e:
        print(f"Failed to get CDP: {e}")
        return None

    # 用 CDW (Chrome DevTools Protocol) 获取 WEVENT
    # 实际可以用 selenium-wire 或直接用 Chrome 的日志
    return None

def get_wevent_via_cdp():
    """通过 CDP Network intercept 捕获 WEVENT"""
    # 这是完整方案，需要 selenium + CDP
    pass

def get_wevent_via_selenium():
    """使用 Selenium + Chrome headless 抓 WEVENT"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    chrome_path = find_chrome()
    if not chrome_path:
        return None

    opts = Options()
    opts.binary_location = chrome_path
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")

    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as e:
        print(f"Chrome driver error: {e}")
        return None

    try:
        print("Loading CSP page...")
        driver.get("https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode=SZU01&CurLocID=343")
        time.sleep(3)

        # 获取所有请求
        for entry in driver.get_log('performance'):
            pass  # 日志获取方式取决于 selenium 版本

        # 另一种方式：执行 JS 获取 network 请求
        # 不过 selenium 的 log 类型有限
        print("Cookies:", driver.get_cookies())
        print("Current URL:", driver.current_url)

        # 尝试注入 JS 拦截 CSP 请求
        script = """
        var captured = [];
        var origFetch = window.fetch;
        window.fetch = function(req) {
            if (req.url.includes('CSP.Broker')) {
                captured.push(req.url);
                console.log('CSP REQUEST:', req.url);
            }
            return origFetch.apply(this, arguments);
        };
        return 'injected';
        """
        driver.execute_script(script)
        time.sleep(1)

        return None
    finally:
        driver.quit()

if __name__ == "__main__":
    # 方式1：Selenium headless
    print("=== Method 1: Selenium Headless ===")
    result = get_wevent_via_selenium()
    if result:
        print(f"WEVENT: {result}")
    else:
        print("Selenium did not capture WEVENT")

    # 方式2：直接检查 Chrome 页面内容（看是否有 WEVENT 泄露）
    print("\n=== Method 2: Check page source ===")
    chrome_path = find_chrome()
    if chrome_path:
        import urllib.request
        req = urllib.request.Request(
            "https://10.1.9.105:1443/imedical/web/csp/dhcpe.uploadchkresult.csp?URCode=SZU01&CurLocID=343",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode('utf-8', errors='replace')
        # 搜索 WEVENT 相关内容
        matches = re.findall(r'WEVENT["\s]*[=:]["\s]*([^\s&"]+)', html)
        print(f"WEVENT in HTML: {matches}")
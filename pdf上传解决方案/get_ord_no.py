"""
获取医嘱号 - 每次运行前从浏览器抓取 Cookie 和 WEVENT

用法:
  1. 打开 Edge → 登录体检系统 → 输入体检号 → 回车
  2. F12 → Network → 找到 %CSP.Broker.cls 请求
  3. 在 Payload 里复制完整内容，粘到这里

依赖: pip install requests
"""

import sys
import re
import requests
import urllib3

urllib3.disable_warnings()

CSP_BROKER_URL = "https://10.1.9.105:1443/imedical/web/csp/%25CSP.Broker.cls"


def parse_response(text):
    """解析 #R 响应，去掉 CSP 包装，提取业务数据"""
    # 响应格式: sessionId\r\n#R\r\n\r\n#OK\r\n业务数据
    if "#R" not in text:
        return None, text
    # 去掉 sessionId 和 #R/#OK 包装
    parts = text.split("#R")
    if len(parts) < 2:
        return None, text
    rest = parts[1]
    if "#OK" in rest:
        rest = rest.split("#OK", 1)[1]
    return rest.strip(), rest


def get_ord_no(pe_rec_no, cookie, wevent):
    """
    输入体检号，返回医嘱号
    pe_rec_no: 体检号，如 "2860830"
    cookie: 浏览器 Cookie 字符串
    wevent: 浏览器抓到的 WEVENT 值
    """
    cookies = {k.strip(): v.strip() for k, v in (item.split("=", 1) for item in cookie.split(";"))}

    params = {
        "WARGC": "6",
        "WEVENT": wevent,
        "WARG_1": "web.DHCPE.Interface.Main",
        "WARG_2": "GetBaseInfo",
        "WARG_3": pe_rec_no,
        "WARG_4": "6930||1",
        "WARG_5": "HPNo",
        "WARG_6": "343",
    }

    resp = requests.post(CSP_BROKER_URL, params=params, cookies=cookies, verify=False, timeout=30)

    raw, body = parse_response(resp.text)
    if not body:
        return None, f"解析失败: {resp.text[:200]}"

    # 提取医嘱号: 格式 ^数字-数字^
    m = re.search(r'\^(\d{5}-\d{2})\^', body)
    if m:
        return m.group(1), body

    # 备选: 搜最后一个 5位-2位
    matches = re.findall(r'(\d{5}-\d{2})', body)
    if matches:
        return matches[-1], body

    return None, body


def main():
    # 1. 体检号
    if len(sys.argv) >= 2:
        pe_rec_no = sys.argv[1]
    else:
        pe_rec_no = input("体检号: ").strip()
    if not pe_rec_no:
        print("体检号不能为空")
        sys.exit(1)

    # 2. Cookie
    if len(sys.argv) >= 3:
        cookie = sys.argv[2]
    else:
        cookie = input("Cookie: ").strip()
    if not cookie:
        print("Cookie不能为空")
        sys.exit(1)

    # 3. WEVENT
    if len(sys.argv) >= 4:
        wevent = sys.argv[3]
    else:
        wevent = input("WEVENT: ").strip()
    if not wevent:
        print("WEVENT不能为空")
        sys.exit(1)

    print(f"\n体检号: {pe_rec_no}")
    print(f"WEVENT: {wevent[:20]}...")
    print("-" * 60)

    ord_no, body = get_ord_no(pe_rec_no, cookie, wevent)

    if ord_no:
        print(f"\n✅ 成功！")
        print(f"   体检号: {pe_rec_no}")
        print(f"   医嘱号: {ord_no}")
        print(f"   完整响应: {body}")
    else:
        print(f"\n❌ 失败: {body[:300]}")


if __name__ == "__main__":
    main()
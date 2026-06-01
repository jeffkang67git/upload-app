"""
测试：体检号 → 医嘱号（精确复刻浏览器请求）
用法：python test_getbaseinfo.py <体检号> [Cookie]
"""
import sys
import re
import requests
import urllib3

urllib3.disable_warnings()

CSP_BROKER_URL = "https://10.1.9.105:1443/imedical/web/csp/%25CSP.Broker.cls"


def csp_request(cookie, warg3, warg4="6930||1", warg5="HPNo", warg6="343"):
    """
    复刻浏览器：POST，参数全在 query string，无 body
    """
    cookies = {k.strip(): v.strip() for k, v in (item.split("=", 1) for item in cookie.split(";"))}

    params = {
        "WARGC": "6",
        "WEVENT": "5UOAjvPfL2xYaAAB8W68ac9TC9W936XpzoDBN6X2gF_0GwlybsFJIKY7GBeVYy1$",
        "WARG_1": "web.DHCPE.Interface.Main",
        "WARG_2": "GetBaseInfo",
        "WARG_3": warg3,
        "WARG_4": warg4,
        "WARG_5": warg5,
        "WARG_6": warg6,
    }

    resp = requests.post(CSP_BROKER_URL, params=params, cookies=cookies, verify=False, timeout=30)
    return resp


def parse_ord_no(text):
    m = re.search(r'\^(\d{5}-\d{2})\^', text)
    if m:
        return m.group(1)
    matches = re.findall(r'(\d{5}-\d{2})', text)
    if matches:
        return matches[-1]
    return None


def main():
    if len(sys.argv) >= 2:
        pe_rec_no = sys.argv[1]
    else:
        pe_rec_no = input("体检号: ").strip()

    if len(sys.argv) >= 3:
        cookie = sys.argv[2]
    else:
        cookie = input("Cookie: ").strip()

    if not cookie:
        sys.exit(1)

    print(f"\n体检号: {pe_rec_no}")
    print("-" * 60)

    try:
        resp = csp_request(cookie, warg3=pe_rec_no)
        print(f"HTTP: {resp.status_code} | {len(resp.text)} chars")
        print(f"\n响应:\n{resp.text}")
        print("-" * 60)

        ord_no = parse_ord_no(resp.text)
        if ord_no:
            print(f"✅ 医嘱号: {ord_no}")
        else:
            print(f"❌ 未解析到医嘱号")

    except Exception as e:
        print(f"请求失败: {e}")


if __name__ == "__main__":
    main()
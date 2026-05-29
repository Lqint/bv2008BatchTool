"""QR code login for bv2008 — obtains accessToken via 京通 scan-to-login.

Usage:
    python3 bv_login.py

Prints the accessToken on success. Paste it into config.py as TOKEN.
"""
import sys
import time
import json
import qrcode
import requests

from bv_client import make_sign, GATEWAY


def call_get(interface_id: str, params: dict, app_id: str = "zybjuser") -> dict:
    form = {
        "app_id": app_id,
        "interface_id": interface_id,
        "version": "1.0",
        "header": '{"accessSource":"pc"}',
        "biz_content": json.dumps(params, separators=(",", ":"), ensure_ascii=False),
        "charset": "utf8",
        "timestamp": str(int(time.time() * 1000)),
        "origin": "1",
    }
    form["sign"] = make_sign(form)
    files = {k: (None, v) for k, v in form.items()}
    r = requests.post(GATEWAY, files=files, timeout=30)
    r.raise_for_status()
    return r.json()


def unwrap_raw(resp: dict) -> dict:
    return json.loads(resp["data"])


def create_qr() -> tuple[str, str]:
    """Returns (codeId, codeContent_url)."""
    resp = call_get("createCityCode", {})
    inner = unwrap_raw(resp)
    if not inner.get("success"):
        raise RuntimeError(f"createCityCode failed: {inner.get('message')} ({inner.get('code')})")
    rd = inner["data"]["resultData"]
    return rd["codeId"], rd["codeData"]["codeContent"]


def poll_status(code_id: str, timeout: int = 120) -> str:
    """Poll until scan+confirm (status=3), return accessToken."""
    deadline = time.time() + timeout
    scanned = False
    while time.time() < deadline:
        resp = call_get("checkCodeStatus", {"codeId": code_id})
        inner = unwrap_raw(resp)
        if not inner.get("success"):
            raise RuntimeError(f"checkCodeStatus error: {inner.get('message')}")
        status = inner["data"]["resultData"]["status"]
        if status == "2" and not scanned:
            print("  已扫码，等待确认...")
            scanned = True
        elif status == "3":
            return inner["data"]["resultData"]["data"]["accessToken"]
        time.sleep(3)
    raise TimeoutError("QR login timed out")


def print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def main() -> int:
    print("[1/2] 创建二维码...")
    code_id, code_url = create_qr()
    print(f"      codeId={code_id}")
    print()
    print_qr(code_url)
    print()
    print("请用 支付宝/微信/百度 或 京通小程序 扫描上方二维码登录")
    print("等待扫码（最长 120 秒）...\n")

    try:
        token = poll_status(code_id, timeout=120)
    except TimeoutError:
        print("超时，请重新运行")
        return 1

    print(f"\n[2/2] 登录成功！accessToken:")
    print(f"\n  {token}\n")
    print("将上方 token 写入 config.py 的 TOKEN 字段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

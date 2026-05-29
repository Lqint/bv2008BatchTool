"""bv2008 gateway client: sign + SM2 encrypt + call."""
import json
import time
import requests
from gmssl import sm2, sm3, func

try:
    from config import GATEWAY, TOKEN
except ImportError:
    GATEWAY = "https://test1.bv2008.cn/api-gateway/jpaas-jags-server/interface/gateway"
    TOKEN = ""

APP_ID = "zybjfront"


def make_sign(form: dict) -> str:
    raw = (
        f"app_id={form['app_id']}&biz_content={form['biz_content']}"
        f"&charset={form['charset']}&interface_id={form['interface_id']}"
        f"&origin={form['origin']}&timestamp={form['timestamp']}"
        f"&version={form['version']}"
    )
    return sm3.sm3_hash(func.bytes_to_list(raw.encode("utf-8")))


def sm2_encrypt(plaintext: str, pub_hex: str) -> str:
    pk = pub_hex[2:] if pub_hex.startswith("04") and len(pub_hex) == 130 else pub_hex
    crypter = sm2.CryptSM2(public_key=pk, private_key="", mode=1)
    enc = crypter.encrypt(plaintext.encode("utf-8"))
    return "04" + enc.hex()


def call(
    interface_id: str,
    biz: dict,
    access_token: str | None = None,
    app_id: str = APP_ID,
    file: tuple | None = None,
) -> dict:
    """Call gateway. file = (filename, bytes, mimetype) when uploading."""
    header = {"accessSource": "pc"}
    if access_token:
        header["accessToken"] = access_token
    form = {
        "app_id": app_id,
        "interface_id": interface_id,
        "version": "1.0",
        "header": json.dumps(header, separators=(",", ":")),
        "biz_content": json.dumps(biz, separators=(",", ":"), ensure_ascii=False),
        "charset": "utf8",
        "timestamp": str(int(time.time() * 1000)),
        "origin": "1",
    }
    form["sign"] = make_sign(form)
    files = {k: (None, v) for k, v in form.items()}
    if file is not None:
        files["file"] = file
    r = requests.post(GATEWAY, files=files, timeout=60)
    r.raise_for_status()
    return r.json()


def unwrap(resp: dict) -> dict:
    """Parse inner JSON string from outer envelope."""
    inner = json.loads(resp["data"])
    if not inner.get("success"):
        raise RuntimeError(f"gateway error: {inner.get('message')} ({inner.get('code')})")
    return inner["data"]


def get_in_sm2_pk(access_token: str) -> str:
    resp = call("getInSm2Key", {}, access_token=access_token)
    return unwrap(resp)["pk"]

"""Programmatic bv2008 API wrapper used by the GUI and batch runner."""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

import requests
from gmssl import func, sm2, sm3

GATEWAY = "https://test1.bv2008.cn/api-gateway/jpaas-jags-server/interface/gateway"
APP_ID = "zybjfront"
REQUEST_DELAY_SECONDS = 3

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@dataclass
class PostInfo:
    name: str
    post_id: str
    post_code: str
    raw: dict


class AmbiguousUserError(RuntimeError):
    def __init__(self, name: str, count: int):
        super().__init__(f"姓名 {name} 匹配到 {count} 人，请补充身份证号后重试")
        self.name = name
        self.count = count


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


def call_gateway(
    interface_id: str,
    biz: dict,
    access_token: str | None = None,
    app_id: str = APP_ID,
    file: tuple | None = None,
) -> dict:
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
    try:
        response = requests.post(GATEWAY, files=files, timeout=60)
        response.raise_for_status()
        return response.json()
    finally:
        time.sleep(REQUEST_DELAY_SECONDS)


def parse_gateway_data(resp: dict) -> dict:
    data = resp.get("data")
    inner = json.loads(data) if isinstance(data, str) else data
    if not isinstance(inner, dict):
        raise RuntimeError(f"unexpected gateway data: {data!r}")
    return inner


def unwrap_success(resp: dict) -> dict:
    inner = parse_gateway_data(resp)
    if not inner.get("success"):
        raise RuntimeError(f"{inner.get('message') or 'gateway error'} ({inner.get('code')})")
    return inner.get("data", {})


def parse_business_data(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected business data: {value!r}")
    return value


class BVApi:
    def __init__(self, token: str = ""):
        self.token = token
        self._pk: str | None = None

    def set_token(self, token: str) -> None:
        self.token = token.strip()
        self._pk = None

    def call(self, interface_id: str, biz: dict, app_id: str = "zybjfront", file: tuple | None = None) -> dict:
        return call_gateway(interface_id, biz, access_token=self.token, app_id=app_id, file=file)

    def get_pk(self) -> str:
        if self._pk is None:
            self._pk = unwrap_success(self.call("getInSm2Key", {}))["pk"]
        return self._pk

    def encrypt(self, value: str) -> str:
        return sm2_encrypt(value, self.get_pk())

    def create_login_qr(self) -> tuple[str, str]:
        data = unwrap_success(call_gateway("createCityCode", {}, app_id="zybjuser"))
        rd = data["resultData"]
        return rd["codeId"], rd["codeData"]["codeContent"]

    def check_login_status(self, code_id: str) -> tuple[str, str | None]:
        data = unwrap_success(call_gateway("checkCodeStatus", {"codeId": code_id}, app_id="zybjuser"))
        rd = data["resultData"]
        status = str(rd.get("status", ""))
        token = None
        if status == "3":
            token = rd["data"]["accessToken"]
            self.set_token(token)
        return status, token

    def find_posts(self, activity_id: str, org_id: str) -> list[PostInfo]:
        resp = self.call("findPostList", {"activityId": activity_id, "orgId": org_id})
        inner = parse_gateway_data(resp)
        if str(inner.get("code", "")) != "200":
            raise RuntimeError(f"{inner.get('message') or inner.get('msg') or 'findPostList failed'} ({inner.get('code')})")
        data = parse_business_data(inner.get("data", {}))
        rows = data.get("resultData", {}).get("dataList", [])
        return [
            PostInfo(
                name=str(row.get("postName", "")),
                post_id=str(row.get("iid") or row.get("postId") or row.get("postCode") or ""),
                post_code=str(row.get("postCode", "")),
                raw=row,
            )
            for row in rows
            if row.get("postName") and (row.get("iid") or row.get("postId") or row.get("postCode"))
        ]

    def find_org_user(self, name: str, cert_no: str, activity_id: str, post_id: str, org_id: str) -> dict | None:
        biz = {
            "pageNo": 1,
            "pageSize": 10,
            "name": self.encrypt(name),
            "activityId": activity_id,
            "postId": post_id,
            "orgId": org_id,
        }
        if cert_no:
            biz["certNo"] = self.encrypt(cert_no)
        data = unwrap_success(self.call("activityUser-findOrgUserList", biz))
        rows = data.get("resultData", {}).get("dataList", [])
        if len(rows) > 1:
            raise AmbiguousUserError(name, len(rows))
        return rows[0] if rows else None

    def add_member(self, name: str, cert_no: str) -> tuple[bool, str]:
        biz = {
            "name": self.encrypt(name),
            "certNo": self.encrypt(cert_no),
        }
        inner = parse_gateway_data(self.call("addMember", biz))
        data = inner.get("data", {})
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {"message": data}
        if not isinstance(data, dict):
            data = {}

        code = str(data.get("code", ""))
        message = data.get("message") or inner.get("message", "")
        if code == "200":
            return True, message or "success"
        if code == "50000" and "该用户已加入团体" in message:
            return True, message
        if code == "50000":
            return False, message or "添加的人尚未注册为志愿者"
        if inner.get("success") is False:
            return False, message or f"gateway error: {inner.get('code')}"
        return False, message or f"unexpected addMember code: {code or 'missing'}"

    def add_to_post(self, activity_id: str, post_id: str, org_id: str, uid: str) -> None:
        biz = {
            "activityId": activity_id,
            "postId": post_id,
            "orgId": org_id,
            "uids": [uid],
        }
        unwrap_success(self.call("activityUser-addList", biz))

    def upload_proof(self, filename: str | None, data: bytes | None) -> str:
        if not data:
            filename = "proof.png"
            data = base64.b64decode(TINY_PNG_B64)
        else:
            filename = filename or "proof.png"
        biz = {
            "file": {"uid": f"vc-upload-{int(time.time() * 1000)}-1"},
            "uploadType": "durationFile",
        }
        resp = self.call(
            "zybj_uploadFile",
            biz,
            app_id="zybjuser",
            file=(filename, data, "image/png"),
        )
        result = unwrap_success(resp)
        return result["resultData"]["fileData"]["newName"]

    def record_hours(
        self,
        activity_id: str,
        post_id: str,
        org_id: str,
        uid: str,
        times: list[tuple[str, float]],
        file_path: str,
        notes: str = "",
    ) -> dict:
        biz = {
            "activityId": activity_id,
            "postId": post_id,
            "orgId": org_id,
            "notes": notes,
            "uids": [uid],
            "times": [{"time": day, "hour": hour} for day, hour in times],
            "filePath": file_path,
        }
        return unwrap_success(self.call("activityTiming-batchAdd", biz))

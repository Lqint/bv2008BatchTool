"""Record service hours for a uid on a specific date.

Usage:
    python3 bv_record_hours.py <uid> <YYYY-MM-DD> <hours> [proof_image_path]

Example:
    python3 bv_record_hours.py 90873434 2026-05-02 8 ./proof.png

Configure TOKEN / ACTIVITY_ID / POST_ID / ORG_ID in config.py first.
"""
import sys
import json
import time
import base64
import mimetypes
from pathlib import Path

from bv_client import call, unwrap

try:
    from config import TOKEN, ACTIVITY_ID, POST_ID, ORG_ID
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and fill in values")

NOTES = ""

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def upload_proof(path: Path | None) -> str:
    """Upload proof image and return the server-assigned filename (newName).

    NOTE: filePath is single-use — re-upload for every batchAdd call.
    """
    if path is None:
        name = "proof.png"
        data = base64.b64decode(TINY_PNG_B64)
        mime = "image/png"
    else:
        name = path.name
        data = path.read_bytes()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"

    biz = {
        "file": {"uid": f"vc-upload-{int(time.time() * 1000)}-1"},
        "uploadType": "durationFile",
    }
    resp = call(
        "zybj_uploadFile",
        biz,
        access_token=TOKEN,
        app_id="zybjuser",
        file=(name, data, mime),
    )
    data_obj = unwrap(resp)
    return data_obj["resultData"]["fileData"]["newName"]


def batch_add(uid: str, date_str: str, hour: float, file_path: str) -> dict:
    biz = {
        "activityId": ACTIVITY_ID,
        "postId": POST_ID,
        "orgId": ORG_ID,
        "notes": NOTES,
        "uids": [uid],
        "times": [{"time": date_str, "hour": hour}],
        "filePath": file_path,
    }
    return unwrap(call("activityTiming-batchAdd", biz, access_token=TOKEN))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    uid = argv[0]
    date_str = argv[1]
    hour = float(argv[2])
    proof = Path(argv[3]) if len(argv) >= 4 else None

    print(f"[1/2] upload proof image ({'<auto 1x1 PNG>' if proof is None else proof})")
    file_path = upload_proof(proof)
    print(f"      filePath = {file_path}")

    print(f"[2/2] batchAdd uid={uid} time={date_str} hour={hour}")
    res = batch_add(uid, date_str, hour, file_path)
    print(f"      result: {json.dumps(res, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

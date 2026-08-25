"""Deliver rendered video + post text to Telegram."""
import os
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, token, **kw):
    files = kw.pop("files", None)
    r = requests.post(API.format(token=token, method=method), data=kw, files=files, timeout=180)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def find_chat_id(token, status):
    cid = os.environ.get("TG_CHAT_ID", "").strip()
    if cid:
        return cid
    code, data = _call("getUpdates", token)
    if code == 200 and data.get("result"):
        for upd in reversed(data["result"]):
            msg = upd.get("message") or upd.get("channel_post")
            if msg:
                cid = str(msg["chat"]["id"])
                status.append(f"chat_id detected: {cid} (add as TG_CHAT_ID secret to pin it)")
                return cid
    status.append("FAIL: no TG_CHAT_ID and no updates — отправь боту /start и перезапусти")
    return None


def deliver(video: Path, caption: str, post_text: str, status) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        status.append("FAIL: no TG_BOT_TOKEN")
        return False
    cid = find_chat_id(token, status)
    if not cid:
        return False
    with video.open("rb") as f:
        code, data = _call("sendVideo", token, chat_id=cid, caption=caption[:1000],
                           supports_streaming="true", files={"video": (video.name, f, "video/mp4")})
    status.append(f"sendVideo -> {code}")
    if post_text:
        code2, _ = _call("sendMessage", token, chat_id=cid, text=post_text[:4000])
        status.append(f"sendMessage(post) -> {code2}")
    return code == 200

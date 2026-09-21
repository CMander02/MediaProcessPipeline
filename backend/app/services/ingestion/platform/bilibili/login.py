"""Bilibili web QR login, with credentials persisted through runtime settings.

Protocol reference: DownKyi.Core/BiliApi/Login/LoginQR.cs in Mu-L/downkyicore.
This implementation uses our HTTP client, settings store and UI lifecycle.
"""
from __future__ import annotations

import base64
from functools import lru_cache
import secrets
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

import httpx
import qrcode
import qrcode.image.svg

from app.core.network import httpx_client_kwargs
from app.core.settings import patch_runtime_settings
from .auth import _UA, _wbi_cache

_PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode"
_FIELDS = {"SESSDATA": "bilibili_sessdata", "bili_jct": "bilibili_bili_jct",
           "DedeUserID": "bilibili_dede_user_id"}


class BilibiliLoginService:
    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _client(self):
        return httpx.Client(**httpx_client_kwargs(_PASSPORT), timeout=15,
                            headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com/"})

    @staticmethod
    def _data(response: httpx.Response) -> dict:
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0 or not isinstance(body.get("data"), dict):
            raise RuntimeError("B 站登录接口返回异常，请稍后重试")
        return body["data"]

    def generate(self) -> dict:
        with self._client() as client:
            data = self._data(client.get(f"{_PASSPORT}/generate"))
        if not data.get("qrcode_key") or not data.get("url"):
            raise RuntimeError("B 站未返回登录二维码，请重试")
        qr = qrcode.make(data["url"], image_factory=qrcode.image.svg.SvgPathFillImage,
                         border=4)
        image = "data:image/svg+xml;base64," + base64.b64encode(qr.to_string()).decode("ascii")
        session_id = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items()
                              if value["expires"] > now}
            self._sessions[session_id] = {"key": data["qrcode_key"], "expires": now + 180}
        return {"session_id": session_id, "image": image, "expires_in": 180}

    def poll(self, session_id: str) -> dict:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session["expires"] <= time.monotonic():
                self._sessions.pop(session_id, None)
                return {"state": "expired", "message": "二维码已过期，请重新获取"}
            if session.get("result"):
                return session["result"]
            with self._client() as client:
                if "credentials" not in session:
                    response = client.get(f"{_PASSPORT}/poll", params={"qrcode_key": session["key"]})
                    data = self._data(response)
                    code = data.get("code")
                    states = {86101: ("waiting", "请使用哔哩哔哩 App 扫码"),
                              86090: ("scanned", "已扫码，请在手机上确认登录"),
                              86038: ("expired", "二维码已过期，请重新获取")}
                    if code in states:
                        state, message = states[code]
                        if state == "expired":
                            self._sessions.pop(session_id, None)
                        return {"state": state, "message": message}
                    if code != 0:
                        raise RuntimeError("B 站扫码状态异常，请重新获取二维码")
                    query = parse_qs(urlparse(data.get("url") or "").query)
                    credentials = {}
                    for name in _FIELDS:
                        value = response.cookies.get(name)
                        if not value:
                            value = quote(query.get(name, [""])[0], safe="%")
                        credentials[name] = value
                    if not all(credentials.values()):
                        raise RuntimeError("登录凭据不完整，请重新扫码")
                    session["credentials"] = credentials
                credentials = session["credentials"]
                # Check the new account before replacing a previously working login.
                nav = self._data(client.get("https://api.bilibili.com/x/web-interface/nav",
                    headers={"Cookie": "; ".join(f"{key}={value}" for key, value in credentials.items())}))
                if not nav.get("isLogin"):
                    raise RuntimeError("登录校验失败，请重新扫码")
            patch_runtime_settings({_FIELDS[key]: value for key, value in credentials.items()})
            _wbi_cache.clear()
            session.pop("credentials", None)
            session.pop("key", None)
            session["result"] = {"state": "success", "message": "登录成功，凭据已保存",
                                 "uid": str(nav.get("mid") or credentials["DedeUserID"])}
            return session["result"]


@lru_cache(maxsize=1)
def get_bilibili_login_service() -> BilibiliLoginService:
    return BilibiliLoginService()

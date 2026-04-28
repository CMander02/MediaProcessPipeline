"""Bilibili API calls with wbi signing.

All functions use stdlib only (urllib.request, hashlib, json, time).
"""

import hashlib
import json
import logging
import time
import urllib.parse
import urllib.request

from .auth import get_cookie, get_wbi_keys

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_REFERER = "https://www.bilibili.com/"

# WBI mixin-key encoding table (64 entries)
MIXIN_KEY_ENC_TAB = [
    46, 47, 18,  2, 53,  8, 23, 32, 15, 50, 10, 31, 58,  3, 45, 35,
    27, 43,  5, 49, 33,  9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48,  7, 16, 24, 55, 40, 61, 26, 17,  0,  1, 60, 51, 30,  4,
    22, 25, 54, 21, 56, 59,  6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def wbi_sign(params: dict) -> dict:
    """Add wts + w_rid to params dict and return signed copy.

    Follows the official wbi algorithm:
      1. Derive mixin_key from img_key + sub_key using MIXIN_KEY_ENC_TAB
      2. Add wts = current unix timestamp
      3. Sort params, strip forbidden chars from values
      4. Compute w_rid = md5(query_string + mixin_key)
    """
    img_key, sub_key = get_wbi_keys()
    raw_key = img_key + sub_key
    if not raw_key:
        # No wbi keys available — return params as-is (will likely get -352)
        logger.warning("wbi_sign: no wbi keys available, skipping signing")
        return dict(params)

    mixin_key = "".join(raw_key[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw_key))[:32]

    signed = dict(params)
    signed["wts"] = int(time.time())
    signed = dict(sorted(signed.items()))
    # Strip forbidden characters from values
    signed = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in signed.items()}

    query = urllib.parse.urlencode(signed)
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return signed


def http_json(url: str, cookie: str = "", referer: str = _REFERER) -> dict:
    """GET url, parse JSON, raise if HTTP != 200 or code != 0.

    Returns the full parsed JSON dict (not just .data).
    """
    headers: dict[str, str] = {
        "User-Agent": _UA,
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url[:80]}")
        body = json.loads(resp.read())

    code = body.get("code")
    if code != 0:
        msg = body.get("message") or body.get("msg") or ""
        raise RuntimeError(f"Bilibili API code={code} msg={msg!r} url={url[:80]}")

    return body


def view(bvid: str) -> dict:
    """GET /x/web-interface/view — returns data dict."""
    cookie = get_cookie()
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    body = http_json(url, cookie=cookie)
    return body.get("data") or {}


def player_v2(bvid: str, aid: int, cid: int) -> dict:
    """GET /x/player/wbi/v2 — wbi-signed, returns data dict."""
    cookie = get_cookie()
    params = wbi_sign({"bvid": bvid, "aid": aid, "cid": cid})
    qs = urllib.parse.urlencode(params)
    url = f"https://api.bilibili.com/x/player/wbi/v2?{qs}"
    body = http_json(url, cookie=cookie, referer=f"https://www.bilibili.com/video/{bvid}")
    return body.get("data") or {}


def playurl(bvid: str, aid: int, cid: int, qn: int = 64, fnval: int = 16) -> dict:
    """GET /x/player/wbi/playurl — wbi-signed, returns data dict.

    fnval=16 → DASH, fnval=0 → FLV fallback.
    """
    cookie = get_cookie()
    params = wbi_sign({"bvid": bvid, "aid": aid, "cid": cid, "qn": qn, "fnval": fnval})
    qs = urllib.parse.urlencode(params)
    url = f"https://api.bilibili.com/x/player/wbi/playurl?{qs}"
    body = http_json(url, cookie=cookie, referer=f"https://www.bilibili.com/video/{bvid}")
    return body.get("data") or {}


def conclusion(bvid: str, aid: int, cid: int) -> dict:
    """GET /x/web-interface/view/conclusion/get — wbi-signed, returns data dict.

    Returns empty dict on inner code != 0 (video has no AI summary).
    """
    cookie = get_cookie()
    params = wbi_sign({"bvid": bvid, "aid": aid, "cid": cid})
    qs = urllib.parse.urlencode(params)
    url = f"https://api.bilibili.com/x/web-interface/view/conclusion/get?{qs}"
    try:
        body = http_json(url, cookie=cookie, referer=f"https://www.bilibili.com/video/{bvid}")
        return body.get("data") or {}
    except RuntimeError as e:
        logger.debug(f"conclusion API: {e}")
        return {}


def subtitle_url_matches_video(url: str, aid: int, cid: int) -> bool:
    """Verify the blob filename starts with str(aid)+str(cid).

    Bilibili subtitle blob paths embed {aid}{cid} in the filename.
    An unsigned player/v2 response may return a blob for a *different*
    video — this check catches that case.
    """
    stem = url.split("/")[-1].split("?")[0]
    return stem.startswith(f"{aid}{cid}")

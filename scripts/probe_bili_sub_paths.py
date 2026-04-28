"""Cross-check Bilibili subtitle endpoints to see if wbi-signed paths return
different (and correct) content vs the un-signed path our pipeline uses.

Compares for a given BV:
  (A) /x/player/v2          — what backend/app/services/ingestion/ytdlp.py uses now
  (B) /x/player/wbi/v2      — what downkyicore uses (wbi signed)
  (C) /x/web-interface/view/conclusion/get   — AI summary endpoint, contains model_result.subtitle
  (D) the raw aisubtitle.hdslb.com blob from each of (A)/(B)

Auth: SESSDATA from backend/tools/bbdown/BBDown.data
WBI: nav-derived img_key/sub_key (impl per docs/misc/sign/wbi.md)
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.parse
import urllib.request
from functools import reduce
from hashlib import md5
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = ROOT / "backend" / "tools" / "bbdown" / "BBDown.data"
OUT_DIR = ROOT / "agentspace" / "bili_sub_path_probe"
OUT_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0 Safari/537.36"

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def load_cookie() -> str:
    return COOKIE_FILE.read_text(encoding="utf-8", errors="ignore").strip() if COOKIE_FILE.exists() else ""


def http_json(url: str, cookie: str = "", referer: str = "https://www.bilibili.com/") -> dict:
    headers = {"User-Agent": UA, "Referer": referer}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def http_get(url: str) -> bytes:
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def get_mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


def get_wbi_keys(cookie: str) -> tuple[str, str]:
    nav = http_json("https://api.bilibili.com/x/web-interface/nav", cookie=cookie)
    img_url = nav["data"]["wbi_img"]["img_url"]
    sub_url = nav["data"]["wbi_img"]["sub_url"]
    img_key = img_url.rsplit("/", 1)[1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]
    return img_key, sub_key


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin_key = get_mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    params["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
    return params


def fmt_ts(sec: float) -> str:
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def summarize_body(body: list[dict]) -> str:
    if not body:
        return "<empty>"
    n = len(body)
    last_t = body[-1].get("from", 0)
    head = " | ".join((c.get("content", "") or "")[:30] for c in body[:3])
    return f"{n} cues, last={last_t:.1f}s; head: {head}"


def probe_bv(bv: str, cookie: str, img_key: str, sub_key: str) -> None:
    print("=" * 78)
    print(f"BV: {bv}")
    referer = f"https://www.bilibili.com/video/{bv}"

    # -- view → cid --
    view = http_json(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bv}",
        cookie=cookie, referer=referer,
    )
    if view.get("code") != 0:
        print(f"  view ERR: code={view.get('code')} msg={view.get('message')}")
        return
    title = view["data"]["title"]
    aid = view["data"]["aid"]
    pages = view["data"].get("pages") or []
    cid = pages[0]["cid"]; dur = pages[0].get("duration")
    print(f"  title: {title}")
    print(f"  aid={aid} cid={cid} duration={dur}s\n")

    # -- (A) un-signed player/v2 --
    print("  -- (A) /x/player/v2 (no wbi) --")
    try:
        a = http_json(
            f"https://api.bilibili.com/x/player/v2?bvid={bv}&cid={cid}",
            cookie=cookie, referer=referer,
        )
        a_subs = (a.get("data") or {}).get("subtitle", {}).get("subtitles", []) or []
        a_url_map = {}
        for s in a_subs:
            print(f"     · type={s.get('type')} lan={s.get('lan')} ai_status={s.get('ai_status')} url={s.get('subtitle_url','')[:120]}")
            a_url_map[s.get("lan")] = s.get("subtitle_url", "")
    except Exception as e:
        print(f"     ERR: {e}")
        a_subs, a_url_map = [], {}

    # -- (B) wbi-signed player/wbi/v2 --
    print("\n  -- (B) /x/player/wbi/v2 (wbi signed) --")
    params_b = {"aid": aid, "bvid": bv, "cid": cid}
    signed = enc_wbi(params_b, img_key, sub_key)
    qs = urllib.parse.urlencode(signed)
    try:
        b = http_json(
            f"https://api.bilibili.com/x/player/wbi/v2?{qs}",
            cookie=cookie, referer=referer,
        )
        b_subs = (b.get("data") or {}).get("subtitle", {}).get("subtitles", []) or []
        b_url_map = {}
        for s in b_subs:
            print(f"     · type={s.get('type')} lan={s.get('lan')} ai_status={s.get('ai_status')} url={s.get('subtitle_url','')[:120]}")
            b_url_map[s.get("lan")] = s.get("subtitle_url", "")
    except Exception as e:
        print(f"     ERR: {e}")
        b_subs, b_url_map = [], {}

    # Compare URLs A vs B
    if a_url_map or b_url_map:
        print("\n  -- compare A vs B subtitle_url --")
        for lan in sorted(set(a_url_map) | set(b_url_map)):
            ua = a_url_map.get(lan, "")
            ub = b_url_map.get(lan, "")
            same = "SAME" if ua == ub else "DIFF"
            print(f"     [{lan}] {same}")
            if not same == "SAME":
                print(f"       A: {ua[:120]}")
                print(f"       B: {ub[:120]}")

    # -- (C) view/conclusion/get (AI summary) --
    print("\n  -- (C) /x/web-interface/view/conclusion/get (AI summary, wbi) --")
    params_c = {"aid": aid, "bvid": bv, "cid": cid}
    signed_c = enc_wbi(params_c, img_key, sub_key)
    qs_c = urllib.parse.urlencode(signed_c)
    try:
        c = http_json(
            f"https://api.bilibili.com/x/web-interface/view/conclusion/get?{qs_c}",
            cookie=cookie, referer=referer,
        )
        cdata = c.get("data") or {}
        c_inner = cdata.get("code")
        mr = cdata.get("model_result") or {}
        c_summary = (mr.get("summary") or "")[:120]
        c_subs = mr.get("subtitle") or []
        print(f"     outer code={c.get('code')} inner code={c_inner} summary: {c_summary}")
        print(f"     #ai-subtitle items: {len(c_subs)}")
        if c_subs:
            for cue in c_subs[:3]:
                # field schema may be {timestamp, content} or similar
                print(f"       {cue}")
        c_outline = mr.get("outline") or []
        if c_outline:
            print(f"     outline: {len(c_outline)} sections")
            for o in c_outline[:3]:
                print(f"       - [{o.get('timestamp')}] {o.get('title','')}")
    except Exception as e:
        print(f"     ERR: {e}")

    # -- (D) download blob from A and (if different) from B --
    print("\n  -- (D) blob bodies --")
    for label, url in [("A", next(iter(a_url_map.values()), "")), ("B", next(iter(b_url_map.values()), ""))]:
        if not url:
            print(f"     [{label}] no url"); continue
        try:
            blob = http_get(url)
            j = json.loads(blob)
            print(f"     [{label}] {summarize_body(j.get('body') or [])}")
            (OUT_DIR / f"{bv}_{label}.json").write_bytes(blob)
        except Exception as e:
            print(f"     [{label}] ERR: {e}")
    print()


def main() -> int:
    cookie = load_cookie()
    print(f"[cookie] {len(cookie)} bytes, SESSDATA: {'SESSDATA=' in cookie}")
    img_key, sub_key = get_wbi_keys(cookie)
    print(f"[wbi] img_key={img_key[:8]}... sub_key={sub_key[:8]}...")
    print()

    # Three candidates: today's broken AI-12, today's broken Bengio (which now seems "fixed"),
    # plus a fresh random B站 video for control. Pick the targets via argv if given.
    targets = sys.argv[1:] or [
        "BV13ZDLB4EW5",  # AI-12 (broken) — water-cooling content was returned
        "BV11mZ5BfE3J",  # Bengio (broken @ task time, seemingly OK now)
        "BV1V1oqBiEij",  # the in-progress one
    ]
    for bv in targets:
        try:
            probe_bv(bv, cookie, img_key, sub_key)
        except Exception as e:
            print(f"[{bv}] FATAL: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

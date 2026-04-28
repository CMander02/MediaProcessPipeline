"""Probe today's Bilibili tasks via the *current* pipeline path.

Mirrors backend/app/services/ingestion/ytdlp.py::_download_bilibili_subtitle but
prints richer diagnostics so we can see whether the subtitle the pipeline
fetched matches the actual video.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = ROOT / "backend" / "tools" / "bbdown" / "BBDown.data"
OUT_DIR = ROOT / "agentspace" / "bili_sub_probe_today"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Today's tasks: (BV, recorded title from DB)
TASKS = [
    ("BV122QFBkEdX", "AI鸿沟，新一轮收入大洗牌的起点"),
    ("BV11mZ5BfE3J", "【硅谷女孩播客】对话AI教父Yoshua Bengio：未来五年将彻底改变人类社会"),
    ("BV13ZDLB4EW5", "AI-12 Agent 一线实战：落地经验与 Know-How"),
    # In progress / queued (for completeness)
    ("BV1V1oqBiEij", "AI时代，什么让你不可替代？【the prompt】"),
    ("BV15af3BTEPy", "设计师在AI时代出路很清晰：远离Figma，尽早写代码｜Cursor设计负责人Ryo Lu"),
    ("BV1meoLBeE5r", "AI原生一代：组织与人的进化"),
    ("BV1he9gBEEg2", "Anthropic CEO 深度访谈"),
]


def load_cookie() -> str:
    if not COOKIE_FILE.exists():
        return ""
    return COOKIE_FILE.read_text(encoding="utf-8", errors="ignore").strip()


def http_json(url: str, cookie: str, referer: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cookie": cookie,
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def http_get(url: str) -> bytes:
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def fmt_ts(sec: float) -> str:
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def main() -> int:
    cookie = load_cookie()
    print(f"[cookie] {len(cookie)} bytes, SESSDATA present: {'SESSDATA=' in cookie}\n")

    for bv, db_title in TASKS:
        referer = f"https://www.bilibili.com/video/{bv}"
        print("=" * 78)
        print(f"{bv}")
        print(f"  DB title: {db_title}")
        try:
            view = http_json(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bv}",
                cookie, referer,
            )
        except Exception as e:
            print(f"  view API ERR: {e}\n")
            continue
        if view.get("code") != 0:
            print(f"  view API code={view.get('code')} msg={view.get('message')}\n")
            continue
        data = view["data"]
        api_title = data.get("title")
        pages = data.get("pages") or []
        print(f"  API title: {api_title}")
        title_match = "OK" if api_title and api_title.strip() == db_title.strip() else "!! MISMATCH"
        print(f"  title check: {title_match}")
        print(f"  #pages: {len(pages)}  duration(view): {data.get('duration')}s")

        if not pages:
            print()
            continue

        for p in pages:
            cid = p["cid"]
            part = p.get("part", "")
            dur = p.get("duration")
            print(f"  -- P{p['page']} cid={cid} dur={dur}s  part={part[:60]!r}")
            try:
                pv2 = http_json(
                    f"https://api.bilibili.com/x/player/v2?bvid={bv}&cid={cid}",
                    cookie, referer,
                )
            except Exception as e:
                print(f"     player/v2 ERR: {e}")
                continue
            subs = (pv2.get("data") or {}).get("subtitle", {}).get("subtitles", []) or []
            print(f"     subtitle tracks: {len(subs)}")
            for s in subs:
                lan = s.get("lan"); lan_doc = s.get("lan_doc")
                s_type = s.get("type")  # 0=CC, 1=AI
                ai_status = s.get("ai_status")
                url = s.get("subtitle_url", "")
                t_label = "CC" if s_type == 0 else "AI"
                print(f"       · {t_label} lan={lan} ({lan_doc}) ai_status={ai_status}")
                print(f"         url: {url[:110]}")
                if not url:
                    continue
                try:
                    blob = http_get(url)
                    j = json.loads(blob)
                except Exception as e:
                    print(f"         download ERR: {e}")
                    continue
                body = j.get("body", [])
                print(f"         #cues: {len(body)}")
                fn = OUT_DIR / f"{bv}_P{p['page']}_{lan}_type{s_type}.json"
                fn.write_bytes(blob)
                for cue in body[:4]:
                    print(f"         [{fmt_ts(cue['from'])}] {cue.get('content','')[:80]}")
                if len(body) > 5:
                    print("         ...")
                    for cue in body[-2:]:
                        print(f"         [{fmt_ts(cue['from'])}] {cue.get('content','')[:80]}")
                if body and dur:
                    last_t = body[-1]["from"]
                    ratio = last_t / dur if dur else 0
                    flag = "" if 0.6 <= ratio <= 1.05 else "  !! TIMESTAMP MISMATCH"
                    print(f"         last_cue={last_t:.1f}s vs video={dur}s  (ratio={ratio:.2f}){flag}")
        print()

    print(f"\nSaved raw JSONs to: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

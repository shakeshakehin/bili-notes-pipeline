"""Download all subtitle tracks of a Bilibili video (multi-P or single) to per-part text files.

Bypasses the bilibili-cli command layer (which 412s / drops credentials); calls
bilibili_api directly with the saved credential.

Usage:
    python bili_subtitle_download.py BVID [--out DIR]

Requires bilibili_api + aiohttp — both present in the bilibili-cli uv tool env:
    /c/Users/Administrator/AppData/Roaming/uv/tools/bilibili-cli/Scripts/python.exe

Prereq: ~/.bilibili-cli/credential.json exists (QR login done once).
"""
import argparse
import asyncio
import json
import pathlib

import aiohttp
from bilibili_api import video
from bilibili_api.utils.network import Credential


def load_cred(path: str | None = None) -> Credential:
    p = pathlib.Path(path or (pathlib.Path.home() / ".bilibili-cli" / "credential.json"))
    d = json.loads(p.read_text())
    return Credential(
        sessdata=d.get("sessdata", ""),
        bili_jct=d.get("bili_jct", ""),
        buvid3=d.get("buvid3", ""),
        buvid4=d.get("buvid4", ""),
        dedeuserid=d.get("dedeuserid", ""),
        ac_time_value=d.get("ac_time_value", ""),
    )


async def main(bvid: str, out: str, max_pages: int | None = None) -> None:
    cred = load_cred()
    v = video.Video(bvid=bvid, credential=cred)
    pages = await v.get_pages()
    if max_pages:
        pages = pages[:max_pages]
    out_dir = pathlib.Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as s:
        for p in pages:
            pi = await v.get_player_info(cid=p["cid"])
            subs = (pi.get("subtitle") or {}).get("subtitles") or []
            if not subs:
                print(f"P{p['page']} NO SUB")
                continue
            url = subs[0]["subtitle_url"]
            if url.startswith("//"):
                url = "https:" + url
            async with s.get(url) as r:
                data = await r.json(content_type=None)
            text = "\n".join(x.get("content", "") for x in (data.get("body") or []))
            safe = "".join(c for c in p["part"] if c.isalnum() or "\u4e00" <= c <= "\u9fff")
            name = f"P{p['page']:02d}_{safe[:20]}.txt"
            (out_dir / name).write_text(text, encoding="utf-8")
            print(f"P{p['page']} saved {len(text)} chars -> {name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download all part subtitles of a Bilibili video")
    ap.add_argument("bvid")
    ap.add_argument("--out", default="subtitles")
    ap.add_argument("--max-pages", type=int, default=None,
                   help="只抓前 N 个分 P（默认全部）")
    args = ap.parse_args()
    asyncio.run(main(args.bvid, args.out, args.max_pages))

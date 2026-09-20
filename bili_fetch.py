#!/usr/bin/env python3
"""bili-fetch: 确定性 B站 视频抓取管道（零 LLM 成本）。

输入 BV 号或完整 URL，输出：
  - 元数据 markdown（标题/UP主/时长/互动数据/简介/发布时间）
  - 干净字幕文本（无时间戳、去连续重复行）
可选：--all 抓全部分 P；--ai 附 B站 官方 AI 摘要（API 数据，非 LLM）。

依赖 bilibili_api（随 bilibili-cli 安装）。运行：
  python bili_fetch.py BVxxxxx [-o 输出目录] [--all] [--meta-only|--subtitle-only] [--ai] [--json]

凭证读取 ~/.bilibili-cli/credential.json（bili login 生成的）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import aiohttp
from bilibili_api import video as bili_video
from bilibili_api.utils.network import Credential

CONFIG_FILE = Path.home() / ".bilibili-cli" / "credential.json"
DEFAULT_OUT = Path(r"E:\AIbulid\hermesonly\bili_fetch")


def load_credential() -> Credential | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not d.get("sessdata"):
            return None
        return Credential(
            sessdata=d.get("sessdata", ""),
            bili_jct=d.get("bili_jct", ""),
            ac_time_value=d.get("ac_time_value", ""),
            buvid3=d.get("buvid3", ""),
            buvid4=d.get("buvid4", ""),
            dedeuserid=d.get("dedeuserid", ""),
        )
    except Exception:
        return None


def extract_bvid(bv_or_url: str) -> str:
    m = re.search(r"BV[0-9A-Za-z]{10}", bv_or_url)
    if not m:
        sys.exit(f"错误：无法从 {bv_or_url!r} 提取 BV 号")
    return m.group(0)


def sanitize(name: str, maxlen: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip()
    return name[:maxlen] or "untitled"


def fmt_duration(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def get_subtitle_text(v: bili_video.Video, cid: int) -> str:
    """下载并拼接近乎干净的字幕文本（content 拼接 + 去连续重复行）。"""
    pi = await v.get_player_info(cid=cid)
    subs = (pi.get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return ""
    url = subs[0]["subtitle_url"]
    if url.startswith("//"):
        url = "https:" + url
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json(content_type=None)
    lines = []
    prev = ""
    for item in data.get("body") or []:
        c = (item.get("content") or "").strip()
        if not c or c == prev:
            continue
        lines.append(c)
        prev = c
    return "\n".join(lines)


async def fetch_one(
    v: bili_video.Video,
    info: dict,
    page: dict,
    out_dir: Path,
    meta_only: bool,
    subtitle_only: bool,
    with_ai: bool,
) -> dict:
    cid = page["cid"]
    pn = page["page"]
    part = page.get("part") or ""
    title = info.get("title", "untitled")
    base = f"{sanitize(title)}"
    if pn > 1:
        base = f"{sanitize(title)}_P{pn:02d}_{sanitize(part, 30)}"

    result = {"page": pn, "part": part, "title": title}

    if not subtitle_only:
        meta_md = [
            f"# {title}",
            "",
            f"- **BV**: {info.get('bvid', '')}",
            f"- **UP主**: {info.get('owner', {}).get('name', '')} (uid {info.get('owner', {}).get('mid', '')})",
            f"- **时长**: {fmt_duration(page.get('duration', 0))}",
            f"- **播放**: {info.get('stat', {}).get('view', 0)} · **弹幕**: {info.get('stat', {}).get('danmaku', 0)}",
            f"- **点赞**: {info.get('stat', {}).get('like', 0)} · **投币**: {info.get('stat', {}).get('coin', 0)}",
            f"- **收藏**: {info.get('stat', {}).get('favorite', 0)} · **分享**: {info.get('stat', {}).get('share', 0)}",
        ]
        if info.get("pubdate"):
            meta_md.append(f"- **发布**: {time.strftime('%Y-%m-%d', time.localtime(info['pubdate']))}")
        if info.get("tname"):
            meta_md.append(f"- **分区**: {info['tname']}")
        desc = (info.get("desc") or "").strip()
        if desc:
            meta_md += ["", "## 简介", "", desc]
        if pn > 1:
            meta_md.insert(1, f"\n> 分 P {pn}: {part}\n")
        (out_dir / f"{base}.meta.md").write_text("\n".join(meta_md), encoding="utf-8")
        result["meta_file"] = f"{base}.meta.md"

        if with_ai:
            try:
                ai = await v.get_ai_conclusion(cid=cid)
                summary = (ai.get("model_result") or {}).get("summary", "")
                if summary:
                    (out_dir / f"{base}.ai.md").write_text(summary, encoding="utf-8")
                    result["ai_file"] = f"{base}.ai.md"
            except Exception as e:
                result["ai_note"] = f"AI 摘要不可用: {type(e).__name__}"

    if not meta_only:
        try:
            text = await get_subtitle_text(v, cid)
        except Exception as e:
            result["subtitle_error"] = f"字幕获取失败: {type(e).__name__}: {e}"
            return result
        if text:
            (out_dir / f"{base}.subtitle.txt").write_text(text, encoding="utf-8")
            result["subtitle_file"] = f"{base}.subtitle.txt"
            result["subtitle_chars"] = len(text)
        else:
            result["subtitle_note"] = "无可用字幕"
    return result


async def run(bv_or_url: str, out_dir: Path, all_pages: bool, meta_only: bool,
              subtitle_only: bool, with_ai: bool, as_json: bool) -> None:
    bvid = extract_bvid(bv_or_url)
    cred = load_credential()
    if cred is None:
        sys.exit("错误：未找到有效凭证 ~/.bilibili-cli/credential.json，请先 bili login")
    v = bili_video.Video(bvid=bvid, credential=cred)
    info = await v.get_info()
    pages = await v.get_pages()
    pages_to_fetch = pages if all_pages else pages[:1]

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for page in pages_to_fetch:
        results.append(await fetch_one(v, info, page, out_dir, meta_only, subtitle_only, with_ai))

    if as_json:
        print(json.dumps({"bvid": bvid, "results": results}, ensure_ascii=False, indent=2))
        return

    print(f"BV {bvid} · {info.get('title', '')}")
    print(f"分 P {len(pages_to_fetch)}/{len(pages)} · 输出目录 {out_dir}")
    for r in results:
        tags = []
        if r.get("meta_file"):
            tags.append(f"meta→{r['meta_file']}")
        if r.get("ai_file"):
            tags.append(f"ai→{r['ai_file']}")
        if r.get("subtitle_file"):
            tags.append(f"subtitle({r['subtitle_chars']}字)→{r['subtitle_file']}")
        elif r.get("subtitle_note"):
            tags.append(r["subtitle_note"])
        if r.get("subtitle_error"):
            tags.append(r["subtitle_error"])
        prefix = f"[P{r['page']}] " if len(pages_to_fetch) > 1 else ""
        print(f"  {prefix}{r.get('part') or r['title']}: " + ", ".join(tags))


def main() -> None:
    p = argparse.ArgumentParser(description="确定性 B站 视频抓取管道（零 LLM）")
    p.add_argument("bv_or_url", help="BV 号或完整链接")
    p.add_argument("-o", "--out", default=str(DEFAULT_OUT), help="输出目录")
    p.add_argument("--all", action="store_true", help="抓取全部分 P（默认仅 P1）")
    p.add_argument("--meta-only", action="store_true", help="只抓元数据")
    p.add_argument("--subtitle-only", action="store_true", help="只抓字幕")
    p.add_argument("--ai", action="store_true", help="附加 B站 官方 AI 摘要")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    args = p.parse_args()
    asyncio.run(run(args.bv_or_url, Path(args.out), args.all, args.meta_only,
                    args.subtitle_only, args.ai, args.json))


if __name__ == "__main__":
    main()

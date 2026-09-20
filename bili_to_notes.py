#!/usr/bin/env python3
"""bili_to_notes: B 站 BV 号/链接 → 逻辑大纲 + 树状导图 PNG，一条命令跑完。

流水线: 抓字幕(bilibili_api) → LLM 逻辑梳理(JSON schema) → SVG 导图 → Chrome 截图

用法:
  python bili_to_notes.py <BV号 或 链接> [--out 目录] [--no-png]

产物(在 <out>/<BV>/ 下):
  src.txt            干净字幕
  src.outline.md     分层大纲 + 概念表 + 主干链 + mermaid
  src.outline.json   结构化 IR
  src.outline.mindmap.svg   树状导图
  导图.png           导图位图(1.6x，便于查看/分享)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HOME = Path(os.path.expanduser("~"))
UVPY = HOME / "AppData/Roaming/uv/tools/bilibili-cli/Scripts/python.exe"
WORK = Path(r"E:\AIbulid\hermesonly")
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
DEFAULT_OUT = WORK / "notes_out"
DSF = 1.6


def run(cmd: list, label: str) -> str:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        print(f"[失败] {label}")
        print((r.stdout or "")[-1500:])
        print((r.stderr or "")[-1500:], file=sys.stderr)
        raise SystemExit(1)
    return r.stdout or ""


def extract_bvid(s: str) -> str:
    m = re.search(r"BV[0-9A-Za-z]{10}", s)
    if not m:
        raise SystemExit(f"无法从 {s!r} 提取 BV 号")
    return m.group(0)


def svg_size(svg_path: Path) -> tuple:
    head = svg_path.read_text(encoding="utf-8")[:400]
    m = re.search(r'width="(\d+)"\s+height="(\d+)"', head)
    return (int(m.group(1)), int(m.group(2))) if m else (1400, 2000)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bv_or_url")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()

    bvid = extract_bvid(args.bv_or_url)
    base = Path(args.out) / bvid
    raw = base / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    print(f"==> 视频 {bvid} | 工作目录 {base}")

    # 1) 抓字幕（需 bilibili_api 环境）
    if not list(raw.glob("*.subtitle.txt")):
        run([UVPY, WORK / "bili_fetch.py", bvid, "-o", raw, "--subtitle-only"], "抓取字幕")
    subs = sorted(raw.glob("*.subtitle.txt"))
    if not subs:
        raise SystemExit(f"未抓到字幕（该视频可能没有 CC 字幕）：{raw}")
    src = base / "src.txt"
    shutil.copy(subs[0], src)
    print(f"==> 字幕 {src.name}（{len(src.read_text(encoding='utf-8'))} 字，源：{subs[0].name[:40]}）")

    # 2) 逻辑梳理
    run([sys.executable, WORK / "notes_structurer.py", src, "-o", base], "逻辑梳理")
    oj = base / "src.outline.json"
    print(f"==> 大纲 {base / 'src.outline.md'}")

    # 3) 出图（仅逻辑型走树状导图；其他类型 markdown 内已有 mermaid 代码块）
    import json as _json
    try:
        _d = _json.loads(oj.read_text(encoding="utf-8"))
        vtype = _d.get("type", "逻辑型")
    except Exception:
        vtype = "逻辑型"
    if vtype == "逻辑型":
        svg = base / "src.outline.mindmap.svg"
        run([sys.executable, WORK / "outline_to_svg.py", oj, "-o", svg], "SVG 渲染")
        if not args.no_png and CHROME.exists():
            run([sys.executable, WORK / "svg2png.py", svg, base / "导图.png"], "PNG 截图")
            png = base / "导图.png"
            if png.exists():
                print(f"==> 导图 {png}")
    else:
        print(f"==> 类型 {vtype}：笔记含 mermaid 代码块（Obsidian 可渲染），跳过树状导图")

    print("\n=== 产物 ===")
    for f in sorted(base.rglob("*")):
        if f.is_file() and f.suffix in (".txt", ".md", ".json", ".svg", ".png") and "raw" not in f.parts:
            print(f"  {f}  ({f.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

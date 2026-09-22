#!/usr/bin/env python3
"""端到端笔记管道：一条命令产出 完整版 + 浓缩版 + 双份复核。

用法:
  python eval/notes_pipeline.py <subtitle.txt> -o <out_dir>
    [--condense]  逻辑型追加浓缩 IR + 浓缩导图
    [--judge]     复核完整版(和浓缩版)的忠实度，出真幻觉率
    [--obsidian]  产物拷进 Obsidian 测试文件夹
    [--name XXX]  Obsidian 文件名前缀（默认取字幕文件名）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

WORK = Path(r"E:\AIbulid\hermesonly")
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OBS = Path(r"C:\Users\Administrator\Documents\Obsidian Vault\07-AI学习\测试-B站笔记输出")


def run(cmd, timeout=400):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        print("  !", (r.stdout + r.stderr).strip()[-300:])
    return (r.stdout or "") + (r.stderr or "")


def svg_to_png(svg: Path, png: Path) -> None:
    m = re.search(r'width="(\d+)" height="(\d+)"', svg.read_text(encoding="utf-8"))
    W, H = int(m.group(1)), int(m.group(2))
    url = "file:///" + svg.resolve().as_posix()
    for attempt in range(3):
        # 每次独立 user-data-dir：避免与残留 Chrome 实例/锁冲突
        tmpd = rf"C:\Users\Administrator\AppData\Local\Temp\chrome-pipe-{uuid.uuid4().hex[:8]}"
        run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--force-device-scale-factor=1.6", f"--user-data-dir={tmpd}",
             f"--window-size={W},{H}", f"--screenshot={png.resolve()}", url])
        if png.exists() and png.stat().st_size > 1000:
            return
    print(f"  ! 截图失败(3次): {png}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("subtitle")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--condense", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--no-revise", action="store_true",
                    help="跳过 seesee 仲裁→修订闭环（默认开启：画图用修订后树）")
    ap.add_argument("--obsidian", action="store_true")
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    src = Path(args.subtitle)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = args.name or re.sub(r"[^\w\u4e00-\u9fff]", "_", src.stem)[:40]

    # 1. 结构化 → 完整 IR
    print("[1/4] 结构化", flush=True)
    run([PY, WORK / "notes_structurer.py", str(src), "-o", str(out)])
    ojs = sorted(out.glob("*.outline.json"))
    if not ojs:
        sys.exit("结构化失败")
    full_ir = ojs[0]
    typ = json.loads(full_ir.read_text(encoding="utf-8")).get("type", "?")
    print(f"     类型: {typ}")

    # 2. 确定性修复（零 LLM）
    print("[2/5] 确定性字幕修复", flush=True)
    run([PY, WORK / "eval" / "subtitle_repair.py", str(src),
         "-o", str(out / "repaired")])

    # 3. seesee 仲裁（外部 Kimi，输出 verdict.json）
    print("[3/5] seesee 仲裁 (kimi-k2.8-preview)", flush=True)
    run([PY, WORK / "eval" / "step5_seesee.py", str(out), "--name", name, "--direct"])

    # 4. 回流修订（verdict → patch IR → validate）
    draw_ir = full_ir
    revs: list = []
    if not args.no_revise:
        print("[4/5] 修订树（apply verdict）", flush=True)
        run([PY, WORK / "eval" / "apply_verdict.py", str(out)])
        revs = sorted(out.glob("*_revised.json"))
        if revs:
            draw_ir = revs[0]
            print(f"     画图源: {draw_ir.name}")

    # 5. 画图（完整版用修订后树）
    print("[5/5] 完整版导图", flush=True)
    run([PY, WORK / "outline_to_svg.py", str(draw_ir), "-o", str(out / "tree.svg")])
    svg_to_png(out / "tree.svg", out / "tree.png")

    condensed_ir = None
    if args.condense:
        if typ == "逻辑型":
            print("     浓缩版", flush=True)
            run([PY, WORK / "eval" / "condense_tree.py", str(draw_ir),
                 "-o", str(out / "condensed.json")])
            condensed_ir = out / "condensed.json"
            run([PY, WORK / "outline_to_svg.py", str(condensed_ir),
                 "-o", str(out / "tree_condensed.svg")])
            svg_to_png(out / "tree_condensed.svg", out / "tree_condensed.png")
        else:
            print("     非逻辑型，跳过浓缩")

    # 6. Obsidian
    if args.obsidian:
        OBS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(draw_ir, OBS / f"{name}.json")
        for md in out.glob("*.outline.md"):
            shutil.copy2(md, OBS / f"{name}.md")
        shutil.copy2(out / "tree.png", OBS / f"{name}_导图.png")
        if condensed_ir:
            shutil.copy2(out / "tree_condensed.png", OBS / f"{name}_浓缩导图.png")
        if (out / "verdict.json").exists():
            shutil.copy2(out / "verdict.json", OBS / f"{name}_verdict.json")
        if (out / "step5_seesee_result.md").exists():
            shutil.copy2(out / "step5_seesee_result.md", OBS / f"{name}_seesee复核.md")
        if revs:
            shutil.copy2(revs[0], OBS / f"{name}_revised.json")
        if (out / "repaired.txt").exists():
            shutil.copy2(out / "repaired.txt", OBS / f"{name}_清洗后字幕.txt")
        print(f"     → Obsidian: {name}")

    print(f"完成: {out}  (类型 {typ})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""harness_run.py — 单模型自主运行包装（可分发版）。

只需配置一个 LLM 接入点（base_url/api_key/model）+ 一个字幕目录，
即可全自动：清洗 → 树生成 → 同模型校验 → 修订 → 画图 → 归档 Obsidian。
不依赖 Hermes profile / se-esee / B站凭证——只部署了大模型的 harness 也能跑。

配置优先级：环境变量 > harness.env（项目根）> 本机 manager config 回退
  LLM_BASE_URL    如 https://ark.cn-beijing.volces.com/api/coding/v3
  LLM_API_KEY
  LLM_MODEL
  SUBTITLE_DIR    字幕目录（默认 ./inbox/，扫描 *.txt）
  OBSIDIAN_ROOT   Obsidian 输出根（默认 <本机 vault>/07-AI学习/测试-B站笔记输出）

用法:
  python harness_run.py                     # 处理 inbox/ 全部字幕
  python harness_run.py <字幕文件>           # 指定单份
  python harness_run.py --no-png            # 无 Chrome 环境：只出 SVG 不出 PNG
  python harness_run.py --check-only        # 只跑 清洗+树生成+校验（不画图/不归档）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

WORK = Path(r"E:\AIbulid\hermesonly")
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OBS_ROOT = Path(r"C:\Users\Administrator\Documents\Obsidian Vault\07-AI学习\测试-B站笔记输出")
DEFAULT_INBOX = WORK / "inbox"


def load_cfg() -> dict:
    """base_url / api_key / model / subtitle_dir / obsidian_root"""
    cfg = {"base_url": "", "api_key": "", "model": "deepseek-v4-flash",
           "subtitle_dir": str(DEFAULT_INBOX), "obsidian_root": str(OBS_ROOT)}

    # 1) harness.env（项目根）
    henv = WORK / "harness.env"
    if henv.exists():
        for line in henv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                         "SUBTITLE_DIR", "OBSIDIAN_ROOT"):
                    cfg[k.replace("LLM_", "").lower() if k.startswith("LLM_")
                        else k.lower()] = v

    # 2) 环境变量优先
    env_map = {"LLM_BASE_URL": "base_url", "LLM_API_KEY": "api_key", "LLM_MODEL": "model",
               "SUBTITLE_DIR": "subtitle_dir", "OBSIDIAN_ROOT": "obsidian_root"}
    import os
    for ev, k in env_map.items():
        if os.environ.get(ev):
            cfg[k] = os.environ[ev]

    # 3) 回退：本机 manager config（仅当 harness.env 没给全时）
    if not cfg["base_url"] or not cfg["api_key"]:
        try:
            sys.path.insert(0, str(WORK))
            import notes_structurer as ns
            mc = ns.load_cfg(ns.CONFIG)
            if not cfg["base_url"]:
                cfg["base_url"] = mc.get("base_url") or ""
            if not cfg["api_key"]:
                cfg["api_key"] = mc.get("api_key") or ""
            if not cfg["model"] or cfg["model"] == "deepseek-v4-flash":
                cfg["model"] = mc.get("model") or cfg["model"]
        except Exception:
            pass
    return cfg


def run(cmd, timeout=900):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        print("  !", (r.stdout + r.stderr).strip()[-300:])
    return (r.stdout or "") + (r.stderr or "")


def svg_to_png(svg: Path, png: Path) -> None:
    m = re.search(r'width="(\d+)" height="(\d+)"', svg.read_text(encoding="utf-8"))
    W, H = int(m.group(1)), int(m.group(2))
    url = "file:///" + svg.as_posix()
    for _ in range(3):
        tmpd = rf"C:\Users\Administrator\AppData\Local\Temp\chrome-harness-{uuid.uuid4().hex[:8]}"
        run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--force-device-scale-factor=1.6", f"--user-data-dir={tmpd}",
             f"--window-size={W},{H}", f"--screenshot={png}", url])
        if png.exists() and png.stat().st_size > 1000:
            return
    print(f"  ! 截图失败(3次): {png.name}")


def process_one(src: Path, out: Path, cfg: dict, no_png: bool) -> str:
    """单份字幕完整闭环。返回 (类型, 状态) 摘要。"""
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w\u4e00-\u9fff]", "_", src.stem)[:40]

    # 1. 树生成（同模型）
    run([PY, WORK / "notes_structurer.py", str(src), "-o", str(out)])
    ojs = sorted(out.glob("*.outline.json"))
    if not ojs:
        return "树生成失败"
    full_ir = ojs[0]
    typ = json.loads(full_ir.read_text(encoding="utf-8")).get("type", "?")

    # 2. 确定性清洗
    run([PY, WORK / "eval" / "subtitle_repair.py", str(src), "-o", str(out / "repaired")])

    # 3. 同模型校验（harness 自主模式：不依赖 se-esee profile）
    run([PY, WORK / "eval" / "step5_seesee.py", str(out), "--name", name, "--direct"])

    # 4. 回流修订
    draw_ir = full_ir
    run([PY, WORK / "eval" / "apply_verdict.py", str(out)])
    revs = sorted(out.glob("*_revised.json"))
    if revs:
        draw_ir = revs[0]

    # 5. 画图（仅逻辑型有树；非逻辑型跳过 PNG）
    if no_png:
        run([PY, WORK / "outline_to_svg.py", str(draw_ir), "-o", str(out / "tree.svg")])
    elif typ == "逻辑型":
        run([PY, WORK / "outline_to_svg.py", str(draw_ir), "-o", str(out / "tree.svg")])
        svg_to_png(out / "tree.svg", out / "tree.png")
    else:
        print(f"  （类型 {typ}：非逻辑型，跳过导图 PNG）")

    v = ""
    vf = out / "verdict.json"
    if vf.exists():
        try:
            vv = json.loads(vf.read_text(encoding="utf-8"))
            vd = vv.get("verdicts", {})
            v = f"| fix {len(vd.get('must_fix') or [])} add {len(vd.get('should_add') or [])}"
        except Exception:
            v = "| verdict 解析失败"
    return f"{typ}{v}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", default=None, help="字幕文件或目录（默认 inbox/）")
    ap.add_argument("--no-png", action="store_true", help="无 Chrome 环境：只出 SVG")
    args = ap.parse_args()

    cfg = load_cfg()
    print(f"配置: {cfg['model']} @ {cfg['base_url']}")
    print(f"字幕目录: {cfg['subtitle_dir']} | Obsidian: {cfg['obsidian_root']}")

    if args.input:
        inp = Path(args.input)
        if inp.is_dir():
            subs = sorted(inp.glob("*.txt"))
        else:
            subs = [inp]
    else:
        inbox = Path(cfg["subtitle_dir"])
        inbox.mkdir(parents=True, exist_ok=True)
        subs = sorted(inbox.glob("*.txt"))
        if not subs:
            print(f"inbox 无字幕: {inbox}（放入 .txt 后重跑）")
            return

    date_str = datetime.now().strftime("%Y-%m-%d")
    obs_dir = Path(cfg["obsidian_root"]) / date_str
    obs_dir.mkdir(parents=True, exist_ok=True)
    print(f"归档: {obs_dir}\n")

    for i, src in enumerate(subs, 1):
        out = WORK / "harness_out" / src.stem[:40]
        print(f"[{i}/{len(subs)}] {src.name}", flush=True)
        try:
            status = process_one(src, out, cfg, args.no_png)
            # 归档 Obsidian
            shutil.copy2(sorted(out.glob("*.outline.json"))[0], obs_dir / f"{src.stem[:40]}.json")
            for md in out.glob("*.outline.md"):
                shutil.copy2(md, obs_dir / f"{src.stem[:40]}.md")
            if (out / "tree.png").exists():
                shutil.copy2(out / "tree.png", obs_dir / f"{src.stem[:40]}_导图.png")
            if (out / "verdict.json").exists():
                shutil.copy2(out / "verdict.json", obs_dir / f"{src.stem[:40]}_verdict.json")
            if (out / "step5_seesee_result.md").exists():
                shutil.copy2(out / "step5_seesee_result.md", obs_dir / f"{src.stem[:40]}_复核.md")
            print(f"  → {status} | 归档 {obs_dir}", flush=True)
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:200]}", flush=True)

    print(f"\n完成: {len(subs)} 份 → {obs_dir}")


if __name__ == "__main__":
    main()

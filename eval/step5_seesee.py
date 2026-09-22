#!/usr/bin/env python3
"""step5：把树 + 确定性修复字幕交给 seesee（外部字幕总结 agent）做仲裁。

仲裁输出结构化 JSON（四分类 verdicts），供 apply_verdict.py 回流修订树。
同时从 JSON 渲染一份 md 报告。

用法:
  python eval/step5_seesee.py <out_dir> [--name 标题] [-o 输出md]
说明:
  - out_dir 需含 *.outline.json 与 repaired.txt
  - 调用 hermes -p se-esee chat -q（模型 kimi-k2.8-preview）
  - 产物：<out_dir>/verdict.json + <out_dir>/step5_seesee_result.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import requests


def strip_fences(text: str) -> str:
    """去掉 LLM 输出可能带的 markdown 代码块围栏。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def render_md(verdict: dict, title: str) -> str:
    lines = [f"# seesee（kimi-k2.8-preview）仲裁结果 — {title}", ""]
    lines.append("## 总结")
    lines.append(verdict.get("summary", "").strip())
    lines.append("")
    for name, label in [("must_fix", "必须修订"), ("should_add", "建议补充"),
                        ("verify_later", "存疑待核"), ("ok_as_is", "合理无需动")]:
        items = verdict.get("verdicts", {}).get(name, []) or []
        if not items:
            continue
        lines.append(f"## {label}（{len(items)}）")
        for it in items:
            p = it.get("path", "?")
            if name == "must_fix":
                lines.append(f"- `{p}`：**{it.get('current','')}** → **{it.get('fixed','')}**（{it.get('reason','')}）")
            elif name == "should_add":
                lines.append(f"- `{p}`：+{it.get('text','')}（{it.get('reason','')}）")
            elif name == "verify_later":
                lines.append(f"- `{p}`：{it.get('note','')}")
            else:
                lines.append(f"- `{p}`：{it.get('note','')}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--name", default="")
    ap.add_argument("--direct", action="store_true",
                    help="同模型直调（requests，不依赖 se-esee profile）——harness 自主运行模式")
    ap.add_argument("--model", default=None,
                    help="校验模型（默认读配置：harness.env 的 LLM_MODEL 或本机 manager config）")
    args = ap.parse_args()

    out = Path(args.out_dir)
    ojs = sorted(out.glob("*.outline.json"))
    if not ojs:
        sys.exit(f"无 outline.json: {out}")
    ir = ojs[0].read_text(encoding="utf-8")
    rep = (out / "repaired.txt")
    if not rep.exists():
        sys.exit(f"无 repaired.txt: {rep}")
    rep_txt = rep.read_text(encoding="utf-8")

    title = args.name or out.name
    prompt = f"""你是字幕总结与校验 agent（seesee）。下面是同一期 B站视频《{title}》的两份材料：

【A. 修复后的字幕】（已确定性修复）
{rep_txt}

【B. 结构化笔记树】（LLM 从原字幕生成的树形 IR，统一为逻辑型：section + points level 1/2/3）
{ir}

任务：对照字幕 A 校验树 B，并输出**一份合法 JSON**（不要任何解释文字，不要 markdown 代码块）：
{{
  "summary": "基于 A 和 B 的精炼字幕总结，3-5 段，适合学习回顾",
  "verdicts": {{
    "must_fix": [{{"path": "节点定位，如 outline[2].points[3].text / concepts[1].definition / key_chain[0]", "current": "树中现有文本", "fixed": "建议修订后的文本", "reason": "依据（引用字幕原文）"}}],
    "should_add": [{{"path": "插入位置，如 outline[2] 或 outline[2].points", "level": 1, "text": "建议补充的节点文本", "reason": "依据"}}],
    "verify_later": [{{"path": "节点定位", "note": "存疑说明（如 ASR 转写本身含混，无法确认）"}}],
    "ok_as_is": [{{"path": "节点定位", "note": "为什么合理（如 ASR 修复恰当/省略恰当）"}}]
  }}
}}

判定规则：
- must_fix：树中**明确错误**（与字幕事实矛盾、或与真实世界知识矛盾如 UTF-8 编码值、数字核算错误）
- should_add：字幕中有明确信息、树完全没提且重要（论述级洞察优先）
- verify_later：字幕本身 ASR 含混、无法确认真伪的（专名/数字），标注即可，**不要**放进 must_fix
- ok_as_is：树的合理修复/合理省略（ASR 拼写统一、合理推断、教学提示未收等）
- 只输出 JSON。"""

    prompt_file = out / "step5_prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    print(f"prompt: {prompt_file} ({len(prompt)} 字)")

    verdict = None
    last_err = None
    if args.direct:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import notes_structurer as ns
        cfg = ns.load_cfg(ns.CONFIG)
        base_url = cfg.get("base_url") or ""
        api_key = cfg.get("api_key") or ""
        model = args.model or cfg.get("model") or "deepseek-v4-flash"
        # 环境变量 / harness.env 优先（harness 自主运行模式）
        from pathlib import Path as _P
        henv = _P(__file__).resolve().parent.parent / "harness.env"
        if henv.exists():
            for line in henv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k == "LLM_BASE_URL": base_url = v.strip()
                    elif k == "LLM_API_KEY": api_key = v.strip()
                    elif k == "LLM_MODEL": model = v.strip()
        print(f"校验: {model} @ {base_url}（同模型直调，升温重试 3 次）")
        for attempt in range(3):
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是字幕总结与校验 agent，只输出合法 JSON，不要解释文字。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0 + 0.15 * attempt,
            }
            try:
                r = requests.post(base_url.rstrip("/") + "/chat/completions",
                                  headers={"Authorization": f"Bearer {api_key}",
                                           "Content-Type": "application/json"},
                                  json=body, timeout=1800)
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                text = (r.json()["choices"][0]["message"]["content"] or "").strip()
                verdict = json.loads(strip_fences(text))
                break
            except Exception as e:
                last_err = e
                print(f"  仲裁尝试 {attempt + 1} 失败: {type(e).__name__}: {str(e)[:150]}", flush=True)
                time.sleep(2)
    else:
        print(f"→ hermes -p se-esee (kimi-k2.8-preview)")
        r = subprocess.run(["hermes", "-p", "se-esee", "chat", "-q", prompt, "-Q"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        text = (r.stdout or "").strip()
        try:
            verdict = json.loads(strip_fences(text))
        except Exception as e:
            last_err = e

    if verdict is None:
        # 3 次失败：不写 verdict.json（driver 据此判 seesee_ok=False），原始输出落盘供人工
        vf = out / "verdict.json"
        if vf.exists():
            vf.unlink()
        raw = out / "verdict_failed.raw.txt"
        raw.write_text(text if locals().get("text") else "", encoding="utf-8")
        print(f"! 仲裁失败（重试后仍无合法 JSON）: {type(last_err).__name__ if last_err else '?'}: "
              f"{str(last_err)[:150] if last_err else '空输出'} → {raw.name} 供人工检查", flush=True)
        sys.exit(1)

    verdict_path = out / "verdict.json"
    verdict_path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verdict.json 落盘")

    md = render_md(verdict, title)
    dst = out / "step5_seesee_result.md"
    dst.write_text(md, encoding="utf-8")
    print(f"md 报告: {dst}")

    v = verdict.get("verdicts", {})
    for k in ("must_fix", "should_add", "verify_later", "ok_as_is"):
        print(f"  {k}: {len(v.get(k) or [])}")
    for it in (v.get("must_fix") or [])[:8]:
        print(f"    FIX {it.get('path')}: {it.get('current','')[:30]} → {it.get('fixed','')[:30]}")
    for it in (v.get("should_add") or [])[:5]:
        print(f"    ADD {it.get('path')}: {it.get('text','')[:40]}")


if __name__ == "__main__":
    main()

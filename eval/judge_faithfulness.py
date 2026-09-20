#!/usr/bin/env python3
"""⚠️ 已弃用（2026-09-19）：LLM 评估移出脚本，交外部 AI 仲裁。

用户决策：只有确定性脚本执行，LLM 全部外部化。
替代：subtitle_repair.py（确定性修复+外部 prompt）+ eval_extractor.py（疑点+外部 prompt）。
本文件保留作历史参考，不再被 notes_pipeline.py 调用。

原用途：LLM-as-judge 判断主张是否被原文支持（真幻觉率）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notes_structurer as ns

JUDGE_TMPL = """你是事实核查员。判断下面的"笔记主张"是否被"原文字幕"支持。

【原文字幕】
{src}

【笔记主张】
{claim}

【判断规则】
- 支持：主张的信息在原文中有直接或可合理推断的依据
- 部分支持：主张核心有依据，但含原文没有的补充/推断/细节
- 不支持：原文未提及，或与原文矛盾（可能是脑补或错误）
专名已规范化（如 DeepSeek Harness 在原文可能是乱码音译），不算矛盾。

只输出 JSON：{{"verdict": "支持|部分支持|不支持", "reason": "一句话理由"}}"""


def judge(claim: str, src_text: str, cfg: dict, model: str, temp: float = 0.0) -> dict:
    prompt = JUDGE_TMPL.format(src=src_text, claim=claim)  # 参照源全量，截断会导致后半段误报"不支持"
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    last = None
    for attempt in range(3):
        try:
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是事实核查员，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": temp + 0.15 * attempt,
            }
            r = requests.post(url, headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            }, json=body, timeout=300)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            content = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.S)  # 容错：提取首个 JSON 对象
            if not m:
                raise RuntimeError("无 JSON 对象")
            return json.loads(m.group(0))
        except Exception as e:
            last = e
            time.sleep(1)
    raise RuntimeError(str(last)[:150])


def collect_claims(d: dict) -> list[tuple[str, str]]:
    """收集全部主张 (text, where)。排除显式 [推断] 与组织性标签。"""
    claims = []
    for i, sec in enumerate(d.get("outline", [])):
        for j, p in enumerate(sec.get("points", []) or []):
            t = p.get("text", "")
            if len(t) >= 8 and "推断" not in t:
                claims.append((t, f"outline[{i}].points[{j}]"))
        h = sec.get("heading", "")
        if len(h) >= 8:
            claims.append((h, f"outline[{i}].heading"))
    for i, c in enumerate(d.get("concepts", []) or []):
        for f in ("name", "definition"):
            t = c.get(f, "")
            if len(t) >= 8:
                claims.append((t, f"concepts[{i}].{f}"))
    for i, k in enumerate(d.get("key_chain", []) or []):
        if len(k) >= 8:
            claims.append((k, f"key_chain[{i}]"))
    for i, s in enumerate(d.get("steps", []) or []):
        t = s.get("step", "")
        if len(t) >= 8:
            claims.append((t, f"steps[{i}].step"))
    for i, it in enumerate(d.get("items", []) or []):  # 叙事型 list
        for f in ("name", "desc"):
            t = it.get(f, "")
            if len(t) >= 8:
                claims.append((t, f"items[{i}].{f}"))
        for j, p in enumerate(it.get("points", []) or []):
            if len(p) >= 8:
                claims.append((p, f"items[{i}].points[{j}]"))
    for i, tl in enumerate(d.get("timeline", []) or []):  # 叙事型 timeline
        t = tl.get("event", "")
        if len(t) >= 8:
            claims.append((t, f"timeline[{i}].event"))
    return claims


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outline_json")
    ap.add_argument("src_txt")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--max-claims", type=int, default=0, help="最多判定 N 条（0=全部）")
    args = ap.parse_args()

    d = json.loads(Path(args.outline_json).read_text(encoding="utf-8"))
    src = Path(args.src_txt).read_text(encoding="utf-8")
    cfg = ns.load_cfg(ns.CONFIG)
    model = cfg.get("model") or "deepseek-v4-flash"

    claims = collect_claims(d)
    if args.max_claims:
        claims = claims[: args.max_claims]
    print(f"判定 {len(claims)} 条主张 | 模型 {model}")

    results = []
    for i, (text, where) in enumerate(claims, 1):
        try:
            v = judge(text, src, cfg, model)
            v["claim"] = text
            v["where"] = where
            results.append(v)
            print(f"[{i}/{len(claims)}] {v.get('verdict','?'):4} {text[:36]}")
        except Exception as e:
            results.append({"claim": text, "where": where, "verdict": "error", "reason": str(e)[:100]})
            print(f"[{i}/{len(claims)}] ERROR {text[:30]}")
        time.sleep(0.3)

    n = len(results)
    cnt = {}
    for v in results:
        cnt[v.get("verdict", "?")] = cnt.get(v.get("verdict", "?"), 0) + 1
    print(f"\n判定结果: {cnt}")
    if n:
        real_halluc = cnt.get("不支持", 0) / n
        print(f"真幻觉率(不支持占比): {real_halluc:.1%}")
        print(f"支持+部分支持(可接受): {(cnt.get('支持',0)+cnt.get('部分支持',0))/n:.1%}")

    rep = {"stats": cnt, "n": n,
           "hallucination_rate": round(cnt.get("不支持", 0) / n, 3) if n else None,
           "per_claim": results}
    out = Path(args.out) if args.out else Path(args.outline_json).with_name("judge_report.json")
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")


if __name__ == "__main__":
    main()

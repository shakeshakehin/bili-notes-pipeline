#!/usr/bin/env python3
"""幻觉审计（lexical overlap 层）：检查输出主张能否在原文找到依据。

分层审计的第一层（自动、零成本）：
  主张(树节点/concepts/key_chain) 的字符 n-gram 与字幕全文的 n-gram 求交，
  重叠比例 = 可追溯分数。分数低 = 可能是推断/脑补，需要人工或 LLM-as-judge 复核。

用法:
  python eval/audit_faithfulness.py <outline.json> <src.txt>
输出:
  <outline.json 同目录>/faithfulness_report.json + 控制台汇总

约定:
  - 模型显式标注 [推断] 的主张直接标记 explicit_inference（放行，不算幻觉）
  - verdict: 可追溯(≥0.5) / 部分推断(0.25~0.5) / 未发现依据(<0.25)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NGRAM = 4          # 字符 n-gram 窗口
TH_HIGH = 0.5      # 可追溯阈值
TH_LOW = 0.25      # 部分推断阈值


def norm(s: str) -> str:
    """去空白/标点，只留中英文数字。"""
    return re.sub(r"[^\w\u4e00-\u9fff]", "", s)


def ngrams(s: str, n: int) -> set:
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def build_index(full_text: str) -> set:
    return ngrams(norm(full_text), NGRAM)


def audit_claim(claim: str, index: set) -> dict:
    """单条主张的审计。返回 {text, score, verdict, matched}"""
    c = norm(claim)
    if len(c) < 6:
        return {"text": claim, "score": None, "verdict": "太短不评", "matched": ""}
    if len(c) < 12:
        # 短语型文本（模型自拟的节点标题/概念名）不是内容主张，不参与 faithful 评分
        return {"text": claim, "score": None, "verdict": "组织性标签", "matched": ""}
    grams = ngrams(c, NGRAM)
    if not grams:
        return {"text": claim, "score": None, "verdict": "太短不评", "matched": ""}
    hit = grams & index
    score = len(hit) / len(grams)
    if score >= TH_HIGH:
        verdict = "可追溯"
    elif score >= TH_LOW:
        verdict = "部分推断"
    else:
        verdict = "未发现依据"
    matched = "…".join(sorted(hit))[:60] if hit else ""
    return {"text": claim, "score": round(score, 2), "verdict": verdict, "matched": matched}


def audit(d: dict, src_text: str) -> dict:
    index = build_index(src_text)
    items: list[dict] = []

    def add(claim: str, where: str):
        if not claim:
            return
        explicit = "推断" in claim or "[推断]" in claim
        r = audit_claim(claim, index)
        r["where"] = where
        r["explicit_inference"] = explicit
        if explicit:
            r["verdict"] = "显式推断(放行)"
        items.append(r)

    for i, sec in enumerate(d.get("outline", [])):
        add(sec.get("heading", ""), f"outline[{i}].heading")
        for j, p in enumerate(sec.get("points", []) or []):
            add(p.get("text", ""), f"outline[{i}].points[{j}].text")
    for i, c in enumerate(d.get("concepts", []) or []):
        add(c.get("name", ""), f"concepts[{i}].name")
        add(c.get("definition", ""), f"concepts[{i}].definition")
        for pr in c.get("prereq", []) or []:
            add(pr, f"concepts[{i}].prereq")
    for i, k in enumerate(d.get("key_chain", []) or []):
        add(k, f"key_chain[{i}]")
    for i, s in enumerate(d.get("steps", []) or []):
        add(s.get("step", ""), f"steps[{i}].step")
        add(s.get("detail", ""), f"steps[{i}].detail")
    for i, tl in enumerate(d.get("timeline", []) or []):
        add(tl.get("event", ""), f"timeline[{i}].event")

    scored = [x for x in items if x["score"] is not None]
    n = len(scored)
    ok = sum(1 for x in scored if x["verdict"] == "可追溯")
    partial = sum(1 for x in scored if x["verdict"] == "部分推断")
    flagged = [x for x in scored if x["verdict"] == "未发现依据"]
    inferred = sum(1 for x in items if x["explicit_inference"])

    return {
        "stats": {
            "claims": len(items), "scored": n,
            "traceable": ok, "partial": partial, "flagged": len(flagged),
            "traceable_rate": round(ok / n, 3) if n else None,
            "explicit_inference": inferred,
        },
        "flagged_claims": flagged,
        "per_claim": items,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outline_json")
    ap.add_argument("src_txt")
    args = ap.parse_args()

    d = json.loads(Path(args.outline_json).read_text(encoding="utf-8"))
    src = Path(args.src_txt).read_text(encoding="utf-8")
    rep = audit(d, src)

    out = Path(args.outline_json).with_name("faithfulness_report.json")
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    s = rep["stats"]
    print(f"主张 {s['claims']} 条（可评分 {s['scored']}）| 可追溯 {s['traceable']} · "
          f"部分推断 {s['partial']} · 未发现依据 {s['flagged']} · 显式推断 {s['explicit_inference']}")
    print(f"可追溯率: {s['traceable_rate']:.1%}" if s["traceable_rate"] is not None else "可追溯率: N/A")
    if rep["flagged_claims"]:
        print(f"\n⚠️ 未发现依据（{len(rep['flagged_claims'])} 条，需人工/LLM复核）:")
        for f in rep["flagged_claims"][:10]:
            print(f"  [{f['where']}] {f['text'][:50]}  (score {f['score']})")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()

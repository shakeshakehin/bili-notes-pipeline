#!/usr/bin/env python3
"""生成 judge 人工复核表 v3：浓缩主张 ↔ 完整版对应节点（浓缩的真正输入）↔ 原文句。

对齐策略：
- 浓缩版主张按结构对齐完整版：outline[i] 的 points 按 level1 顺序对齐（浓缩保留结构）
- level2 用 4-gram 相似度对齐
- 完整版节点（改写度低）再匹配原文句
"""
import json
import random
import re

random.seed(42)

SRC2SUB = {
    "CS336": "eval/tmp_batch/BV1Bwe76eEgo/P01_ai奶绿奶姐带你学习cs336lessi.txt",
    "CS336浓缩": "eval/tmp_batch/BV1Bwe76eEgo/P01_ai奶绿奶姐带你学习cs336lessi.txt",
    "科技补全": "eval/tmp_batch/BV1Ch8m6HEdr/clean.txt",
    "Github热榜": "eval/tmp_batch/BV1Uetz68ENB/clean.txt",
    "热点130": "eval/tmp_batch/BV1m4bn6jErE/clean.txt",
}
REPORTS = [
    ("CS336", "eval/demo_out/BV1Bwe76eEgo/judge_report.json"),
    ("CS336浓缩", "eval/demo_out/BV1Bwe76eEgo/judge_condensed.json"),
    ("科技补全", "eval/demo_out2/BV1Ch8m6HEdr/judge_report.json"),
    ("Github热榜", "eval/demo_out2/BV1Uetz68ENB/judge_report.json"),
    ("热点130", "eval/demo_out2/BV1m4bn6jErE/judge_report.json"),
]
FULL_IR = "eval/demo_out/BV1Bwe76eEgo/P01_ai奶绿奶姐带你学习cs336lessi.outline.json"
COND_IR = "eval/demo_out/BV1Bwe76eEgo/judge_condensed.json"

subs = {k: open(v, encoding="utf-8").read() for k, v in SRC2SUB.items()}
full = json.load(open(FULL_IR, encoding="utf-8"))


def norm(s):
    return re.sub(r"[\s，。；：！？、,.!?;:'\"“”‘’()（）\-—…]", "", s)


def ngrams(s, n):
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()


def sim4(a, b):
    x, y = ngrams(norm(a), 4), ngrams(norm(b), 4)
    return len(x & y) / max(1, len(x))


def find_origin(text, src_text):
    sents = [s.strip() for s in re.split(r"[。！？\n]", src_text) if len(s.strip()) >= 8]
    for ng in (6, 4, 3):
        png = ngrams(norm(text), ng)
        if not png:
            continue
        best, best_score = None, 0
        for s in sents:
            ov = len(png & ngrams(norm(s), ng)) / len(png)
            if ov > best_score:
                best, best_score = s, ov
        if best and best_score >= 0.25:
            return best
    return None


# 浓缩版 ↔ 完整版结构对齐（level1 按 section+顺序）
cond_full_map = {}
cond_rep = json.load(open(COND_IR, encoding="utf-8"))
cond = json.loads(cond_rep["per_claim"][0]["claim"] if False else open(
    "eval/demo_out/BV1Bwe76eEgo/cond_t0.5.json", encoding="utf-8").read())
# cond 用 judge 报告里的主张定位：先按 section 数近似
full_secs = full.get("outline", [])
cond_secs = cond.get("outline", [])


def map_cond_to_full(claim):
    """浓缩主张 → 完整版最相似节点（按 section 顺序 + level1 索引 + 相似度兜底）"""
    best, best_score = None, 0
    for s in full_secs:
        for p in s.get("points", []):
            sc = sim4(claim, p.get("text", ""))
            if sc > best_score:
                best, best_score = p.get("text", ""), sc
        for k in ("heading",):
            sc = sim4(claim, s.get(k, ""))
            if sc > best_score:
                best, best_score = s.get(k, ""), sc
    return best if best_score >= 0.25 else None


rows = []
for src, f in REPORTS:
    rep = json.load(open(f, encoding="utf-8"))
    for c in rep["per_claim"]:
        rows.append({"src": src, "claim": c["claim"], "verdict": c.get("verdict", "?")})

edge = [r for r in rows if r["verdict"] in ("部分支持", "error", "不支持")]
normal = [r for r in rows if r["verdict"] == "支持"]
random.shuffle(normal)
sample = edge + normal[:30 - len(edge)]
random.shuffle(sample)

lines = ["# judge 人工复核表 v3（30 条 · 浓缩↔完整版↔原文）",
         "",
         "> 每条：【浓缩主张】 ←【完整版对应节点】（浓缩的真实输入）→【原文句】（若能匹配到）。",
         "> 判断：浓缩是否忠于完整版节点（没编、没漏关键）→ ✅ / ❌（可加原因）。填完告诉我。",
         "",
         "| # | 来源 | 浓缩主张 | 完整版对应（浓缩输入） | 原文句 | 你同意？ |",
         "|---|------|----------|------------------------|--------|---------|"]
no_full = no_origin = 0
for i, r in enumerate(sample, 1):
    full_node = ""
    if r["src"] == "CS336浓缩":
        full_node = map_cond_to_full(r["claim"]) or "⚠️未对齐完整版"
        if full_node.startswith("⚠️"):
            no_full += 1
        probe = None if full_node.startswith("⚠️") else full_node
    else:
        probe = r["claim"]
    origin = find_origin(probe or r["claim"], subs[r["src"]])
    if not origin:
        no_origin += 1
        origin = "—"
    claim = r["claim"].replace("|", "\\|")[:50]
    full_node = full_node.replace("|", "\\|")[:50]
    origin = origin.replace("|", "\\|")[:60]
    lines.append(f"| {i} | {r['src']} | {claim} | {full_node} | {origin} | |")

out = r"C:\Users\Administrator\Documents\Obsidian Vault\07-AI学习\测试-B站笔记输出\judge_人工复核表.md"
open(out, "w", encoding="utf-8").write("\n".join(lines))
print(f"写出 {out} | 未对齐完整版 {no_full} | 未命中原文 {no_origin}/30")

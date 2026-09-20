#!/usr/bin/env python3
"""subtitle_repair.py — 确定性字幕修复器（零 LLM，v1 独立版）

从 clean_subtitle.py（LLM 清洗）拆出：LLM 清洗的三大问题——
 1. 过度删除（把论证链当口语删，如 ResNet 560-611 行 85.2%/10.8% 数据被删）
 2. 数字修复靠猜（16.59% 被猜成 10.59%）
 3. 无法区分"知识修复"与"瞎猜"

本脚本只做确定性操作：
 阶段1 替换表修复    同音/形近/乱码专名（知识可靠项，正确率高）
 阶段2 数字断句修复  发音角度规则（中文数字沿十位/个位边界裂开可检测）
 阶段3 不删行        只替换/拼接，不删除任何内容行
 阶段4 输出          repaired.txt + external_prompt.txt（可直接扔给外部 AI）

用法:
  python eval/subtitle_repair.py <subtitle.txt> [-o 输出前缀]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---- 阶段1：替换表（错误 → 正确, 类型）。只放"知识可靠"项：同音/形近/乱码 ----
# 语境敏感项不用无差别替换（如 12月→12页 只在"论文页数"语境成立，"发表于…12月"不能改）
WORD_REPLACE = [
    # 同音/形近字（无差别安全）
    ("孙健", "孙剑", "同音"), ("点击", "点积", "形近语义"),
    ("较前", "较浅", "同音"), ("点成", "点乘", "形近语义"),
    # 乱码/大小写专名
    ("RESONNET", "ResNet", "乱码专名"), ("resonnet", "ResNet", "乱码专名"),
    ("alex net", "AlexNet", "专名"), ("Alex net", "AlexNet", "专名"),
    ("image net", "ImageNet", "专名"), ("imagenet", "ImageNet", "专名"),
    ("coco", "COCO", "专名"), ("divon2", "DINOv2", "乱码专名"), ("DIVON2", "DINOv2", "乱码专名"),
    ("ZIV初始化", "Xavier初始化", "专名"), ("HE初始化", "He初始化", "专名"),
    ("deep sick", "DeepSeek", "专名"), ("深度求索", "DeepSeek", "专名"),
    ("卷积核之间的点击", "卷积核之间的点积", "形近语义"),
]
# 语境敏感修正（带约束的子串模式）
CONTEXT_REPLACE = [
    (re.compile(r"只有12月"), "只有12页", "同音·语境"),     # 论文页数语境
    (re.compile(r"这篇只有12页"), "这篇只有12页", "同音·语境"),
]

# ---- 阶段2：数字断句修复（发音角度） ----
# 中文读 "16.59%" = "十六点五九" = "十"(10) + "六点五九"(6.59)。
# ASR 断句错误时数字沿十位/个位边界裂开：上行尾 10% + 下行 6.59 → 16.59%
NUM_TENS_TAIL = re.compile(r"^(.*?)([1-9])0(%?)$")      # 行尾 10/20..90 可选 %
NUM_UNIT_LINE = re.compile(r"^([1-9])\.(\d{1,4})%?$")    # 整行 个位.小数（孤立数值行）


def repair(text: str) -> tuple[str, list[dict], list[dict]]:
    """返回 (修复后文本, 修正记录, 疑点清单)"""
    lines = text.replace("\r\n", "\n").split("\n")
    fixes: list[dict] = []
    doubts: list[dict] = []

    # ---- 阶段1：替换表 ----
    for i, ln in enumerate(lines):
        for wrong, right, kind in WORD_REPLACE:
            if wrong in ln:
                lines[i] = ln.replace(wrong, right)
                fixes.append({"行": i + 1, "类型": kind, "原": ln.strip(),
                              "修复": lines[i].strip()})
                ln = lines[i]
        for pat, right, kind in CONTEXT_REPLACE:
            if pat.search(ln):
                lines[i] = pat.sub(right, ln)
                fixes.append({"行": i + 1, "类型": kind, "原": ln.strip(),
                              "修复": lines[i].strip()})
                ln = lines[i]

    # 疑点：年份断裂（"2000 015年" = "2015年" 说岔）——规则不猜，交外部 AI
    for i, ln in enumerate(lines):
        if re.search(r"\d{4}\s+\d{2,3}", ln):
            doubts.append({"行": i + 1, "内容": ln.strip(),
                           "原因": "年份/数字断裂（如 2000 015 → 2015？），需发音/知识判断"})

    # ---- 阶段2：数字断句修复（发音拼接） ----
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = NUM_TENS_TAIL.match(lines[i])
        if m and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            n = NUM_UNIT_LINE.match(nxt)
            if n:  # 十位整数 + 个位小数行 → 发音拼接
                tens, x, yy = m.group(2), n.group(1), n.group(2)
                pct = m.group(3)
                merged = f"{tens}{x}.{yy}{pct}"
                fixes.append({"行": f"{i + 1}+{i + 2}", "类型": "数字断句[推断]",
                              "原": f"{lines[i].strip()} | {nxt}", "修复": merged})
                out.append(m.group(1) + merged)  # 前文 + 拼接数字
                i += 2  # 跳过下一行（已并入）
                continue
        # 孤立数值行但无可拼上下文 → 疑点
        if NUM_UNIT_LINE.match(lines[i].strip()) or \
           re.fullmatch(r"\d+(\.\d+)?%?", lines[i].strip()):
            doubts.append({"行": i + 1, "内容": lines[i].strip(),
                           "原因": "孤立数值行，无法确定性拼接"})
        # 跨行乘号断裂（"64×110" + "12×112" → "64×112×112"？）→ 疑点，不猜
        if i + 1 < len(lines) and re.search(r"×\d+$", lines[i]) and \
           re.match(r"^\d+×", lines[i + 1]):
            doubts.append({"行": f"{i + 1}+{i + 2}",
                           "内容": f"{lines[i].strip()} | {lines[i + 1].strip()}",
                           "原因": "乘号维度跨行断裂，需领域知识重组（如 110，12→112）"})
        out.append(lines[i])
        i += 1

    return "\n".join(out), fixes, doubts


def build_external_prompt(orig: str, repaired: str, fixes: list[dict],
                          doubts: list[dict]) -> str:
    head = "你是字幕修复仲裁员。下面是一段 B站视频字幕，已由**确定性脚本**修复（替换表+发音规则），"
    head += "但脚本只做可推导的修复，需要你审核以下内容并给出最终裁决：\n\n"
    p = [head, f"【1. 修复清单】共 {len(fixes)} 项：", ""]
    for f in fixes:
        p.append(f"- 行{f['行']} [{f['类型']}]")
        p.append(f"  原: {f['原']}")
        p.append(f"  修: {f['修复']}")
    p += ["", f"【2. 疑点清单】共 {len(doubts)} 项（脚本无法确定，需你判断）：", ""]
    for d in doubts:
        p.append(f"- 行{d['行']}: {d['内容']}（{d['原因']}）")
    p += ["", "【3. 修复后完整文本】", "", repaired,
          "", "【你的任务】", "1. 修复清单里的 [推断] 项是否正确？不正确请给出正确值并说明依据。",
          "2. 疑点清单各项：根据上下文/发音/领域知识给出正确解读，或确认保留原文。",
          "3. 如需进一步压缩口语冗余，请在不丢失信息的前提下自行处理。",
          "4. 输出：修正后的最终字幕全文。"]
    return "\n".join(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("subtitle")
    ap.add_argument("-o", "--out", default=None, help="输出前缀，默认同输入文件名")
    args = ap.parse_args()

    src = Path(args.subtitle)
    text = src.read_text(encoding="utf-8").strip()
    print(f"修复: {src.name} | {len(text)} 字")

    repaired, fixes, doubts = repair(text)
    prefix = Path(args.out) if args.out else src.with_name(src.stem + "_repaired")

    (Path(str(prefix) + ".txt")).write_text(repaired, encoding="utf-8")
    (Path(str(prefix) + "_external_prompt.txt")).write_text(
        build_external_prompt(text, repaired, fixes, doubts), encoding="utf-8")

    n_rep = len([f for f in fixes if "推断" not in f["类型"]])
    n_inf = len([f for f in fixes if "推断" in f["类型"]])
    print(f"修复: 确定性 {n_rep} 项, 推断性 {n_inf} 项, 疑点 {len(doubts)} 项")
    print(f"写出: {prefix}.txt | {prefix}_external_prompt.txt")


if __name__ == "__main__":
    main()

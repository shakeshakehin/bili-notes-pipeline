#!/usr/bin/env python3
"""确定性疑点提取器（本地层，零 LLM 依赖）。

设计：本地先筛"实体幻觉疑点 + 锚点遗漏疑点"，生成 payload 由外部 LLM 仲裁。
LLM 调用不在此脚本内——`generate_payload_for_ai` 只产出仲裁文本。

用户提供的原始版本，尚未修复以下已知坑（见评估）：
1. 锚点 4-gram 对"改写"假阳性（同 audit_faithfulness 的失效模式）
2. 中文实体漏检（只提取英文大写专名）
3. raw[:1000] 截断（长字幕后半段误报，参照 judge 截断 bug）
"""
import re
import json

# 复用你的实体映射表
TERM_MAP = {
    "DeepSeek": ["deep sick", "深度求索"], "CNN": ["卷积神经"],
    # ... 其他映射保持不变
}

def extract_entities(text: str) -> set:
    numbers = set(re.findall(r"(?<![A-Za-z0-9.])\d{2,}(?:\.\d+)?%?(?![A-Za-z0-9])", text))
    terms = set(re.findall(r"(?<![A-Za-z0-9])[A-Z][a-zA-Z0-9\-_]{2,}(?![A-Za-z0-9])", text))
    return numbers | terms

def get_unmatched_entities(raw: str, generated: str) -> list[str]:
    src = extract_entities(raw)
    out = extract_entities(generated)
    unmatched = []
    
    for e in out:
        # 1. 严格子串匹配
        if any(e in s or s in e for s in src):
            continue
        # 2. 映射表匹配
        if any(zh in raw for zh in TERM_MAP.get(e, [])):
            continue
        unmatched.append(e)
    return unmatched

def get_unmatched_anchors(raw: str, generated: str) -> list[str]:
    stop = set("的了是在有一不就我你他这也那和与及为被把个呢吗吧啊")
    grams = {}
    for i in range(len(raw) - 3):
        g = raw[i:i + 4]
        if any(ch in stop for ch in g) or g.isdigit() or g.isascii():
            continue
        grams[g] = grams.get(g, 0) + 1
    
    # 取 Top 10 核心锚点
    anchors = [g for g, _ in sorted(grams.items(), key=lambda x: -x[1])[:10]]
    unmatched = [a for a in anchors if a not in generated]
    return unmatched

def generate_payload_for_ai(raw_text: str, generated_text: str):
    unmatched_entities = get_unmatched_entities(raw_text, generated_text)
    unmatched_anchors = get_unmatched_anchors(raw_text, generated_text)
    
    payload = f"""请你作为 LLM 语义裁判，帮我完成第二部分的评估。
以下是本地 Python 脚本提取出的"疑点数据"，请根据【原文】和【总结产物】进行仲裁：

1. **实体幻觉核验 (unmatched_entities)**: {unmatched_entities}
2. **核心锚点遗漏核验 (unmatched_anchors)**: {unmatched_anchors}

【原文片段】：
{raw_text[:1000]}... (为节省Token，此处可截取原文相关部分或全量)

【总结产物】：
{generated_text}

请分析这些未匹配的实体是否属于"合理推演/单位换算"（还是纯粹的幻觉）？未匹配的锚点是否被"同义改写"保留在了产物中（还是真的遗漏了）？并给出最终的语义修订分数。
"""
    return payload

# 测试运行
if __name__ == "__main__":
    raw = "昨天的Deep sick模型发布了，参数量达到了670亿。因为算力成本太高，所以做了一些折中权衡，最终均方误差下降了百分之十五。"
    gen = "DeepSeek模型发布，参数为67B。由于成本原因进行了权衡，MSE下降了15%。"
    
    print("=== 复制以下内容直接发给 AI ===")
    print(generate_payload_for_ai(raw, gen))

#!/usr/bin/env python3
"""导图前 LLM 浓缩：完整树形 IR → 浓缩树形 IR（压文字 + 砍层级）。

定位：md 吃完整 IR（精读），导图吃浓缩 IR（概览）——展示层与阅读层解耦。
浓缩版必须保持 section/level 结构合法（过 validate），只压缩/删减，不新增信息。

用法:
  python eval/condense_tree.py <outline.json> [-o condensed.json] [--max-chars 16] [--max-branches 6]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notes_structurer as ns

CONDENSE_TMPL = """你是教学笔记浓缩器。输入是某教学视频的结构化笔记（树形 IR），请浓缩成**保留教学性**的精简版——用于思维导图展示，但必须让读者看懂"为什么"和"怎么推演"。

【浓缩规则】
1. **教学性优先**：步骤推演、因果解释、辩证思考是教学的灵魂，必须保留，不许砍
2. **保留推演过程**：如"18字节→16→14→12"的每轮合并、"词表256→257→258→259"的编号递增——给过程，不只给结果
3. **保留因果链**：如"为什么整词会变UNK"、"为何选UTF-8+BPE"这类解释必须保留"因为…所以…"
4. **保留辩证思考**：如 FlashAttention 优化、参数量翻倍、权重共享等工程权衡
5. **保留关键条件**：如"UTF-8汉字被切碎时必须先拼完再解码"的场景说明
6. **删除**：口语废词、重复表述、纯修饰语、与主题无关的闲聊
7. **完整句子**：每个节点用完整句子（20-40字），禁止"例：""成：""→"式碎片拼接，禁止只罗列名词
8. **保留层级**：推演步骤保留在 points 里（level 2/3 有价值的不合并不删除），每节分支 ≤7 个
9. **只压缩不新增**：不得添加原文没有的信息；concepts 保留名称+完整定义；key_chain 每步 20-40 字

【输入 IR】
{ir}

【输出】浓缩后的合法 JSON："""


def condense(d: dict, cfg: dict, model: str, max_chars: int, max_branches: int,
             fixed_temp: float | None = None) -> tuple[dict, dict]:
    return _condense_with(CONDENSE_TMPL, d, cfg, model, fixed_temp)


def _condense_with(tmpl: str, d: dict, cfg: dict, model: str,
                   fixed_temp: float | None = None) -> tuple[dict, dict]:
    prompt = tmpl.format(ir=json.dumps(d, ensure_ascii=False))
    url = cfg["base_url"].rstrip("/") + "/chat/completions"

    def once(temp: float) -> tuple[dict, dict]:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是信息浓缩器，只输出合法 JSON，不输出解释文字。"},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temp,
        }
        r = requests.post(url, headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }, json=body, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        payload = r.json()
        content = payload["choices"][0]["message"]["content"]
        return json.loads(ns.strip_fences(content)), (payload.get("usage") or {})

    last = None
    for attempt in range(3):
        try:
            # 温度实测（0.1/0.3/0.5 对比）：0.1 压缩过度丢语义，0.3-0.5 保真更好
            temp = fixed_temp if fixed_temp is not None else 0.3 + 0.1 * attempt  # 0.3/0.4/0.5
            out, usage = once(temp)
            errs = ns.validate(out)
            if not errs:
                return out, usage
            last = f"validate {len(errs)} 处: {json.dumps(errs[:3], ensure_ascii=False)[:200]}"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:200]}"
        time.sleep(2)
    raise RuntimeError(f"浓缩三次失败: {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outline_json")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--max-chars", type=int, default=16)
    ap.add_argument("--max-branches", type=int, default=6)
    ap.add_argument("--temperature", type=float, default=None,
                    help="固定温度（默认 None=升温重试 0.3/0.4/0.5，实测 0.3-0.5 保真更好）")
    args = ap.parse_args()

    src = Path(args.outline_json)
    d = json.loads(src.read_text(encoding="utf-8"))
    cfg = ns.load_cfg(ns.CONFIG)
    model = cfg.get("model") or "deepseek-v4-flash"
    print(f"浓缩: {src.name} | 模型 {model} | 每节点≤{args.max_chars}字 每节≤{args.max_branches}分支 "
          f"| 温度 {args.temperature if args.temperature is not None else '升温0.2/0.35/0.5'}")

    t0 = time.time()
    out, usage = condense(d, cfg, model, args.max_chars, args.max_branches, args.temperature)
    print(f"耗时 {time.time()-t0:.1f}s | tokens 入 {usage.get('prompt_tokens')} 出 {usage.get('completion_tokens')}")

    dst = Path(args.out) if args.out else src.with_name(src.stem + "_condensed.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    def cnt(o):
        if o.get("type") != "逻辑型":
            return 0, 0, {}
        lv = {}
        secs = len(o.get("outline", []))
        for s in o.get("outline", []):
            for p in s.get("points", []):
                l = p.get("level", 1)
                lv[l] = lv.get(l, 0) + 1
        return secs, sum(lv.values()), lv

    s1, p1, l1 = cnt(d)
    s2, p2, l2 = cnt(out)
    print(f"节点数: {p1} → {p2} | level 分布 {l1} → {l2} | 每节平均 {p1/max(s1,1):.1f} → {p2/max(s2,1):.1f}")
    print(f"写出: {dst}")


if __name__ == "__main__":
    main()

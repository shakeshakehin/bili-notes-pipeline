#!/usr/bin/env python3
"""validate() 单测（树形版 IR）。M0 验收的一部分：规则改动要有回归保障。

用法: python eval/test_validate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from notes_structurer import validate

PASS = FAIL = 0


def check(name: str, errs: list, expect: int, must_contain: tuple = ()) -> None:
    global PASS, FAIL
    ok = len(errs) == expect and all(any(m in e["path"] for e in errs) for m in must_contain)
    if ok:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}: 期望 {expect} 处错 got {len(errs)}"
              + (f"，缺 {must_contain}" if must_contain else "")
              + (" -> " + str([(e["path"], e["kind"]) for e in errs][:4]) if errs else ""))


def logic(points, **extra):
    d = {"type": "逻辑型", "title": "t", "thesis": "x", "outline":
         [{"level": 1, "heading": "h", "points": points}],
         "concepts": [{"name": "c", "definition": "d", "prereq": []}], "key_chain": ["k"]}
    d.update(extra)
    return d


# 1. 合法树形 IR（1/2/3 级嵌套）
check("合法树形 IR", validate(logic([
    {"level": 1, "text": "A"},
    {"level": 2, "text": "A1"},
    {"level": 3, "text": "A1a"},
    {"level": 1, "text": "B"},
])), 0)

# 2. level 缺失
check("level 缺失", validate(logic([{"text": "no level"}])), 1, ("points[0].level",))

# 3. level 越界（0 和 4）
check("level=0 越界", validate(logic([{"level": 0, "text": "x"}])), 1, ("points[0].level",))
check("level=4 越界", validate(logic([{"level": 4, "text": "x"}])), 1, ("points[0].level",))

# 4. text 缺失 / 类型错
check("text 缺失", validate(logic([{"level": 1}])), 1, ("points[0].text",))
check("text 非字符串", validate(logic([{"level": 1, "text": 123}])), 1, ("points[0].text",))

# 5. point 不是 dict
check("point 非 dict", validate(logic(["string"])), 1, ("points[0]",))

# 6. 旧 relation IR（无 level）→ 应报 level missing
check("旧 relation IR 不合法", validate(logic([
    {"text": "a", "relation": "因果"},
    {"text": "b", "relation": None},
])), 2)  # 两条都缺 level

# 7. outline 空 / section level 越界
check("outline 空", validate({"type": "逻辑型", "title": "t", "thesis": "x",
                              "outline": [], "concepts": [], "key_chain": []}),
      1, ("outline",))
check("section level=4", validate({"type": "逻辑型", "title": "t", "thesis": "x",
                                   "outline": [{"level": 4, "heading": "h", "points": []}],
                                   "concepts": [], "key_chain": []}),
      1, ("outline[0].level",))

# 8. 非逻辑类型必须被拒绝（v5.4 全逻辑型：类型收拢为"逻辑型"单一 schema）
check("决策型被拒", validate({"type": "决策型", "title": "t", "subject": "s", "score": "8",
                              "pros": ["a"], "cons": ["b"], "specs": [{"item": "i", "value": "v"}],
                              "price": "p", "target": "t", "verdict": "v"}), 1, ("type",))
check("操作型被拒", validate({"type": "操作型", "title": "t", "prerequisites": ["p"],
                              "steps": [{"step": "1", "detail": "d", "tools": "t", "pitfall": "p"}],
                              "verify": "v"}), 1, ("type",))
check("叙事型被拒", validate({"type": "叙事型", "title": "t",
                              "timeline": [{"when": "w", "event": "e"}],
                              "people": ["p"], "impact": "i"}), 1, ("type",))

print(f"\n{'-'*40}\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

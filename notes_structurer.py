#!/usr/bin/env python3
"""notes_structurer v4: 视频字幕 → 按「视频类型」路由的结构化笔记。

先让 LLM 判断类型（逻辑型/决策型/操作型/叙事型），再按对应 schema 输出，
避免"测评视频硬套因果大纲"这类错配。参考 PDTB/RST 话语关系理论。

用法:
  python notes_structurer.py <subtitle.txt> [-o 输出目录] [--model X] [--base-url Y] [--api-key Z] [--tag T] [--dry-run]
产出:
  <name>.outline.md    按类型的 markdown 笔记（含 mermaid 代码块，Obsidian 可渲染）
  <name>.outline.json  结构化 IR（含 type 字段）
  <name>.usage.json    token 计量
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CONFIG = Path(os.path.expanduser("~")) / "AppData/Local/hermes/profiles/manager/config.yaml"
DEFAULT_OUT = Path(r"E:\AIbulid\hermesonly\notes_out")

SYSTEM = (
    "你是视频内容结构分析师：先判断视频类型，再按该类型的结构重建信息骨架。"
    "你的输出必须是合法 JSON，不要输出任何解释文字、不要用 markdown 代码围栏包裹。"
)

USER_TMPL = """下面是某期视频的字幕（口播转录，含口语冗余、错别字、重复、广告）。请先判断视频类型，再按对应结构重建骨架。

【输入字幕】
{text}

【第 1 步：判断类型】(type 只能取一个)
- 逻辑型：讲原理/概念/原因/为什么/影响/对比论证。特征词：原理、概念、为什么、原因、导致、本质上、机制
- 决策型：讲值不值得买/推荐/优缺点/价格/评分/性价比/适合人群。特征词：推荐、值不值得、优缺点、价格、性价比、适合、评分、入手、评测
- 操作型：讲怎么做/步骤/教程/工具/配置/注意。特征词：步骤、教程、怎么做、首先...然后、工具、配置、点击、新建、注意
- 叙事型：以上都不明显，讲事件/时间线/经历/盘点。特征词：那天、后来、回顾、事件、盘点、经历
判定原则：特征冲突时看字幕整体像哪种；仍不确定选叙事型。

【第 2 步：按类型输出结构】(只输出所选类型的 JSON)

■ 逻辑型：
{{\"type\":\"逻辑型\",\"title\":\"...\",\"thesis\":\"...\",\"outline\":[{{\"level\":1,\"heading\":\"...\",\"points\":[{{\"level\":1,\"text\":\"...\"}},{{\"level\":2,\"text\":\"...\"}}]}}],\"concepts\":[{{\"name\":\"...\",\"definition\":\"...\",\"prereq\":[]}}],\"key_chain\":[\"...\",\"...\"]}}
  - outline：heading 的 level 取 1/2/3；points 用 level 表达本节内的层级（树）：
      level 1 = 本节的直接要点（多个 level 1 是并列的兄弟分支）
      level 2 = 上一条要点的展开/举例/细化/执行步骤（子项）
      level 3 = 更细一层的展开（孙项）
    判断规则：这条如果是"新的一点"→ level 1；如果是"对前面某点的展开/支撑/举例/步骤"→ 比被展开的点低一层。
    特别注意：并列的两个概念（两个方法/两个架构/两类东西）必须是两个 level 1 兄弟，各自下面挂自己的展开，不要合并成一条。
    流程/步骤描述（怎么做、实现过程）用连续降层的子项表达：父项是动作名，子项按顺序展开。
  - concepts：定义一句话 + prereq 前置概念；prereq 若为推断（原文未明说）则名称后加 [推断]
  - key_chain：主干推进 3-6 步

■ 决策型：
{{"type":"决策型","title":"...","subject":"被测对象","score":"评分，如 8.5/10","pros":["优点1","..."],"cons":["缺点1","..."],"specs":[{{"item":"参数名","value":"值"}}],"price":"价格","target":"适合人群","verdict":"最终推荐结论"}}

■ 操作型：
{{"type":"操作型","title":"...","prerequisites":["前置条件1"],"steps":[{{"step":"1. 动作","detail":"说明","tools":"所需工具/材料","pitfall":"易错点或留空"}}],"common_pitfalls":["常见坑1"],"verify":"完成标志/如何确认成功"}}

■ 叙事型（两种形态二选一）：
- 讲事件/时间线/经历 → timeline 形态：
{{\"type\":\"叙事型\",\"title\":\"...\",\"format\":\"timeline\",\"timeline\":[{{\"when\":\"阶段名(≤6字)\",\"event\":\"核心动作一句话(≤25字)\",\"points\":[\"细节1\",\"细节2\"]}}],\"people\":[\"关键人物或空数组\"],\"impact\":\"影响/结论\"}}
  - timeline 的 when：简短阶段名（如 开场/冲突/转折/结局，或 2024年/第一周）
  - timeline 的 event：**只写一句话核心**（谁做了/发生了什么），细节、原话、理由放 points（2-4 条）
  - 时点数 4-8 个，不要逐句拆，合并同类回合
- 盘点/罗列多个东西（项目/商品/资料/清单）→ list 形态：
{{\"type\":\"叙事型\",\"title\":\"...\",\"format\":\"list\",\"items\":[{{\"name\":\"条目名(≤12字)\",\"desc\":\"一句话简述(≤40字)\",\"points\":[\"要点1\",\"要点2\"]}}],\"people\":[],\"impact\":\"影响/结论\"}}
  - format 选择：内容主体是事件演进/时间经历 → timeline；内容主体是"罗列多个独立条目"（开源项目、产品、资料、榜单）→ list
  - list 的 items：每个条目 name 简短、desc 一句话、points 提炼 3-6 个关键点（**不要**把全部细节塞进 desc，细节放 points）

【通用规则】
- 删除口语废话与引流内容（加微信/学习资料/报名等），只留信息
- 修正明显错别字，但不得添加字幕中没有的信息；信息不足处写 [原文未提及]
- 只输出那一个 JSON，不要任何解释文字"""


def load_cfg(path: Path) -> dict:
    if not path.exists():
        return {}
    txt = path.read_text(encoding="utf-8")

    def grab(key: str):
        m = re.search(rf"^\s*{key}:\s*(.+)$", txt, re.M)
        return m.group(1).strip().strip('"').strip("'") if m else None

    cfg = {"base_url": grab("base_url") or "", "api_key": grab("api_key") or "",
           "model": grab("default"), "provider": grab("provider") or "custom"}

    # provider 非 custom 时，端点与 key 在 .env（deepseek → DEEPSEEK_API_KEY）
    if not cfg["base_url"] or not cfg["api_key"]:
        env_path = path.with_name(".env")
        env = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

        def egrab(key: str):
            m = re.search(rf"^{key}=(.+)$", env, re.M)
            return m.group(1).strip() if m else None

        if not cfg["base_url"]:
            if cfg["provider"] == "deepseek":
                cfg["base_url"] = "https://api.deepseek.com"
        if not cfg["api_key"]:
            cfg["api_key"] = (egrab("DEEPSEEK_API_KEY") or egrab("ARK_CODING_API_KEY")
                              or egrab("GLM_API_KEY"))
    return cfg


def strip_fences(s: str) -> str:
    s = s.strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", s, re.S)
    return m.group(1) if m else s


def call_llm(text: str, cfg: dict, model: str, temperature: float) -> dict:
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TMPL.format(text=text)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
    }
    r = requests.post(url, headers={
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }, json=body, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    payload = r.json()
    content = payload["choices"][0]["message"]["content"]
    return json.loads(strip_fences(content)), (payload.get("usage") or {})


TYPE_REQ = {
    "逻辑型": ["type", "title", "thesis", "outline", "concepts", "key_chain"],
    "决策型": ["type", "title", "subject", "pros", "cons", "verdict"],
    "操作型": ["type", "title", "prerequisites", "steps", "verify"],
    "叙事型": ["type", "title", "format", "impact"],
}


RELATIONS = {"因果", "条件", "对比", "让步", "并列", "例证", "细化", "时序"}


def _check(cond: bool, errs: list, path: str, kind: str, expected, actual) -> None:
    """追加一条结构化错误：kind ∈ missing/type/enum/empty"""
    if not cond:
        errs.append({"path": path, "kind": kind, "expected": expected, "actual": actual})


def _str_or_null(x):
    return isinstance(x, str) or x is None


def validate(d: dict) -> list[dict]:
    """严格校验，返回结构化错误清单（空列表 = 合法）。

    每个错误条目:
      {"path": "outline[0].points[2].relation",
       "kind": "missing|type|enum|empty",
       "expected": "期望的取值/类型",
       "actual":   "实际值"}
    """
    errs: list[dict] = []
    t = d.get("type")
    if t not in TYPE_REQ:
        errs.append({"path": "type", "kind": "enum",
                     "expected": "/".join(TYPE_REQ), "actual": t})
        return errs  # 类型未知时其余字段无从校验

    for k in TYPE_REQ[t]:
        if k not in d:
            _check(False, errs, k, "missing", "必填字段", None)

    if t == "逻辑型":
        outline = d.get("outline")
        if not isinstance(outline, list):
            _check(False, errs, "outline", "type", "list", type(outline).__name__)
        else:
            if not outline:
                _check(False, errs, "outline", "empty", "非空数组", "[]")
            for i, sec in enumerate(outline):
                p = f"outline[{i}]"
                if not isinstance(sec, dict):
                    _check(False, errs, p, "type", "object", type(sec).__name__)
                    continue
                if "heading" not in sec:
                    _check(False, errs, f"{p}.heading", "missing", "必填字段", None)
                elif not isinstance(sec["heading"], str):
                    _check(False, errs, f"{p}.heading", "type", "string", type(sec["heading"]).__name__)
                lv = sec.get("level")
                if lv not in (1, 2, 3):
                    _check(False, errs, f"{p}.level", "enum", "1/2/3", lv)
                pts = sec.get("points")
                if not isinstance(pts, list):
                    _check(False, errs, f"{p}.points", "type", "list", type(pts).__name__)
                    continue
                for j, pt in enumerate(pts):
                    pp = f"{p}.points[{j}]"
                    if not isinstance(pt, dict):
                        _check(False, errs, pp, "type", "object", type(pt).__name__)
                        continue
                    if "text" not in pt:
                        _check(False, errs, f"{pp}.text", "missing", "必填字段", None)
                    elif not isinstance(pt["text"], str):
                        _check(False, errs, f"{pp}.text", "type", "string", type(pt["text"]).__name__)
                    if "level" not in pt:
                        _check(False, errs, f"{pp}.level", "missing", "必填字段", None)
                    elif pt["level"] not in (1, 2, 3):
                        _check(False, errs, f"{pp}.level", "enum", "1/2/3", pt["level"])
        concepts = d.get("concepts")
        if not isinstance(concepts, list):
            _check(False, errs, "concepts", "type", "list", type(concepts).__name__)
        else:
            for i, c in enumerate(concepts):
                p = f"concepts[{i}]"
                if not isinstance(c, dict):
                    _check(False, errs, p, "type", "object", type(c).__name__)
                    continue
                for f in ("name", "definition"):
                    if f not in c:
                        _check(False, errs, f"{p}.{f}", "missing", "必填字段", None)
                    elif not isinstance(c[f], str):
                        _check(False, errs, f"{p}.{f}", "type", "string", type(c[f]).__name__)
                pr = c.get("prereq")
                if not isinstance(pr, list) or not all(isinstance(x, str) for x in pr):
                    _check(False, errs, f"{p}.prereq", "type", "list of string",
                           type(pr).__name__ if pr is not None else None)
        for f in ("key_chain",):
            v = d.get(f)
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                _check(False, errs, f, "type", "list of string", type(v).__name__)

    elif t == "决策型":
        for f in ("subject", "score", "price", "target", "verdict"):
            if f not in d:
                _check(False, errs, f, "missing", "必填字段", None)
            elif not isinstance(d[f], str):
                _check(False, errs, f, "type", "string", type(d[f]).__name__)
        for f in ("pros", "cons"):
            v = d.get(f)
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                _check(False, errs, f, "type", "list of string", type(v).__name__)
            elif not v:
                _check(False, errs, f, "empty", "非空数组", "[]")
        specs = d.get("specs")
        if not isinstance(specs, list):
            _check(False, errs, "specs", "type", "list", type(specs).__name__)
        else:
            for i, s in enumerate(specs):
                p = f"specs[{i}]"
                if not isinstance(s, dict):
                    _check(False, errs, p, "type", "object", type(s).__name__)
                    continue
                for f in ("item", "value"):
                    if f not in s:
                        _check(False, errs, f"{p}.{f}", "missing", "必填字段", None)
                    elif not isinstance(s[f], str):
                        _check(False, errs, f"{p}.{f}", "type", "string", type(s[f]).__name__)

    elif t == "操作型":
        v = d.get("prerequisites")
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            _check(False, errs, "prerequisites", "type", "list of string", type(v).__name__)
        steps = d.get("steps")
        if not isinstance(steps, list):
            _check(False, errs, "steps", "type", "list", type(steps).__name__)
        else:
            if not steps:
                _check(False, errs, "steps", "empty", "非空数组", "[]")
            for i, s in enumerate(steps):
                p = f"steps[{i}]"
                if not isinstance(s, dict):
                    _check(False, errs, p, "type", "object", type(s).__name__)
                    continue
                for f in ("step", "detail", "tools", "pitfall"):
                    if f not in s:
                        _check(False, errs, f"{p}.{f}", "missing", "必填字段", None)
                    elif not isinstance(s[f], str):
                        _check(False, errs, f"{p}.{f}", "type", "string", type(s[f]).__name__)
        v = d.get("common_pitfalls")
        if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
            _check(False, errs, "common_pitfalls", "type", "list of string", type(v).__name__)
        if "verify" not in d:
            _check(False, errs, "verify", "missing", "必填字段", None)
        elif not isinstance(d["verify"], str):
            _check(False, errs, "verify", "type", "string", type(d["verify"]).__name__)

    elif t == "叙事型":
        fmt = d.get("format")
        if fmt not in ("timeline", "list"):
            _check(False, errs, "format", "enum", "timeline/list", fmt)
            return errs
        if fmt == "timeline":
            timeline = d.get("timeline")
            if not isinstance(timeline, list):
                _check(False, errs, "timeline", "type", "list", type(timeline).__name__)
            else:
                if not timeline:
                    _check(False, errs, "timeline", "empty", "非空数组", "[]")
                for i, tl in enumerate(timeline):
                    p = f"timeline[{i}]"
                    if not isinstance(tl, dict):
                        _check(False, errs, p, "type", "object", type(tl).__name__)
                        continue
                    for f in ("when", "event"):
                        if f not in tl:
                            _check(False, errs, f"{p}.{f}", "missing", "必填字段", None)
                        elif not isinstance(tl[f], str):
                            _check(False, errs, f"{p}.{f}", "type", "string", type(tl[f]).__name__)
                    pts = tl.get("points")
                    if pts is not None and (not isinstance(pts, list)
                                            or not all(isinstance(x, str) for x in pts)):
                        _check(False, errs, f"{p}.points", "type", "list of string",
                               type(pts).__name__)
        else:  # list
            items = d.get("items")
            if not isinstance(items, list):
                _check(False, errs, "items", "type", "list", type(items).__name__)
            else:
                if not items:
                    _check(False, errs, "items", "empty", "非空数组", "[]")
                for i, it in enumerate(items):
                    p = f"items[{i}]"
                    if not isinstance(it, dict):
                        _check(False, errs, p, "type", "object", type(it).__name__)
                        continue
                    for f in ("name", "desc"):
                        if f not in it:
                            _check(False, errs, f"{p}.{f}", "missing", "必填字段", None)
                        elif not isinstance(it[f], str):
                            _check(False, errs, f"{p}.{f}", "type", "string", type(it[f]).__name__)
                    pts = it.get("points")
                    if pts is not None and (not isinstance(pts, list)
                                            or not all(isinstance(x, str) for x in pts)):
                        _check(False, errs, f"{p}.points", "type", "list of string",
                               type(pts).__name__)
        v = d.get("people")
        if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
            _check(False, errs, "people", "type", "list of string", type(v).__name__)
        if "impact" not in d:
            _check(False, errs, "impact", "missing", "必填字段", None)
        elif not isinstance(d["impact"], str):
            _check(False, errs, "impact", "type", "string", type(d["impact"]).__name__)

    return errs


def _esc(t) -> str:
    return re.sub(r"[\"\[\]{}()|<>#;:\n]", "", str(t))[:80]


# ---------- mermaid 生成（全部由代码生成，不让模型画） ----------

def mermaid_chain(chain) -> str:
    lines = ["flowchart LR"]
    for i, s in enumerate(chain):
        lines.append(f'  s{i}["{_esc(s)}"]')
    for i in range(len(chain) - 1):
        lines.append(f"  s{i} --> s{i+1}")
    return "\n".join(lines)


def mermaid_steps(steps) -> str:
    lines = ["flowchart TD"]
    for i, s in enumerate(steps):
        lines.append(f'  s{i}["{_esc(s.get("step", ""))}"]')
        if i:
            lines.append(f"  s{i-1} --> s{i}")
    if len(steps) > 1:
        lines.append(f"  s{len(steps)-1} -.-> D[\"完成\"]")
    return "\n".join(lines)


def mermaid_timeline(timeline) -> str:
    lines = ["timeline", "  title 时间线"]
    for x in timeline:
        w, e = _esc(x.get("when", "")), _esc(x.get("event", ""))
        lines.append(f"  {w} : {e}")
    return "\n".join(lines)


# ---------- 按类型的 markdown 渲染 ----------

def _hdr(d: dict, src: str) -> list:
    return [f"# {d.get('title', '')}", "",
            f"> **类型**：{d.get('type', '')} · **来源**：{src}",
            f"> **生成**：notes_structurer v4 · {time.strftime('%Y-%m-%d %H:%M')}", ""]


def _logic_md(d, src) -> str:
    out = _hdr(d, src)
    out += [f"> **主旨**：{d.get('thesis', '')}", "", "## 结构大纲", ""]
    for sec in d.get("outline", []):
        lvl = min(max(int(sec.get("level", 1)), 1), 3)
        out.append("#" * (lvl + 1) + " " + str(sec.get("heading", "")))
        for p in sec.get("points", []) or []:
            pl = min(max(int(p.get("level", 1)), 1), 3)
            indent = "  " * (pl - 1)
            out.append(f"{indent}- {p.get('text', '')}")
        out.append("")
    if d.get("concepts"):
        out += ["## 概念表", "", "| 概念 | 定义 | 前置 |", "|---|---|---|"]
        for c in d["concepts"]:
            pre = "、".join(c.get("prereq") or []) or "—"
            out.append(f"| **{c.get('name', '')}** | {c.get('definition', '')} | {pre} |")
        out.append("")
    if d.get("key_chain"):
        out += ["## 主干推进链", ""]
        for i, s in enumerate(d["key_chain"], 1):
            out.append(f"{i}. {s}")
        out += ["", "```mermaid", mermaid_chain(d["key_chain"]), "```", ""]
    return "\n".join(out)


def _decision_md(d, src) -> str:
    out = _hdr(d, src)
    meta = []
    if d.get("subject"):
        meta.append(f"**对象**：{d['subject']}")
    if d.get("score"):
        meta.append(f"**评分**：{d['score']}")
    if d.get("price"):
        meta.append(f"**价格**：{d['price']}")
    out += ["", "> " + " ｜ ".join(meta), ""]
    if d.get("pros"):
        out += ["## 优点", ""]
        out += [f"- {p}" for p in d["pros"]]
        out += [""]
    if d.get("cons"):
        out += ["## 缺点", ""]
        out += [f"- {c}" for c in d["cons"]]
        out += [""]
    if d.get("specs"):
        out += ["## 规格参数", "", "| 项目 | 值 |", "|---|---|"]
        for s in d["specs"]:
            out.append(f"| {s.get('item', '')} | {s.get('value', '')} |")
        out += [""]
    if d.get("target"):
        out += [f"## 适合人群", "", d["target"], ""]
    out += ["## 最终推荐", "", str(d.get("verdict", "")), ""]
    return "\n".join(out)


def _operational_md(d, src) -> str:
    out = _hdr(d, src)
    if d.get("prerequisites"):
        out += ["## 前置条件", ""]
        out += [f"- {p}" for p in d["prerequisites"]]
        out += [""]
    out += ["## 操作步骤", ""]
    for i, st in enumerate(d.get("steps", []), 1):
        out.append(f"### {st.get('step', f'{i}.')}")
        if st.get("detail"):
            out.append("")
            out.append(str(st["detail"]))
        extras = []
        if st.get("tools"):
            extras.append(f"🛠 工具：{st['tools']}")
        if st.get("pitfall"):
            extras.append(f"⚠️ 易错：{st['pitfall']}")
        if extras:
            out.append("")
            out.extend(extras)
        out.append("")
    if d.get("common_pitfalls"):
        out += ["## 常见坑", ""]
        out += [f"- {p}" for p in d["common_pitfalls"]]
        out += [""]
    if d.get("verify"):
        out += ["## 完成标志", "", str(d["verify"]), ""]
    out += ["```mermaid", mermaid_steps(d.get("steps", [])), "```", ""]
    return "\n".join(out)


def _narrative_md(d, src) -> str:
    out = _hdr(d, src)
    fmt = d.get("format", "timeline")
    if fmt == "list":
        out += ["## 条目清单", ""]
        for i, it in enumerate(d.get("items", []), 1):
            out.append(f"### {i}. {it.get('name', '')}")
            if it.get("desc"):
                out += ["", str(it["desc"]), ""]
            for p in it.get("points", []) or []:
                out.append(f"- {p}")
            out.append("")
    else:
        out += ["## 时间线", ""]
        for x in d.get("timeline", []):
            out.append(f"- **{x.get('when', '')}**：{x.get('event', '')}")
            for p in (x.get("points") or []):
                out.append(f"  - {p}")
        out.append("")
    if d.get("people"):
        out += ["## 关键人物", ""]
        out += [f"- {p}" for p in d["people"]]
        out += [""]
    if d.get("impact"):
        out += ["## 影响 / 结论", "", str(d["impact"]), ""]
    if fmt == "list":
        pass  # 清单形态不画 mermaid 时间线
    elif d.get("timeline"):
        out += ["```mermaid", mermaid_timeline(d["timeline"]), "```", ""]
    return "\n".join(out)


def render_md(d: dict, src: str) -> str:
    t = d.get("type")
    if t == "逻辑型":
        return _logic_md(d, src)
    if t == "决策型":
        return _decision_md(d, src)
    if t == "操作型":
        return _operational_md(d, src)
    if t == "叙事型":
        return _narrative_md(d, src)
    return "\n".join(_hdr(d, src) + ["", str(d)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("subtitle")
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT))
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--tag", default="", help="输出文件名后缀，用于区分不同模型")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.subtitle)
    text = src.read_text(encoding="utf-8").strip()
    cfg = load_cfg(CONFIG)
    if args.base_url:
        cfg["base_url"] = args.base_url
    if args.api_key:
        cfg["api_key"] = args.api_key
    model = args.model or cfg.get("model") or "deepseek-v4-flash"

    prompt = USER_TMPL.format(text=text)
    print(f"输入: {src.name} | {len(text)} 字 | 模型: {model} | prompt {len(prompt)} 字")
    if args.dry_run:
        print("--- prompt 预览（末 600 字）---")
        print(prompt[-600:])
        return

    last_err = None
    data = None
    usage = {}
    t0 = time.time()
    for attempt in range(3):
        try:
            data, usage = call_llm(text, cfg, model, temperature=0.0 + 0.15 * attempt)
            v_errs = validate(data)
            if not v_errs:
                break
            raise ValueError(f"schema 校验失败 {len(v_errs)} 处: "
                             + json.dumps(v_errs[:5], ensure_ascii=False))
        except Exception as e:
            last_err = e
            print(f"  尝试 {attempt+1} 失败: {type(e).__name__}: {str(e)[:200]}")
            time.sleep(2)
    if data is None:
        sys.exit(f"三次尝试均失败: {last_err}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[\\/:*?"<>|\r\n]+', "_", src.stem)[:60] + (args.tag or "")
    md = render_md(data, src.name)
    (out_dir / f"{stem}.outline.md").write_text(md, encoding="utf-8")
    (out_dir / f"{stem}.outline.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    stat = {"model": model, "base_url": cfg.get("base_url"),
            "elapsed_s": round(time.time() - t0, 1), "type": data.get("type")}
    if data.get("type") == "逻辑型":
        stat.update(sections=len(data["outline"]), concepts=len(data.get("concepts", [])),
                    key_chain=len(data.get("key_chain", [])))
    elif data.get("type") == "决策型":
        stat.update(pros=len(data.get("pros", [])), cons=len(data.get("cons", [])),
                    specs=len(data.get("specs", [])))
    elif data.get("type") == "操作型":
        stat.update(steps=len(data.get("steps", [])), pitfalls=len(data.get("common_pitfalls", [])))
    elif data.get("type") == "叙事型":
        stat.update(timeline=len(data.get("timeline", [])), people=len(data.get("people", [])))
    stat.update({
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
    })
    (out_dir / f"{stem}.usage.json").write_text(
        json.dumps(stat, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"写出: {out_dir / (stem + '.outline.md')}  | 类型 {data.get('type')}")
    print(f"tokens 入 {usage.get('prompt_tokens','?')} 出 {usage.get('completion_tokens','?')}")


if __name__ == "__main__":
    main()

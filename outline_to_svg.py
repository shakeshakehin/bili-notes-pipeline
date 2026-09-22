#!/usr/bin/env python3
"""outline_to_svg (紧凑版): 把 outline.json 渲染成卡片式树状导图 SVG。

与 v1 的区别（v1 备份在 backups/compact_before_*/outline_to_svg.v1.py）：
  - 要点内联进所属卡片（不再每个要点一个节点）→ 节点数 51→10
  - x 按父节点动态排布（局部紧凑），不再全局按层对齐
  - 间距/字号收紧，逻辑关系（因果/对比/…）上色标签

零依赖、确定性、离线可用。
用法: python outline_to_svg.py <xxx.outline.json> [-o out.svg]
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FONT = "Microsoft YaHei, PingFang SC, sans-serif"
FS_TITLE, FS_PT, FS_BADGE = 15, 12, 10.5
LH_TITLE, LH_PT = 21, 17
PADX, PADY = 13, 9
HGAP, VGAP = 26, 10
MAXW_CARD = 400
SEP = 7
MARGIN = 24

REL_COLORS = {
    "因果": ("#fef3c7", "#b45309"),
    "条件": ("#ffedd5", "#c2410c"),
    "对比": ("#fee2e2", "#b91c1c"),
    "让步": ("#fce7f3", "#be185d"),
    "并列": ("#d1fae5", "#047857"),
    "例证": ("#ede9fe", "#6d28d9"),
    "细化": ("#e0f2fe", "#0369a1"),
    "时序": ("#f1f5f9", "#475569"),
}

PAL = {
    0: dict(fill="#1e293b", stroke="#1e293b", tcol="#ffffff", sep="#475569"),
    1: dict(fill="#eff6ff", stroke="#93c5fd", tcol="#1e3a8a", sep="#bfdbfe"),
    2: dict(fill="#f0fdf4", stroke="#86efac", tcol="#166534", sep="#bbf7d0"),
    3: dict(fill="#fafafa", stroke="#d4d4d8", tcol="#3f3f46", sep="#e4e4e7"),
}


def cwid(ch: str, fs: float) -> float:
    return fs if unicodedata.east_asian_width(ch) in "WF" else fs * 0.56


def text_w(s: str, fs: float) -> float:
    return sum(cwid(c, fs) for c in s)


def wrap(text: str, fs: float, maxw: float) -> list[str]:
    lines, cur, w = [], "", 0.0
    for ch in text:
        c = cwid(ch, fs)
        if w + c > maxw and cur:
            lines.append(cur)
            cur, w = ch, c
        else:
            cur += ch
            w += c
    if cur:
        lines.append(cur)
    return lines or [""]


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def txt(x, y, s, fs, col, bold=False, anchor="start"):
    b = ' font-weight="600"' if bold else ""
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{fs}" '
            f'fill="{col}"{b}{a}>{esc(s)}</text>')


def build(data: dict) -> dict:
    title = data.get("title", "未命名")

    root = {"title": title, "points": [], "children": [], "depth": 0}

    def point_nodes(pts: list, base: int) -> list:
        """pts [{level,text}] → 按 level 递归建子树。相对 depth = base + level，
        让配色按「section(蓝) → 要点(绿) → 细节(灰)」逐级递进。"""
        out: list[dict] = []
        stack: list[tuple[int, dict]] = []
        for p in pts or []:
            lvl = max(1, min(int(p.get("level", 1)), 3))
            node = {"title": str(p.get("text", "")), "points": [],
                    "children": [], "depth": base + lvl}
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            if stack:
                stack[-1][1]["children"].append(node)
            else:
                out.append(node)
            stack.append((lvl, node))
        return out

    stack: dict[int, dict] = {}
    for item in data.get("outline", []):
        lvl = max(1, min(int(item.get("level", 1)), 3))
        node = {"title": str(item.get("heading", "")), "points": [],
                "children": point_nodes(item.get("points"), lvl), "depth": lvl}
        parent = root
        for l in range(lvl - 1, 0, -1):
            if l in stack:
                parent = stack[l]
                break
        parent["children"].append(node)
        stack[lvl] = node
        for k in [k for k in stack if k > lvl]:
            del stack[k]
    return root


def measure(node: dict) -> None:
    fs_t = FS_TITLE if node["depth"] <= 1 else FS_TITLE - 1
    node["fs_t"] = fs_t
    node["title_lines"] = wrap(node["title"], fs_t, MAXW_CARD - 2 * PADX)
    inner = max(text_w(l, fs_t) for l in node["title_lines"])
    node["pts"] = []
    for rel, text in node["points"]:
        bw = (text_w(f"[{rel}]", FS_BADGE) + 10) if rel else 0
        avail = MAXW_CARD - 2 * PADX - (bw + 6 if rel else 0)
        lines = wrap(text, FS_PT, avail)
        node["pts"].append((rel, bw, lines))
        wpt = max(text_w(l, FS_PT) for l in lines)
        inner = max(inner, (bw + 6 + wpt) if rel else wpt)
    node["w"] = inner + 2 * PADX
    h = PADY + len(node["title_lines"]) * LH_TITLE
    if node["pts"]:
        h += SEP + sum(len(p[2]) * LH_PT for p in node["pts"])
    node["h"] = h + PADY
    for c in node["children"]:
        measure(c)


def layout(node: dict, x: float, cursor: list) -> None:
    node["x"] = x
    if not node["children"]:
        node["y"] = cursor[0]
        cursor[0] += node["h"] + VGAP
        return
    kids = node["children"]
    # 单层树（所有直接子节点都是叶子）→ 多列网格，填满横向空间（linear 标记的跳过）
    if not node.get("linear") and all(not c["children"] for c in kids) and len(kids) >= 3:
        node["y"] = cursor[0]              # 父节点先占位
        cursor[0] += node["h"] + VGAP
        cols = 3 if len(kids) >= 6 else 2
        groups: list[list] = [[] for _ in range(cols)]
        for i, c in enumerate(kids):
            groups[i % cols].append(c)  # 轮流分配，列高均衡
        col_x = x + node["w"] + HGAP
        col_ys = [cursor[0]] * cols
        for gi, g in enumerate(groups):
            if not g:
                continue
            gw = max(c["w"] for c in g)
            for c in g:
                c["x"] = col_x
                c["y"] = col_ys[gi]
                col_ys[gi] += c["h"] + VGAP
            col_x += gw + HGAP
        cursor[0] = max(cursor[0], max(col_ys) - VGAP)
        return
    # 深层树：父节点先占位（避免与兄弟卡片重叠），子节点在右侧顺序流
    node["y"] = cursor[0]
    cursor[0] += node["h"] + VGAP
    cx = x + node["w"] + HGAP
    for c in kids:
        layout(c, cx, cursor)


def render(node: dict, out: list) -> None:
    pal = PAL.get(min(node["depth"], 3))
    x, y, w, h = node["x"], node["y"], node["w"], node["h"]
    out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" '
               f'fill="{pal["fill"]}" stroke="{pal["stroke"]}" stroke-width="1.4"/>')
    cy = y + PADY + node["fs_t"] * 0.95
    for ln in node["title_lines"]:
        out.append(txt(x + PADX, cy, ln, node["fs_t"], pal["tcol"], bold=True))
        cy += LH_TITLE
    if node["pts"]:
        cy += SEP * 0.4
        out.append(f'<line x1="{x + PADX:.1f}" y1="{cy:.1f}" x2="{x + w - PADX:.1f}" y2="{cy:.1f}" '
                   f'stroke="{pal["sep"]}" stroke-width="1"/>')
        cy += SEP * 0.6
        for rel, bw, lines in node["pts"]:
            tx = x + PADX
            if rel:
                bg, fg = REL_COLORS.get(rel, ("#e5e7eb", "#374151"))
                bh = FS_BADGE + 6
                by = cy + (LH_PT - FS_PT * 0.95 - bh) / 2 + 1
                out.append(f'<rect x="{tx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                           f'rx="3" fill="{bg}"/>')
                out.append(txt(tx + bw / 2, by + bh * 0.74, f"[{rel}]", FS_BADGE, fg, anchor="middle"))
                tx += bw + 6
            for i, ln in enumerate(lines):
                out.append(txt(tx, cy + FS_PT * 0.95, ln, FS_PT, "#334155"))
                cy += LH_PT
    for c in node["children"]:
        x1, y1 = x + w, y + h / 2
        x2, y2 = c["x"], c["y"] + c["h"] / 2
        mx = (x1 + x2) / 2
        out.append(f'<path d="M{x1:.1f},{y1:.1f} C{mx:.1f},{y1:.1f} {mx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" '
                   f'fill="none" stroke="#cbd5e1" stroke-width="1.4"/>')
        render(c, out)


def iterate(node):
    yield node
    for c in node["children"]:
        yield from iterate(c)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outline_json")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    p = Path(args.outline_json)
    data = json.loads(p.read_text(encoding="utf-8"))
    root = build(data)
    measure(root)
    layout(root, MARGIN, [MARGIN])
    body: list = []
    render(root, body)
    nodes = list(iterate(root))
    W = max(n["x"] + n["w"] for n in nodes) + MARGIN
    H = max(n["y"] + n["h"] for n in nodes) + MARGIN
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
           f'viewBox="0 0 {W:.0f} {H:.0f}"><rect width="100%" height="100%" fill="#ffffff"/>'
           + "".join(body) + "</svg>")
    outp = Path(args.out) if args.out else p.with_suffix(".mindmap.svg")
    outp.write_text(svg, encoding="utf-8")
    print(f"写出: {outp}  ({len(nodes)} 卡片, {W:.0f}x{H:.0f})")


if __name__ == "__main__":
    main()

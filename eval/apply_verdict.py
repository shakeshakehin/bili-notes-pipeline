#!/usr/bin/env python3
"""把 seesee 仲裁结果（verdict.json）回流修订树（outline.json）。

只 patch must_fix / should_add 指出的节点，不重生成整树；validate 兜底；
输出修订前后 diff 报告，供人核对（黑盒"已修正"不被接受）。

用法:
  python eval/apply_verdict.py <out_dir> [--dry-run] [-o revised.json]
说明:
  - out_dir 需含 *.outline.json 与 verdict.json
  - --dry-run 只打印将应用的改动，不改文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notes_structurer as ns


def resolve(root: dict, path: str):
    """按 'outline[2].points[3].text' 定位。支持任意深度（逻辑树 points 可嵌套，
    如 outline[6].points[4].points[0].text）。返回 (父容器, 键) 或 None。"""
    segs = path.split(".")
    if not segs:
        return None
    node: object = root
    for i, seg in enumerate(segs):
        m = re.match(r"^([a-z_]+)(?:\[(\d+)\])?$", seg)
        if not m:
            return None
        name, idx = m.groups()
        if i == len(segs) - 1:
            # 末段：无下标 → 叶子键 (父, 键)；有下标 → 容器元素 (元素, None)
            if not isinstance(node, dict):
                return None
            if idx is not None:
                nxt = node.get(name)
                if not isinstance(nxt, (list, tuple)) or not (0 <= int(idx) < len(nxt)):
                    return None
                return nxt[int(idx)], None
            return node, name
        nxt = node.get(name) if isinstance(node, dict) else None
        if nxt is None:
            return None
        if idx is not None:
            if not isinstance(nxt, (list, tuple)) or not (0 <= int(idx) < len(nxt)):
                return None
            node = nxt[int(idx)]
        else:
            node = nxt
    return node, None


def selftest() -> None:
    """resolve 回归自检：深浅路径、叶子、越界、缺失。"""
    ir = {"outline": [{"heading": "h",
                       "points": [{"text": "a", "points": [{"text": "deep"}]}]}],
          "concepts": [{"name": "c", "definition": "d"}], "key_chain": ["k0"]}
    assert resolve(ir, "outline[0].points[0].points[0].text") == (ir["outline"][0]["points"][0]["points"][0], "text")
    assert resolve(ir, "outline[0].points[0].text") == (ir["outline"][0]["points"][0], "text")
    assert resolve(ir, "concepts[0].definition") == (ir["concepts"][0], "definition")
    assert resolve(ir, "outline[0]") == (ir["outline"][0], None)
    assert resolve(ir, "outline[9]") is None
    assert resolve(ir, "outline[0].nope") == (ir["outline"][0], "nope")  # 键不存在 → 调用方判空跳过
    assert resolve(ir, "key_chain[0].x") is None  # 中间段落到非 dict → None
    print("apply_verdict resolve selftest: 6 passed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    out = Path(args.out_dir)
    ojs = sorted(out.glob("*.outline.json"))
    if not ojs:
        sys.exit(f"无 outline.json: {out}")
    vf = out / "verdict.json"
    if not vf.exists():
        sys.exit(f"无 verdict.json: {vf}")

    ir = json.loads(ojs[0].read_text(encoding="utf-8"))
    verdict = json.loads(vf.read_text(encoding="utf-8"))
    v = verdict.get("verdicts", {})

    applied_fix, applied_add, skipped = [], [], []

    # must_fix：改 text / definition 等叶子值
    for it in v.get("must_fix", []) or []:
        path, fixed = it.get("path", ""), it.get("fixed")
        if not path or not fixed:
            skipped.append(("must_fix 缺 path/fixed", it)); continue
        r = resolve(ir, path)
        if r is None or r[1] is None:
            skipped.append((f"path 无法解析 {path}", it)); continue
        parent, key = r
        cur = parent.get(key)
        if cur is None:
            skipped.append((f"{path} 当前值为空", it)); continue
        # 类型感知写入：list 字段（prereq/points/key_chain）不能被 fixed 字符串覆盖
        if isinstance(cur, list):
            if isinstance(fixed, list):
                parent[key] = fixed
            else:
                try:
                    parsed = json.loads(fixed)
                    if isinstance(parsed, list):
                        parent[key] = parsed
                    else:
                        skipped.append((f"{path} 目标为 list 但 fixed 非 list", it)); continue
                except Exception:
                    skipped.append((f"{path} 目标为 list 但 fixed 无法解析为 list", it)); continue
        elif isinstance(cur, str):
            if not isinstance(fixed, str):
                skipped.append((f"{path} 目标为 str 但 fixed 非 str", it)); continue
            parent[key] = fixed
        else:
            parent[key] = fixed
        applied_fix.append((path, cur, fixed))

    # should_add：向 outline[i].points 插入节点（或 concepts/key_chain）
    for it in v.get("should_add", []) or []:
        path, text = it.get("path", ""), it.get("text")
        lvl = it.get("level", 1)
        if not path or not text:
            skipped.append(("should_add 缺 path/text", it)); continue
        # path 形如 outline[2] 或 outline[2].points
        if ".points" in path:
            sec_path, _, _ = path.partition(".points")
        else:
            sec_path = path
        r = resolve(ir, sec_path)
        if r is None:
            skipped.append((f"section 无法解析 {path}", it)); continue
        parent, leaf_key = r
        if leaf_key is not None or not isinstance(parent, dict):
            # path 指向叶子字段或非 dict 容器（如 steps[1].detail / 顶层 list）
            # → 无法插入 points，跳过
            skipped.append((f"should_add 指向非对象容器 {path}", it)); continue
        pts = parent.setdefault("points", [])
        new = {"level": min(max(int(lvl), 1), 3), "text": text}
        idx = it.get("index")
        if idx is not None and 0 <= int(idx) <= len(pts):
            pts.insert(int(idx), new)
        else:
            pts.append(new)
        applied_add.append((path, new, it.get("reason", "")))

    # validate 兜底
    errs = ns.validate(ir)
    print(f"修订后 validate: {'通过' if not errs else f'{len(errs)} 处错误'}")
    if errs:
        print(json.dumps(errs[:5], ensure_ascii=False, indent=1))
        if not args.dry_run:
            sys.exit("validate 未过，已中止不写盘（修订引入了结构问题）")

    print(f"\n== 应用修订: must_fix {len(applied_fix)} / should_add {len(applied_add)} / 跳过 {len(skipped)} ==")
    for path, cur, fixed in applied_fix:
        print(f"  FIX {path}: {cur[:35]!r} → {fixed[:35]!r}")
    for path, new, reason in applied_add:
        print(f"  ADD {path}: +{new['text'][:40]!r}")
    for why, it in skipped:
        print(f"  SKIP {why}: {it}")

    if args.dry_run:
        print("\n--dry-run：未写盘")
        return

    dst = Path(args.out) if args.out else ojs[0].with_name(ojs[0].stem + "_revised.json")
    dst.write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n修订树写出: {dst}（validate {'通过' if not errs else '未通过但已写盘'}）")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    main()

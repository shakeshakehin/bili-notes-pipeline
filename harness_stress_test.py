#!/usr/bin/env python3
"""Logic-Only 全逻辑型管道压力测试 driver（v5.4）。

分阶段批量执行（用户指令：先一次性抓取全部 → 再一次性清洗 → 再 seesee），
逐视频记录：字幕字数 + 每一步耗时 + 结构/内容指标，汇总 stress_test_report.md。

用法:
  python harness_stress_test.py fetch|repair|struct|seesee|apply|svg|report [--only G1,G2]
  python harness_stress_test.py all            # 全部阶段顺序跑
  断点续跑：已完成的阶段/任务自动跳过（进度存在 test/stress_progress.json）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

WORK = Path(r"E:\AIbulid\hermesonly")
TEST = Path(r"C:\Users\Administrator\Desktop\新建文件夹\test")
UVPY = Path(os.path.expanduser("~")) / "AppData/Roaming/uv/tools/bilibili-cli/Scripts/python.exe"
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROG = TEST / "stress_progress.json"
_LOCK = threading.Lock()

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def load_tasks() -> list[dict]:
    """解析 BV.txt → 任务列表（去重）。G2 分P 用 page 标记。"""
    tasks: list[dict] = []
    seen: set = set()
    grad = ""
    for line in TEST.joinpath("BV.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"G[1-4]", line):
            grad = line
            continue
        m = BV_RE.search(line)
        if not m:
            continue
        p = None
        pm = re.search(r"[?&]p=(\d+)", line)
        if pm:
            p = int(pm.group(1))
        key = (grad, m.group(1), p)
        if key in seen:
            continue
        seen.add(key)
        tasks.append({"grad": grad, "bv": m.group(1), "page": p})
    return tasks


def task_dir(t: dict) -> Path:
    page = f"_p{t['page']:02d}" if t["page"] else ""
    return TEST / f"{t['grad']}_{t['bv']}{page}"


def task_key(t: dict) -> str:
    return t["grad"] + "_" + t["bv"] + (f"_p{t['page']}" if t["page"] else "")


def load_prog() -> dict:
    return json.loads(PROG.read_text(encoding="utf-8")) if PROG.exists() else {}


def save_prog(p: dict) -> None:
    # default=str: 防御偶发 Path 等非 JSON 类型混入记录，压测进度不因单点数据异常中断
    with _LOCK:
        PROG.write_text(json.dumps(p, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def run(cmd: list, timeout: int = 900) -> tuple[int, str]:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def timed(tag: str, fn) -> tuple[float, object]:
    t0 = time.time()
    try:
        out = fn()
        return round(time.time() - t0, 1), out
    except Exception as e:
        return round(time.time() - t0, 1), ("EXC", str(e))


def find_subtitle(d: Path) -> Path | None:
    hits = sorted(d.glob("*.subtitle.txt"))
    return hits[0] if hits else None


def get_subtitle(d: Path, page: int | None) -> Path | None:
    """按任务页码选字幕（G2 分P：--all 会写入全部 P，须筛 _P{page:02d}_）。"""
    if page:
        hits = sorted(d.glob(f"*_P{page:02d}_*.subtitle.txt"))
        if hits:
            return hits[0]
        return None  # 目标 P 缺失 → 显式失败，绝不用其他 P 冒充
    return find_subtitle(d)


def normalize_g2_dir(d: Path, page: int) -> None:
    """清理 G2 分P 目录：只保留目标 P 的字幕文件，删除其他 P 的（防选错）。"""
    keep = f"*_P{page:02d}_*.subtitle.txt"
    for f in d.glob("*_P*.subtitle.txt"):
        if not f.match(keep):
            f.unlink()


# ---------- 阶段实现 ----------

def stage_fetch(tasks, prog, only) -> None:
    for t in tasks:
        if only and t["grad"] not in only:
            continue
        d = task_dir(t)
        rec = prog.setdefault(t["grad"] + "_" + t["bv"] + (f"_p{t['page']}" if t["page"] else ""), {})
        if rec.get("fetch_ok"):
            continue
        d.mkdir(parents=True, exist_ok=True)
        args = [UVPY, WORK / "bili_fetch.py", t["bv"], "-o", d, "--subtitle-only"]
        if t["grad"] == "G2":
            args.append("--all")
        dt, (rc, out) = timed("fetch", lambda: run(args))
        # bili_fetch 对无 CC 字幕视频退出码为 0 但不产出文件 → 按产物判，不按 rc 判
        hits = list(d.glob("*.subtitle.txt"))
        rec["fetch_ok"] = rc == 0 and len(hits) > 0
        if rc == 0 and not hits:
            rec["fetch_err"] = "无可用字幕(rc=0 无产物)"
        rec["t_fetch"] = dt
        if not rec["fetch_ok"]:
            rec["fetch_err"] = rec.get("fetch_err") or out.strip()[-200:]
            print(f"  [fetch] FAIL {t['grad']} {t['bv']} p{t['page']}: {rec['fetch_err']}")
        else:
            print(f"  [fetch] ok {t['grad']} {t['bv']} p{t['page']} {dt}s")
        save_prog(prog)


def stage_repair(tasks, prog, only) -> None:
    for t in tasks:
        if only and t["grad"] not in only:
            continue
        d = task_dir(t)
        key = t["grad"] + "_" + t["bv"] + (f"_p{t['page']}" if t["page"] else "")
        rec = prog.get(key, {})
        if not rec.get("fetch_ok") or rec.get("repair_ok"):
            continue
        if t["grad"] == "G2":
            normalize_g2_dir(d, t["page"])
        src = get_subtitle(d, t["page"])
        if src is None:
            rec["repair_ok"] = False; rec["repair_err"] = "no subtitle"; save_prog(prog); continue
        # subtitle_repair 的 -o 是「输出前缀」：传 d/repaired → 产出 d/repaired.txt + _external_prompt.txt
        prefix = d / "repaired"
        dt, (rc, out_text) = timed("repair", lambda: run([PY, WORK / "eval" / "subtitle_repair.py", str(src), "-o", str(prefix)]))
        rep = d / "repaired.txt"
        rec["t_repair"] = dt
        rec["repair_ok"] = rc == 0 and rep.exists()
        if rec["repair_ok"]:
            rec["chars_raw"] = len(src.read_text(encoding="utf-8").strip())
            rec["chars_rep"] = len(rep.read_text(encoding="utf-8").strip())
            print(f"  [repair] ok {key} {dt}s raw={rec['chars_raw']} rep={rec['chars_rep']}")
        else:
            rec["repair_err"] = out_text.strip()[-200:]
            print(f"  [repair] FAIL {key}: {rec['repair_err']}")
        save_prog(prog)


def _do_struct(t, d, key, rec, prog) -> None:
    """单视频结构化（供串行阶段与并发 pipe 复用）。"""
    if not rec.get("repair_ok") or rec.get("struct_ok"):
        return
    rep = d / "repaired.txt"
    dt, (rc, out_text) = timed("struct", lambda: run([PY, WORK / "notes_structurer.py", str(rep), "-o", str(d)]))
    ojs = sorted(d.glob("*.outline.json"))
    ok = rc == 0 and ojs
    rec["t_struct"] = dt
    rec["struct_ok"] = ok
    if ok:
        ir = json.loads(ojs[0].read_text(encoding="utf-8"))
        rec["type"] = ir.get("type")
        rec["sections"] = len(ir.get("outline", []))
        rec["points"] = sum(len(s.get("points", [])) for s in ir.get("outline", []))
        rec["attempts"] = 1 + out_text.count("尝试 1 失败") + out_text.count("尝试 2 失败")
        rec["first_attempt_ok"] = "尝试 1 失败" not in out_text
        rec["outline_json"] = str(ojs[0].name)
        rec["levels"] = [pt.get("level") for s in ir.get("outline", []) for pt in s.get("points", [])]
        print(f"  [struct] ok {key} {dt}s type={rec['type']} pts={rec['points']} attempts={rec['attempts']}", flush=True)
    else:
        rec["struct_err"] = out_text.strip()[-200:]
        print(f"  [struct] FAIL {key}: {rec['struct_err']}", flush=True)
    save_prog(prog)


def _do_seesee(t, d, key, rec, prog) -> None:
    """单视频 seesee 仲裁（供串行阶段与并发 pipe 复用）。"""
    if not rec.get("struct_ok") or rec.get("seesee_ok"):
        return
    dt, (rc, out_text) = timed("seesee", lambda: run([PY, WORK / "eval" / "step5_seesee.py", str(d), "--name", key, "--direct"]))
    rec["t_seesee"] = dt
    rec["seesee_ok"] = rc == 0 and (d / "verdict.json").exists()
    if rec["seesee_ok"]:
        print(f"  [seesee] ok {key} {dt}s", flush=True)
    else:
        rec["seesee_err"] = out_text.strip()[-200:]
        print(f"  [seesee] FAIL {key}: {rec['seesee_err']}", flush=True)
    save_prog(prog)


def stage_struct(tasks, prog, only) -> None:
    for t in tasks:
        if only and t["grad"] not in only:
            continue
        _do_struct(t, task_dir(t), task_key(t), prog.get(task_key(t), {}), prog)


def stage_seesee(tasks, prog, only) -> None:
    for t in tasks:
        if only and t["grad"] not in only:
            continue
        _do_seesee(t, task_dir(t), task_key(t), prog.get(task_key(t), {}), prog)


def stage_pipe(tasks, prog, only, workers: int = 3) -> None:
    """并发流水线：不同视频的 struct→seesee 链并行跑（同一视频内仍串行）。
    提速依据：两个环节都是网络 IO 等待，线程并发不共享状态（子进程隔离），
    唯一共享点是 save_prog（已加锁）。"""
    pending = []
    for t in tasks:
        if only and t["grad"] not in only:
            continue
        key = task_key(t)
        rec = prog.get(key, {})
        if not rec.get("repair_ok"):
            continue
        if rec.get("struct_ok") and rec.get("seesee_ok"):
            continue
        pending.append((t, task_dir(t), key, rec))

    def chain(item):
        t, d, key, rec = item
        _do_struct(t, d, key, rec, prog)
        _do_seesee(t, d, key, rec, prog)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(chain, pending))


def stage_apply(tasks, prog, only) -> None:
    for t in tasks:
        if only and t["grad"] not in only:
            continue
        d = task_dir(t)
        key = t["grad"] + "_" + t["bv"] + (f"_p{t['page']}" if t["page"] else "")
        rec = prog.get(key, {})
        if not rec.get("seesee_ok") or rec.get("apply_ok"):
            continue
        dt, (rc, out_text) = timed("apply", lambda: run([PY, WORK / "eval" / "apply_verdict.py", str(d)]))
        revs = sorted(d.glob("*_revised.json"))
        rec["t_apply"] = dt
        rec["apply_ok"] = rc == 0 and revs
        if rec["apply_ok"]:
            rec["revised_json"] = str(revs[0].name)
            rec["apply_skips"] = out_text.count("SKIP ")
            print(f"  [apply] ok {key} {dt}s skips={rec['apply_skips']}")
        else:
            rec["apply_err"] = out_text.strip()[-200:]
            print(f"  [apply] FAIL {key}: {rec['apply_err']}")
        save_prog(prog)


def stage_svg(tasks, prog, only) -> None:
    for t in tasks:
        if only and t["grad"] not in only:
            continue
        d = task_dir(t)
        key = t["grad"] + "_" + t["bv"] + (f"_p{t['page']}" if t["page"] else "")
        rec = prog.get(key, {})
        if not rec.get("apply_ok") or rec.get("svg_ok"):
            continue
        ir = d / rec["revised_json"]
        dt, (rc, _) = timed("svg", lambda: run([PY, WORK / "outline_to_svg.py", str(ir), "-o", str(d / "tree.svg")]))
        svg = d / "tree.svg"
        rec["t_svg"] = dt
        rec["svg_ok"] = rc == 0 and svg.exists()
        if rec["svg_ok"]:
            m = re.search(r'width="(\d+)" height="(\d+)"', svg.read_text(encoding="utf-8"))
            rec["svg_size"] = f"{m.group(1)}x{m.group(2)}" if m else "?"
            rec["t_png"] = None
            t0 = time.time()
            png = d / "tree.png"
            okpng = False
            for _ in range(2):
                tmpd = rf"C:\Users\Administrator\AppData\Local\Temp\stress-{uuid.uuid4().hex[:8]}"
                run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                     "--force-device-scale-factor=1.6", f"--user-data-dir={tmpd}",
                     f"--window-size={m.group(1)},{m.group(2)}",
                     f"--screenshot={png.resolve()}", "file:///" + svg.resolve().as_posix()])
                if png.exists() and png.stat().st_size > 1000:
                    okpng = True
                    break
            rec["png_ok"] = okpng
            rec["png_bytes"] = png.stat().st_size if okpng else None
            rec["t_png"] = round(time.time() - t0, 1)
            print(f"  [svg] ok {key} {dt}s png={rec['png_bytes']} ({rec['t_png']}s)")
        else:
            rec["svg_err"] = "svg fail"
            print(f"  [svg] FAIL {key}")
        save_prog(prog)


# ---------- 汇总 ----------

LEAK_RE = re.compile(r"（字幕中|字幕中|ASR误|疑为|原文为|应为|修正为")


def compute_metrics(prog, tasks) -> dict:
    grad_map = {task_key(t): t["grad"] for t in tasks}
    recs = list(prog.items())  # (key, rec)：grad 不在 rec 里，须经 key 反查
    done = [(k, r) for k, r in recs if r.get("svg_ok")]
    m = {
        "total_tasks": len(tasks),
        "tasks_done": len(done),
        "success_rate": round(100 * len(done) / max(len(recs), 1), 1),
        # 首 attempt 口径分母 = 跑过结构化的任务（有该字段），不是全部任务
        "first_attempt_ok": round(100 * sum(1 for k, r in recs if r.get("first_attempt_ok")) / max(len([1 for k, r in recs if "first_attempt_ok" in r]), 1), 1),
        "avg_t_fetch": round(sum(r["t_fetch"] for k, r in recs if "t_fetch" in r) / max(len([r for k, r in recs if "t_fetch" in r]), 1), 1),
        "avg_t_struct": round(sum(r["t_struct"] for k, r in recs if "t_struct" in r) / max(len([r for k, r in recs if "t_struct" in r]), 1), 1),
        "avg_t_seesee": round(sum(r["t_seesee"] for k, r in recs if "t_seesee" in r) / max(len([r for k, r in recs if "t_seesee" in r]), 1), 1),
        "avg_t_repair": round(sum(r["t_repair"] for k, r in recs if "t_repair" in r) / max(len([r for k, r in recs if "t_repair" in r]), 1), 1),
        "avg_t_apply": round(sum(r["t_apply"] for k, r in recs if "t_apply" in r) / max(len([r for k, r in recs if "t_apply" in r]), 1), 1),
        "avg_t_svg": round(sum(r["t_svg"] for k, r in recs if "t_svg" in r) / max(len([r for k, r in recs if "t_svg" in r]), 1), 1),
        "avg_t_png": round(sum(r["t_png"] for k, r in recs if r.get("t_png")) / max(len([r for k, r in recs if r.get("t_png")]), 1), 1),
        "meta_leak": 0,
        "leak_examples": [],
        "empty_node": 0,
        "depth_stats": {},
        "levels_all_1": [],
        "by_grad": {},
    }
    leak_total = empty_total = 0
    for k, r in done:
        g = grad_map.get(k, "?")
        m["by_grad"].setdefault(g, {"done": 0, "t_struct_sum": 0})
        m["by_grad"][g]["done"] += 1
        m["by_grad"][g]["t_struct_sum"] += r.get("t_struct", 0)
        lv = {}
        for s in r.get("levels", []):
            lv[s] = lv.get(s, 0) + 1
        for l, v in lv.items():
            m["depth_stats"][l] = m["depth_stats"].get(l, 0) + v
        if set(lv) == {1}:
            m["levels_all_1"].append(k)
        # 元文字泄露 / 空节点：读最终树（revised 优先）
        d = TEST / k
        src = d / (r.get("revised_json") or r.get("outline_json") or "")
        if src.exists():
            try:
                ir = json.loads(src.read_text(encoding="utf-8"))
                for s in ir.get("outline", []):
                    for pt in s.get("points", []):
                        txt = str(pt.get("text", ""))
                        if not txt.strip():
                            m["empty_node"] += 1
                        if LEAK_RE.search(txt):
                            m["meta_leak"] += 1
                            if len(m["leak_examples"]) < 3:
                                m["leak_examples"].append(f"{k}: {txt[:40]}")
                for c in ir.get("concepts", []):
                    for f in ("name", "definition"):
                        txt = str(c.get(f, ""))
                        if LEAK_RE.search(txt):
                            m["meta_leak"] += 1
                for kc in ir.get("key_chain", []):
                    if LEAK_RE.search(str(kc)):
                        m["meta_leak"] += 1
            except Exception:
                pass
        leak_total += 1
    m["meta_leak_pct"] = round(100 * m["meta_leak"] / max(leak_total, 1), 1)
    return m


def stage_report(tasks, prog, only) -> None:
    m = compute_metrics(prog, tasks)
    lines = ["# 管道压力测试报告 (Logic-Only Mode v5.4)", "", "## 1. 测试概览",
             f"- 总任务数: {m['total_tasks']} (G1:7 G2:4 G3:4 G4:3，已去重)",
             f"- 完成数: {m['tasks_done']} / 成功率: {m['success_rate']}%",
             f"- 平均耗时(s): 抓取 {m['avg_t_fetch']} | 清洗 {m['avg_t_repair']} | 结构化 {m['avg_t_struct']} | seesee {m['avg_t_seesee']} | 修订 {m['avg_t_apply']} | SVG {m['avg_t_svg']} | PNG {m['avg_t_png']}", "",
             "## 2. 核心指标", "| 指标 | 结果 | 目标 | 结论 |", "|---|---|---|---|",
             f"| Pipeline 成功率 | {m['success_rate']}% | ≥98% | {'✅' if m['success_rate'] >= 98 else '⚠️'} |",
             f"| 首 Attempt Schema 通过率 | {m['first_attempt_ok']}% | ≥90% | {'✅' if m['first_attempt_ok'] >= 90 else '⚠️'} |",
             f"| 元文字泄露 | {m['meta_leak']} 处 ({m['meta_leak_pct']}%/树) | 0 | {'✅' if m['meta_leak'] == 0 else '⚠️'} |",
             f"| 空节点 | {m['empty_node']} 个 | 0 | {'✅' if m['empty_node'] == 0 else '⚠️'} |",
             f"| 渲染崩溃率(失败未出PNG) | {round(100 * (m['tasks_done'] - sum(1 for r in prog.values() if r.get('png_ok'))) / max(m['tasks_done'],1), 1)}% | 0% | ⚠️ 需看PNG |",
             "", "## 3. 层级深度分布", json.dumps(m["depth_stats"], ensure_ascii=False),
             f"- 退化为纯Level1的任务: {m['levels_all_1'] or '无'}", "",
             "## 4. 分梯度明细", "| 任务 | 梯度 | 字数(raw) | t_fetch | t_repair | t_struct | t_seesee | t_apply | t_svg | png | 首attempt | 状态 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in tasks:
        key = t["grad"] + "_" + t["bv"] + (f"_p{t['page']}" if t["page"] else "")
        r = prog.get(key, {})
        st = "✅" if r.get("svg_ok") else ("❌ " + str(r.get("struct_err") or r.get("seesee_err") or r.get("fetch_err") or r.get("apply_err") or "")[:40])
        lines.append(f"| {key} | {t['grad']} | {r.get('chars_raw', '-')} | {r.get('t_fetch','-')} | {r.get('t_repair','-')} | {r.get('t_struct','-')} | {r.get('t_seesee','-')} | {r.get('t_apply','-')} | {r.get('t_svg','-')} | {r.get('png_bytes','-')} | {'✅' if r.get('first_attempt_ok') else '❌'} | {st} |")
    (TEST / "stress_test_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"报告写出: {TEST / 'stress_test_report.md'}")


STAGES = {"fetch": stage_fetch, "repair": stage_repair, "struct": stage_struct,
          "seesee": stage_seesee, "pipe": stage_pipe, "apply": stage_apply,
          "svg": stage_svg, "report": stage_report}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", nargs="?", default="all", help="fetch|repair|struct|seesee|pipe|apply|svg|report|all")
    ap.add_argument("--only", default=None, help="只跑指定梯度, 逗号分隔 G1,G2")
    ap.add_argument("--workers", type=int, default=3, help="pipe 阶段并发数（默认 3）")
    args = ap.parse_args()
    tasks = load_tasks()
    prog = load_prog()
    print(f"任务: {len(tasks)} 条 | {TEST}")
    order = ["fetch", "repair", "struct", "seesee", "apply", "svg", "report"]
    stages = order if args.stage == "all" else [s.strip() for s in args.stage.split(",")]
    only = set(args.only.split(",")) if args.only else None
    for s in stages:
        print(f"\n=== 阶段 {s} ===", flush=True)
        if s == "pipe":
            stage_pipe(tasks, prog, only, workers=args.workers)
        elif s in STAGES:
            STAGES[s](tasks, prog, only)
            save_prog(prog)


if __name__ == "__main__":
    main()

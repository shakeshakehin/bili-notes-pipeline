#!/usr/bin/env python3
"""M0 eval harness：批量评测 notes_structurer 的 schema 合法率。

核心口径：schema 合法率 = 首次 attempt 通过率。
重试只做兜底（保证最终产物可用），不计入质量指标——否则重试会把合法率刷成 100%。

每条样本记录：类型 / 输出 JSON / 首次 attempt 是否通过 / 最终重试次数 / usage。

用法：
  python eval_harness.py                        # 跑 eval/samples/ 下全部字幕
  python eval_harness.py --samples 目录          # 指定样本目录
  python eval_harness.py --samples 文件1 文件2   # 指定文件列表
  python eval_harness.py --max-samples 3        # 只跑前 N 个（快速冒烟）
  python eval_harness.py --report 路径.json      # 自定义报告输出路径

输出：
  eval/report.json   结构化报告（meta + 指标 + 每条明细 + 错误分布）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import notes_structurer as ns

MAX_ATTEMPTS = 3
DEFAULT_SAMPLES = Path(__file__).parent / "eval" / "samples"
DEFAULT_REPORT = Path(__file__).parent / "eval" / "report.json"


def run_one(path: Path, cfg: dict, model: str, temperature: float | None = None) -> dict:
    """跑一条样本，返回结构化记录。

    temperature: None → 升温重试策略 0.2/0.35/0.5（生产默认）；
                 给定值  → 固定该温度（用于 A/B 对比温度影响）。
    """
    text = path.read_text(encoding="utf-8").strip()
    rec: dict = {"file": path.name, "chars": len(text)}

    data = None
    usage = {}
    last_err = None
    t0 = time.time()

    for attempt in range(MAX_ATTEMPTS):
        try:
            temp = temperature if temperature is not None else 0.0 + 0.15 * attempt
            d, u = ns.call_llm(text, cfg, model, temperature=temp)
            errs = ns.validate(d)
            if attempt == 0:
                rec["first_attempt_errors"] = errs
            if not errs:
                data, usage = d, u
                rec["final_attempts"] = attempt + 1
                break
            last_err = (f"schema 校验失败 {len(errs)} 处: "
                        + json.dumps(errs[:3], ensure_ascii=False)[:250])
        except Exception as e:  # HTTP 失败 / JSON 解析失败等
            if attempt == 0:
                rec["first_attempt_errors"] = [{
                    "path": "<call>", "kind": "exception",
                    "expected": "HTTP 200 + 合法 JSON",
                    "actual": f"{type(e).__name__}: {str(e)[:150]}"}]
            last_err = f"{type(e).__name__}: {str(e)[:250]}"
        time.sleep(2)

    rec["elapsed_s"] = round(time.time() - t0, 1)
    if data is not None:
        rec.update(pass_=True, type=data.get("type"), output=data, usage=usage,
                   first_attempt_pass=not rec.get("first_attempt_errors"))
    else:
        rec.update(pass_=False, first_attempt_pass=False, error=last_err)
    return rec


def tree_metrics(records: list[dict]) -> dict:
    """树合理性指标（仅逻辑型）：level 分布 / 树深 / 每节分支数。
    回答「树歪不歪」：level1 过少=扁平堆叠，level2/3 过少=树没展开。"""
    level_dist = Counter()
    max_depths: list[int] = []
    branches: list[float] = []
    pts_per_sec: list[float] = []
    n_logic = 0
    for r in records:
        o = r.get("output") or {}
        if o.get("type") != "逻辑型":
            continue
        n_logic += 1
        for sec in o.get("outline", []):
            pts = sec.get("points", []) or []
            if not pts:
                continue
            lvls = [int(p.get("level", 1)) for p in pts]
            for lv in lvls:
                level_dist[lv] += 1
            max_depths.append(max(lvls))
            branches.append(sum(1 for lv in lvls if lv == 1))
            pts_per_sec.append(len(pts))
    n_sec = len(max_depths) or 1
    return {
        "logic_samples": n_logic,
        "sections": len(max_depths),
        "point_level_dist": dict(sorted(level_dist.items())),
        "avg_tree_depth": round(sum(max_depths) / n_sec, 2),
        "avg_branches_per_section": round(sum(branches) / n_sec, 2),
        "avg_points_per_section": round(sum(pts_per_sec) / n_sec, 2),
    }


def aggregate(records: list[dict]) -> dict:
    """从每条记录聚合指标。"""
    n = len(records) or 1
    first_pass = sum(1 for r in records if r["first_attempt_pass"])
    final_pass = sum(1 for r in records if r["pass_"])
    attempts = [r["final_attempts"] for r in records if r.get("final_attempts")]
    retries = [a - 1 for a in attempts]

    err_path_kind = Counter()
    err_kind = Counter()
    err_actual = Counter()
    for r in records:
        for e in r.get("first_attempt_errors") or []:
            err_path_kind[(e.get("path"), e.get("kind"))] += 1
            err_kind[e.get("kind")] += 1
            if e.get("kind") == "enum":
                err_actual[str(e.get("actual"))] += 1

    type_counter = Counter(r.get("type") for r in records if r.get("type"))

    return {
        "n_samples": len(records),
        "schema_valid_rate_first_attempt": round(first_pass / n, 4),
        "first_attempt_pass": first_pass,
        "final_pass_rate": round(final_pass / n, 4),
        "final_pass": final_pass,
        "avg_retries": round(sum(retries) / n, 2),
        "total_retries": sum(retries),
        "avg_elapsed_s": round(sum(r["elapsed_s"] for r in records) / n, 1),
        "total_tokens": sum((r.get("usage") or {}).get("total_tokens") or 0 for r in records),
        "type_distribution": dict(type_counter),
        "tree": tree_metrics(records),
        "error_dist": {
            "by_path_kind": [{"path": k[0], "kind": k[1], "count": v}
                             for k, v in err_path_kind.most_common()],
            "by_kind": dict(err_kind),
            "enum_actual_values": [{"actual": k, "count": v}
                                   for k, v in err_actual.most_common(20)],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="M0 eval: schema 合法率（首次 attempt 口径）")
    ap.add_argument("--samples", nargs="+", default=[str(DEFAULT_SAMPLES)],
                    help="样本目录或文件列表（默认 eval/samples/）")
    ap.add_argument("--max-samples", type=int, default=None, help="最多跑前 N 个")
    ap.add_argument("--model", default=None)
    ap.add_argument("--temperature", type=float, default=None,
                    help="固定温度（默认 None=升温重试 0.2/0.35/0.5）")
    ap.add_argument("--report", default=str(DEFAULT_REPORT), help="报告输出路径")
    args = ap.parse_args()

    # 解析样本列表：参数可以是目录或文件
    files: list[Path] = []
    for s in args.samples:
        p = Path(s)
        if p.is_dir():
            files.extend(sorted(p.glob("*.txt")))
        elif p.is_file():
            files.append(p)
        else:
            sys.exit(f"样本路径不存在: {s}")
    if args.max_samples:
        files = files[: args.max_samples]
    if not files:
        sys.exit("没有找到样本文件")

    cfg = ns.load_cfg(ns.CONFIG)
    model = args.model or cfg.get("model") or "deepseek-v4-flash"
    print(f"样本 {len(files)} 条 | 模型 {model} | 端点 {cfg.get('base_url')}")

    records = []
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name} …", flush=True)
        rec = run_one(f, cfg, model, temperature=args.temperature)
        flag = "PASS" if rec["first_attempt_pass"] else "FAIL"
        print(f"   首attempt {flag} | 类型 {rec.get('type','-')} | "
              f"重试 {max(rec.get('final_attempts',0)-1,0)} | "
              f"{rec['elapsed_s']}s | tokens {rec.get('usage',{}).get('total_tokens','-')}")
        records.append(rec)

    metrics = aggregate(records)
    report = {
        "meta": {
            "model": model, "base_url": cfg.get("base_url"),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "temperature": args.temperature,
            "samples": [r["file"] for r in records],
            "max_attempts": MAX_ATTEMPTS,
            "notes": "schema 合法率 = 首次 attempt 通过率；重试仅兜底",
        },
        "metrics": metrics,
        "per_sample": records,
    }
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    m = metrics
    print("\n================ 汇总 ================")
    print(f"样本数           : {m['n_samples']}")
    print(f"schema 合法率(首attempt): {m['first_attempt_pass']}/{m['n_samples']} = "
          f"{m['schema_valid_rate_first_attempt']:.1%}")
    print(f"最终通过率       : {m['final_pass']}/{m['n_samples']} = {m['final_pass_rate']:.1%}")
    print(f"平均重试次数     : {m['avg_retries']}（总 {m['total_retries']}）")
    print(f"平均耗时         : {m['avg_elapsed_s']}s / 条")
    print(f"总 tokens        : {m['total_tokens']}")
    print(f"类型分布         : {m['type_distribution']}")
    if m["tree"]["logic_samples"]:
        t = m["tree"]
        print(f"树合理性(逻辑型) : level分布 {t['point_level_dist']} | "
              f"平均树深 {t['avg_tree_depth']} | 平均每节分支 {t['avg_branches_per_section']} | "
              f"每节要点 {t['avg_points_per_section']}")
    if m["error_dist"]["by_kind"]:
        print(f"错误类型分布     : {m['error_dist']['by_kind']}")
        print("错误位置 TOP5    :")
        for e in m["error_dist"]["by_path_kind"][:5]:
            print(f"   {e['count']}x  {e['path']} ({e['kind']})")
        if m["error_dist"]["enum_actual_values"]:
            print("relation 非法实际值:")
            for e in m["error_dist"]["enum_actual_values"][:10]:
                print(f"   {e['count']}x  {e['actual']!r}")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()

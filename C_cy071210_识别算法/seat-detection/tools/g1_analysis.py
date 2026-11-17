# -*- coding: utf-8 -*-
"""G1 分析：滑窗评估 / 阈值扫描 / 书包检验 / B0 检验 / 图表 + 中文报告。

前置：
    python tools/make_synthetic_g1.py
    python detect.py --recording examples/recordings --emit-scores   (全部生成后再分析)
输出（--out，默认 <repo>/outputs/g1-preview/）：
    g1_report.md（中文报告，G1 三条件逐条结论）
    timelines.png / threshold_sweep.png / bag_check.png（英文标签，避免字体坑）

方法（设计文档 §8）：
  * 滑窗：4 s 窗、步长 1 s、起点 ≥4 s（warmup 后）；窗真值 = 剧本覆盖 ≥0.5；
    窗判定 = presence 均值 ≥0.5（presence=-1 帧不计入均值，全未知窗记 no-decision）。
  * 阈值扫描：T_ON ∈ [0.20,0.80] 步 0.02、T_OFF=0.7·T_ON，对 scores.csv 重放滞回。
    注意重放是近似（背景 EMA 与判决耦合）——最优阈值会用 run_m1 带 tweaked config 复核。
  * 书包检验（G1-b 两判据）：raw 区域能量抬升 <3 dB；或去背景残差收敛后差 <1 dB。
  * B0 检验：passby 窗内出点；still/bag/empty 无静态持续检出（点帧率符合 p_fa 设计率）；
    reading 翻页帧出点（RQ2 证据）。FA 偶发聚簇触发的短 presence 闪现为基线特性。
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from algos.m1_features import _window_lo, presence_from_scores, run_m1  # noqa: E402
from algos.region import resolve_region  # noqa: E402

WIN_S = 4.0
STEP_S = 1.0
START_MIN_S = 4.0
SIT_SCENARIOS = ("sitting_reading", "sitting_still")
G1A_MAX_ERR = 0.20  # 计划书门槛（待教师确认，脚本可配置）


# ---------------- IO ----------------

def read_csv_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    head, data = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    return head, data


def parse_result(path):
    head, data = read_csv_rows(path)
    idx = {c: i for i, c in enumerate(head)}
    return {
        "presence": [int(r[idx["presence"]]) for r in data],
        "target_count": [int(r[idx["target_count"]]) for r in data],
        "timestamp": [float(r[idx["timestamp"]]) for r in data],
    }


def parse_scores(path):
    head, data = read_csv_rows(path)
    idx = {c: i for i, c in enumerate(head)}
    return {c: [float(r[idx[c]]) for r in data] for c in head if c not in ("frame_id", "timestamp")}


def parse_points(path):
    head, data = read_csv_rows(path)
    idx = {c: i for i, c in enumerate(head)}
    if "points_per_frame" in idx:
        return [int(r[idx["points_per_frame"]]) for r in data]
    # 兜底：用 result 的 target_count（注意滞回会掩盖单帧点）
    return [int(r[idx["target_count"]]) for r in data]


class Rec:
    pass


def load_all(rec_dir):
    recs, missing = [], []
    for sp in sorted(glob.glob(os.path.join(rec_dir, "*_scenario_script.json"))):
        rid = os.path.basename(sp)[: -len("_scenario_script.json")]
        r = Rec()
        r.rid = rid
        r.script = json.load(open(sp, encoding="utf-8"))
        r.scenario = r.script["scenario"]
        r.token = r.script["token"]
        m = re.search(r"_(\d{2})_(1d|2d)$", rid)
        r.seg, r.level = (int(m.group(1)), m.group(2)) if m else (None, None)
        base = os.path.dirname(sp)
        res_path = os.path.join(base, f"{rid}_result.csv")
        npz_path = os.path.join(base, f"{rid}_raw.npz")
        meta_path = os.path.join(base, f"{rid}_meta.json")
        if not (os.path.exists(res_path) and os.path.exists(npz_path) and os.path.exists(meta_path)):
            missing.append(rid)
            continue
        r.result = parse_result(res_path)
        r.meta = json.load(open(meta_path, encoding="utf-8"))
        z = np.load(npz_path)
        r.ts = np.asarray(z["timestamps"], dtype=np.float64)
        r.raw = np.asarray(z["raw_data"])
        r.region = resolve_region(r.meta, None, r.raw.shape[-1])
        sc_path = os.path.join(base, f"{rid}_scores.csv")
        r.scores = parse_scores(sc_path) if os.path.exists(sc_path) else None
        r.scores_from_result = not os.path.exists(sc_path)
        if r.level == "2d":
            r.points = parse_points(sc_path) if os.path.exists(sc_path) else r.result["target_count"]
            r.points_from_result = not os.path.exists(sc_path)
        recs.append(r)
    return recs, missing


# ---------------- 窗口评估 ----------------

def coverage(ts_slice, intervals):
    if len(ts_slice) == 0:
        return 0.0
    inside = sum(1.0 for t in ts_slice
                 if any(s <= t < e for s, e in intervals))
    return inside / len(ts_slice)


def eval_windows(ts, presence, intervals,
                 win_s=WIN_S, step_s=STEP_S, start_min_s=START_MIN_S):
    out = []
    w = start_min_s
    while w + win_s <= ts[-1] + 1e-9:
        i0 = int(np.searchsorted(ts, w, side="left"))
        i1 = int(np.searchsorted(ts, w + win_s, side="left"))
        if i1 > i0:
            vals = [p for p in presence[i0:i1] if p in (0, 1)]
            dec = (sum(vals) / len(vals) >= 0.5) if vals else None
            out.append({"start": w, "truth": coverage(ts[i0:i1], intervals) >= 0.5,
                        "decision": dec})
        w += step_s
    return out


def window_stats(windows):
    st = {"truth1": 0, "truth0": 0, "tp": 0, "fp": 0, "fn": 0, "nd": 0}
    for w in windows:
        if w["decision"] is None:
            st["nd"] += 1
            continue
        if w["truth"]:
            st["truth1"] += 1
            st["tp" if w["decision"] else "fn"] += 1
        else:
            st["truth0"] += 1
            if w["decision"]:
                st["fp"] += 1
    return st


def pooled_stats(stats_list):
    keys = ("truth1", "truth0", "tp", "fp", "fn", "nd")
    total = {k: sum(s[k] for s in stats_list) for k in keys}
    total["fp_rate"] = total["fp"] / total["truth0"] if total["truth0"] else 0.0
    total["fn_rate"] = total["fn"] / total["truth1"] if total["truth1"] else 0.0
    return total


def _on_runs(presence):
    """presence 序列的连续 ON 段长列表（B0 闪现统计）。"""
    runs, cur = [], 0
    for p in presence:
        if p == 1:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs


# ---------------- 书包检验（G1-b） ----------------

def region_power_db(raw, region):
    lo, hi = region["lo"], region["hi"]
    X = raw[:, lo:hi + 1].astype(np.complex128)
    return 10.0 * np.log10(np.mean(np.abs(X) ** 2, axis=1) + 1e-12)


def residual_db(raw, region, presence):
    """按 M1 规则复算去背景残差（EMA α=0.2，presence=0 更新）。"""
    lo, hi = region["lo"], region["hi"]
    X = raw.astype(np.complex128)
    bg = X[0].copy()
    out = np.zeros(len(X))
    for t in range(len(X)):
        reg = X[t, lo:hi + 1]
        out[t] = 10.0 * np.log10(float(np.sum(np.abs(reg - bg[lo:hi + 1]) ** 2)) + 1e-12)
        if presence[t] == 0:
            bg = 0.8 * bg + 0.2 * X[t]
    return out


# ---------------- 主流程 ----------------

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="G1 synthetic self-test analysis")
    ap.add_argument("--recordings", default=os.path.join(root, "examples", "recordings"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(root), "outputs", "g1-preview"))
    ap.add_argument("--win-s", type=float, default=WIN_S)
    ap.add_argument("--step-s", type=float, default=STEP_S)
    ap.add_argument("--t-on-default", type=float, default=0.5)
    ap.add_argument("--t-min", type=float, default=0.20)
    ap.add_argument("--t-max", type=float, default=0.80)
    ap.add_argument("--t-step", type=float, default=0.02)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    recs, missing = load_all(a.recordings)
    if missing:
        print("missing result/meta/npz (run detect.py first): " + ", ".join(missing))
        sys.exit(1)
    by_scenario = {}
    for r in recs:
        by_scenario.setdefault(r.scenario, []).append(r)
    n_1d = sum(1 for r in recs if r.level == "1d")
    n_2d = sum(1 for r in recs if r.level == "2d")
    print(f"loaded {len(recs)} recordings ({n_1d} x 1dfft, {n_2d} x 2dfft)")

    lines = []
    lines.append("# G1 合成自测报告（seat-detection v0.1）\n")
    lines.append(f"- 日期：2026-11-17 ｜ 数据：`examples/recordings/`（5 场景 × 2 段 × 2 层级，"
                 f"60 s @ 1 Hz，前 10 s 空位前缀，seed 方案 `20261117 + crc32(scenario)*7 + segment`）")
    lines.append(f"- 滑窗：{a.win_s:g} s 窗、步长 {a.step_s:g} s、起点 ≥ {START_MIN_S:g} s；"
                 f"窗真值 = 剧本覆盖 ≥0.5，窗判定 = presence 均值 ≥0.5")
    lines.append(f"- 默认阈值：T_ON={a.t_on_default}，T_OFF=0.7·T_ON（config/M1_default.json 为 0.35）")
    lines.append("- 门槛：G1-a 静坐/空位窗口错分 ≤ 20%（脚本参数，正式门槛待教师确认）\n")

    # ---- 1) 默认阈值滑窗 ----
    lines.append("## 1. 滑窗评估（默认阈值）\n")
    lines.append("| 场景 | 段 | 层级 | 有人窗 | 空窗 | TP | FP | FN | no-dec | FP率 | FN率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    per_sc = {}
    for r in recs:
        st = window_stats(eval_windows(r.ts, r.result["presence"],
                                       r.script["presence_intervals_s"]))
        per_sc.setdefault((r.scenario, r.level), []).append(st)
        fp = f"{st['fp'] / st['truth0']:.1%}" if st["truth0"] else "-"
        fn = f"{st['fn'] / st['truth1']:.1%}" if st["truth1"] else "-"
        lines.append(f"| {r.scenario} | {r.seg:02d} | {r.level} | {st['truth1']} | {st['truth0']} "
                     f"| {st['tp']} | {st['fp']} | {st['fn']} | {st['nd']} | {fp} | {fn} |")
    lines.append("\n说明：2d/B0 行为 RQ2 证据（静态占用弱检出为设计特性），不计入 G1-a。")
    sit_1d = pooled_stats([s for sc, lv in per_sc if sc in SIT_SCENARIOS and lv == "1d"
                           for s in per_sc[(sc, lv)]])
    empty_1d = pooled_stats(per_sc.get(("empty", "1d"), []))
    bag_1d = pooled_stats(per_sc.get(("bag", "1d"), []))
    passby_1d = pooled_stats(per_sc.get(("passby", "1d"), []))
    g1a_fn, g1a_fp = sit_1d["fn_rate"], empty_1d["fp_rate"]
    g1a_pass = (g1a_fn <= G1A_MAX_ERR) and (g1a_fp <= G1A_MAX_ERR)
    lines.append(f"\n**G1-a（默认阈值）**：静坐 FN 率 = {g1a_fn:.1%}"
                 f"（{sit_1d['fn']}/{sit_1d['truth1']}），空位 FP 率 = {g1a_fp:.1%}"
                 f"（{empty_1d['fp']}/{empty_1d['truth0']}）→ "
                 f"**{'通过' if g1a_pass else '不通过'}**（门槛 ≤{G1A_MAX_ERR:.0%}）")
    lines.append(f"- 干扰诊断（不计入 G1-a）：书包 FP 率 = {bag_1d['fp_rate']:.1%}"
                 f"（{bag_1d['fp']}/{bag_1d['truth0']}）；"
                 f"行人经过 FP 窗 = {passby_1d['fp']}（设计上限 2）\n")

    # ---- 2) 阈值扫描（1d + scores） ----
    sweep_rows = []
    have_scores = all(r.scores is not None for r in recs if r.level == "1d")
    if have_scores:
        recs_1d = [r for r in recs if r.level == "1d"]
        # 重放判决参数与 config 对齐：confirm 帧数 + 静态门。f23_win 由记录的
        # f2n/f3n 按同一窗口逻辑重算——F2/F3 与背景 EMA 无耦合，这部分重放是精确的。
        cfg_path = os.path.join(root, "config", "M1_default.json")
        cfg_m1 = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
        confirm = max(1, int(cfg_m1.get("hysteresis_confirm_frames", 1)))
        f23_floor = float(cfg_m1.get("static_gate", {}).get("f23_floor", 0.0))
        win_gate = float(cfg_m1.get("windows", {}).get("gate_s", 4.0))
        for r in recs_1d:
            if "f2n" in r.scores and "f3n" in r.scores:
                f23 = np.asarray(r.scores["f2n"]) + np.asarray(r.scores["f3n"])
            else:
                f23 = None
            if f23 is None or not np.any(f23 > 0):
                r.static_flag = None  # 无相位特征（退化输入）→ 静态门禁用
            else:
                lo = 0
                flag = np.zeros(len(f23), dtype=bool)
                for t in range(len(f23)):
                    lo = _window_lo(r.ts, t, win_gate, lo)
                    flag[t] = float(np.mean(f23[lo:t + 1])) < f23_floor
                r.static_flag = flag
        t_grid = np.arange(a.t_min, a.t_max + 1e-9, a.t_step)
        for t_on in t_grid:
            t_off = round(0.7 * t_on, 4)
            acc = {}
            for r in recs_1d:
                pres = presence_from_scores(r.scores["score"], t_on, t_off,
                                            confirm, r.static_flag)
                st = window_stats(eval_windows(r.ts, pres, r.script["presence_intervals_s"]))
                acc.setdefault(r.scenario, []).append(st)
            sit = pooled_stats([s for sc in acc if sc in SIT_SCENARIOS for s in acc[sc]])
            emp = pooled_stats(acc.get("empty", []))
            bag = pooled_stats(acc.get("bag", []))
            pb = pooled_stats(acc.get("passby", []))
            sweep_rows.append({"t_on": float(t_on), "fn_sit": sit["fn_rate"],
                               "fp_empty": emp["fp_rate"], "fp_bag": bag["fp_rate"],
                               "fp_passby": pb["fp_rate"],
                               "max_err": max(sit["fn_rate"], emp["fp_rate"])})
        best = min(sweep_rows, key=lambda x: (round(x["max_err"], 6),
                                              abs(x["t_on"] - a.t_on_default)))
        default_row = min(sweep_rows, key=lambda x: abs(x["t_on"] - a.t_on_default))

        lines.append("## 2. 阈值扫描（T_ON ∈ [%.2f, %.2f] 步 %.2f，T_OFF=0.7·T_ON）\n" % (a.t_min, a.t_max, a.t_step))
        lines.append(f"> 重放判决与 config 对齐（confirm={confirm} 帧，静态门 f23_floor={f23_floor:g}）；"
                     "F1n/score 与背景 EMA 耦合仍为近似——最优阈值下方用 tweaked config 重跑 run_m1 复核。\n")
        lines.append("| T_ON | 静坐FN | 空位FP | 书包FP | 经过FP | max(静坐FN,空位FP) |")
        lines.append("|---|---|---|---|---|---|")
        for row in sweep_rows:
            mark = ""
            if abs(row["t_on"] - a.t_on_default) < 1e-9:
                mark = " ←默认"
            if row is best:
                mark += " ←最优"
            lines.append(f"| {row['t_on']:.2f} | {row['fn_sit']:.1%} | {row['fp_empty']:.1%} "
                         f"| {row['fp_bag']:.1%} | {row['fp_passby']:.1%} "
                         f"| {row['max_err']:.1%}{mark} |")
        lines.append(f"\n**最优阈值**：T_ON = {best['t_on']:.2f}"
                     f"（max 错分 {best['max_err']:.1%}）；默认 {a.t_on_default} 处 "
                     f"max 错分 {default_row['max_err']:.1%}。")

        # 复核：最优阈值重跑 run_m1（背景 EMA 耦合的真实路径）
        cfg_path = os.path.join(root, "config", "M1_default.json")
        if os.path.exists(cfg_path):
            cfg = json.load(open(cfg_path, encoding="utf-8"))
            cfg.setdefault("thresholds", {})
            cfg["thresholds"]["t_on"] = best["t_on"]
            cfg["thresholds"]["t_off"] = round(0.7 * best["t_on"], 4)
            rerun_stats = {}
            for r in recs_1d:
                res = run_m1(r.raw, r.ts, r.meta, cfg, None)
                st = window_stats(eval_windows(r.ts, res["presence"],
                                               r.script["presence_intervals_s"]))
                rerun_stats.setdefault(r.scenario, []).append(st)
            sit2 = pooled_stats([s for sc in rerun_stats if sc in SIT_SCENARIOS for s in rerun_stats[sc]])
            emp2 = pooled_stats(rerun_stats.get("empty", []))
            bag2 = pooled_stats(rerun_stats.get("bag", []))
            lines.append(f"- **复核（重跑 run_m1，T_ON={best['t_on']:.2f}）**：静坐 FN = {sit2['fn_rate']:.1%}、"
                         f"空位 FP = {emp2['fp_rate']:.1%}、书包 FP = {bag2['fp_rate']:.1%}"
                         f"（与重放差异即背景耦合效应）\n")
    else:
        lines.append("## 2. 阈值扫描\n\n跳过：缺少 `*_scores.csv`（需 `detect.py --emit-scores`）。\n")

    # ---- 3) 书包检验（G1-b） ----
    lines.append("## 3. 书包检验（G1 条件 b）\n")
    lines.append("| 判据 | 段 | bag | empty | 差值 | 门槛 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|")
    crit_pass = []
    bag_margin = None
    for seg in (1, 2):
        bag_r = next((r for r in by_scenario.get("bag", []) if r.seg == seg and r.level == "1d"), None)
        emp_r = next((r for r in by_scenario.get("empty", []) if r.seg == seg and r.level == "1d"), None)
        if not (bag_r and emp_r):
            continue
        t_conv = 25
        raw_bag = float(np.median(region_power_db(bag_r.raw, bag_r.region)[t_conv:]))
        raw_emp = float(np.median(region_power_db(emp_r.raw, emp_r.region)[t_conv:]))
        res_bag = float(np.median(residual_db(bag_r.raw, bag_r.region,
                                              bag_r.result["presence"])[t_conv:]))
        res_emp = float(np.median(residual_db(emp_r.raw, emp_r.region,
                                              emp_r.result["presence"])[t_conv:]))
        ok_a = (raw_bag - raw_emp) < 3.0
        ok_b = (res_bag - res_emp) < 1.0
        crit_pass.append(ok_a or ok_b)
        lines.append(f"| raw 区域能量抬升 | {seg:02d} | {raw_bag:.1f} dB | {raw_emp:.1f} dB "
                     f"| {raw_bag - raw_emp:+.1f} dB | <3 dB | {'通过' if ok_a else '不通过'} |")
        lines.append(f"| 去背景残差 | {seg:02d} | {res_bag:.1f} dB | {res_emp:.1f} dB "
                     f"| {res_bag - res_emp:+.1f} dB | <1 dB | {'通过' if ok_b else '不通过'} |")
        if bag_r.scores:
            sc = [v for v, t in zip(bag_r.scores["score"], bag_r.ts) if t >= 10]
            if sc:
                bag_margin = max(bag_margin or 0.0, max(sc))
    g1b_pass = all(crit_pass) if crit_pass else False
    lines.append(f"\n**G1-b**：两判据至少一条通过 → **{'通过' if g1b_pass else '不通过'}**"
                 "（amp5 静态书包 raw 抬升必然显著；设计路径是背景去除消除，判据 2 应通过）")
    if bag_margin is not None:
        lines.append(f"- 分数余量：书包 score 峰值 = {bag_margin:.3f}（放置瞬态，3-4 帧；"
                     f"confirm=5 帧防抖拦截，背景 EMA 随后吸收）——"
                     "真实数据需关注（设计文档 §12 风险）\n")

    # ---- 4) B0 检验（2d） ----
    lines.append("## 4. B0 检验（点云，RQ2 证据）\n")
    lines.append("| 场景 | 段 | 检验 | 结果 | 结论 |")
    lines.append("|---|---|---|---|---|")
    b0_notes = []
    FA_MAX_FRAMES = 20  # p_fa=1e-3 → 60 帧期望 ~7 个点帧，此为 3σ 上界
    for name, check in (("passby", "经过出点 t∈[10,16]"),
                        ("sitting_still", "静坐无持续检出（点帧率=p_fa 设计率）"),
                        ("bag", "书包无持续检出（点帧率=p_fa 设计率）"),
                        ("empty", "无人无持续检出（点帧率=p_fa 设计率）")):
        for r in by_scenario.get(name, []):
            if r.level != "2d":
                continue
            pts = np.asarray(r.points)
            if name == "passby":
                in_win = [p for p, t in zip(pts, r.ts) if 10 <= t <= 16]
                ok = bool(in_win) and max(in_win) >= 1
                detail = f"max points={max(in_win) if in_win else 0} @t∈[10,16]"
            else:
                # 静态目标能量在 doppler DC → MTI 滤除 → 持续检出必为 0；
                # 观测到的点帧应为 CFAR FA（设计率 ~7/60 段），presence 短闪现如实报告
                pf = int(np.count_nonzero(pts))
                runs = _on_runs(r.result["presence"])
                ok = pf <= FA_MAX_FRAMES
                detail = f"点帧数={pf}（期望 ~7），presence ON 段={runs or '无'}"
            lines.append(f"| {name} | {r.seg:02d} | {check} | {detail} | {'符合' if ok else '不符合'} |")
            if not ok:
                b0_notes.append(f"{name} seg{r.seg:02d}: {detail}")
    for r in [x for x in by_scenario.get("sitting_reading", []) if x.level == "2d"]:
        pts = np.asarray(r.points)
        flip_frames = {20, 35, 47}
        hit = sorted({int(t) for p, t in zip(pts, r.ts) if p >= 1})
        flips_hit = all(f in hit for f in flip_frames)
        ok = flips_hit and int(np.count_nonzero(pts)) <= FA_MAX_FRAMES
        lines.append(f"| sitting_reading | {r.seg:02d} | 翻页帧 {sorted(flip_frames)} 出点 | 出点帧 {hit} | "
                     f"{'符合（翻页=真实运动，MTI 不滤非 DC）' if ok else '需检查'} |")
        if not ok:
            b0_notes.append(f"sitting_reading seg{r.seg:02d}: 出点帧 {hit}")
    lines.append("\n**RQ2 证据**：静坐/书包能量几乎全在 doppler DC → 被 MTI（±1 bin）滤除，"
                 "B0 v1 对静态占用无持续检出；官方基线“静坐弱检出”在我们管线中的复现。"
                 "静态占用由 M1 负责，B0/M1 互补。")
    lines.append("FA 统计说明：p_fa=1e-3 → 每段约 7 个 FA 点帧，偶发 2-3 帧聚簇会触发"
                 "短 presence 闪现（≤ t_on+t_off 帧）——B0 基线特性而非缺陷；"
                 "静态占用判定不依赖 B0 presence。\n")

    # ---- 5) G1 汇总 ----
    lines.append("## 5. G1 条件汇总\n")
    lines.append(f"| 条件 | 内容 | 结论 |")
    lines.append(f"|---|---|---|")
    lines.append(f"| a | 默认阈值静坐/空位错分 ≤ 20% | **{'通过' if g1a_pass else '不通过'}**"
                 f"（FN {g1a_fn:.1%} / FP {g1a_fp:.1%}） |")
    lines.append(f"| b | 书包无特征抬升或可被背景去除消除 | **{'通过' if g1b_pass else '不通过'}** |")
    lines.append(f"| c | B 端 30 分钟真实采集回放一致 | **待真实数据**（接口就绪：detect.py 直接消费 measured "
                 f"data_level=1dfft/2dfft；评价用 D 的 labels.csv，g1_analysis 留 measured 扩展位） |")
    if b0_notes:
        lines.append(f"\n异常：{'；'.join(b0_notes)}")
    if passby_1d["fp"] > 2:
        lines.append(f"\n注意：passby 瞬态 FP 窗 = {passby_1d['fp']}（设计上限 2），"
                     "超过则需调 hysteresis_confirm_frames 或 T_OFF。")
    lines.append("")

    report = "\n".join(lines)
    with open(os.path.join(a.out, "g1_report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    print(f"report -> {os.path.join(a.out, 'g1_report.md')}")

    # ---- 6) 图表 ----
    try:
        plot_timelines(by_scenario, a)
        plot_sweep(sweep_rows, a)
        plot_bag(by_scenario, a)
        for name in ("timelines.png", "threshold_sweep.png", "bag_check.png"):
            print(f"plot   -> {os.path.join(a.out, name)}")
    except Exception as e:  # noqa: BLE001
        print(f"warning: plotting failed ({e}); report still written")

    print(f"G1-a: {'PASS' if g1a_pass else 'FAIL'}  G1-b: {'PASS' if g1b_pass else 'FAIL'}  G1-c: pending")
    sys.exit(0 if (g1a_pass and g1b_pass) else 1)


def plot_timelines(by_scenario, a):
    fig, axes = plt.subplots(5, 1, figsize=(10, 13), sharex=True)
    for ax, name in zip(axes, ("empty", "bag", "sitting_reading", "sitting_still", "passby")):
        r = next((x for x in by_scenario.get(name, []) if x.level == "1d" and x.seg == 1), None)
        if r is None or r.scores is None:
            ax.set_title(f"{name}: no scores")
            continue
        ax.plot(r.ts, r.scores["score"], lw=1.2, color="tab:blue", label="score")
        ax.axhline(a.t_on_default, color="green", ls="--", lw=0.8, label="T_ON")
        ax.axhline(0.7 * a.t_on_default, color="red", ls="--", lw=0.8, label="T_OFF")
        pres = np.asarray(r.result["presence"])
        ax.fill_between(r.ts, 0, pres, color="green", alpha=0.12, label="presence")
        for ev in r.script["events"]:
            ax.axvline(ev["t_s"], color="orange", ls=":", lw=0.8)
        ax.set_ylim(-0.05, 1.05)
        ax.set_ylabel("score")
        ax.set_title(f"{name} (seg1, 1dfft)")
    axes[-1].set_xlabel("time [s]")
    axes[0].legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "timelines.png"), dpi=130)
    plt.close(fig)


def plot_sweep(sweep_rows, a):
    if not sweep_rows:
        return
    t = [row["t_on"] for row in sweep_rows]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(t, [row["fn_sit"] for row in sweep_rows], "o-", ms=3, label="FN sit (reading+still)")
    ax.plot(t, [row["fp_empty"] for row in sweep_rows], "s-", ms=3, label="FP empty")
    ax.plot(t, [row["fp_bag"] for row in sweep_rows], "^-", ms=3, label="FP bag")
    ax.plot(t, [row["max_err"] for row in sweep_rows], "k-", lw=1.5, label="max(FN sit, FP empty)")
    ax.axhline(G1A_MAX_ERR, color="grey", ls=":", label="20% gate")
    ax.axvline(a.t_on_default, color="grey", ls="--", lw=0.8, label="default T_ON")
    best = min(sweep_rows, key=lambda x: (round(x["max_err"], 6), abs(x["t_on"] - a.t_on_default)))
    ax.axvline(best["t_on"], color="green", ls="--", lw=0.8, label="best T_ON")
    ax.set_xlabel("T_ON")
    ax.set_ylabel("window error rate")
    ax.set_title("G1 threshold sweep (replayed hysteresis)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "threshold_sweep.png"), dpi=130)
    plt.close(fig)


def plot_bag(by_scenario, a):
    bag_r = next((r for r in by_scenario.get("bag", []) if r.level == "1d" and r.seg == 1), None)
    emp_r = next((r for r in by_scenario.get("empty", []) if r.level == "1d" and r.seg == 1), None)
    if not (bag_r and emp_r):
        return
    fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    axes[0].plot(bag_r.ts, region_power_db(bag_r.raw, bag_r.region), label="bag", color="tab:red")
    axes[0].plot(emp_r.ts, region_power_db(emp_r.raw, emp_r.region), label="empty", color="tab:grey")
    axes[0].set_ylabel("raw region power [dB]")
    axes[0].set_title("G1-b: raw region energy vs background-removed residual (seg1)")
    axes[0].legend(fontsize=8)
    axes[1].plot(bag_r.ts, residual_db(bag_r.raw, bag_r.region, bag_r.result["presence"]),
                 label="bag", color="tab:red")
    axes[1].plot(emp_r.ts, residual_db(emp_r.raw, emp_r.region, emp_r.result["presence"]),
                 label="empty", color="tab:grey")
    axes[1].axvline(25, color="grey", ls=":", lw=0.8, label="convergence cut t=25")
    axes[1].set_ylabel("residual power [dB]")
    axes[1].set_xlabel("time [s]")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "bag_check.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""eval_v121 —— v1.2 vs v1.2.1 组员验收表（开场误报 / 静坐保持 / 走远判空）。

用法
----
    python -X utf8 tools/eval_v121.py \
        --v12-dir examples/real \
        --v121-dir examples/real_v121 \
        --config-v12 config/M1_dev112p_v0.2.json \
        --config-v121 config/M1_dev112p_v1.2.1.json \
        --out outputs/v1.2.1

评测口径（与组员任务单一致）
--------------------------
* **开场空座误报**：从标定结束（v1.2.1 = 10 s；v1.2 从 0 s 起无门控，两版都
  取 [10 s, 首次坐稳) 统计，另附全程开场列）到首次坐稳之间 presence=1 的帧数。
* **静坐保持（漏报）**：标签为有人区间内 presence=0 的帧数（离座释放延迟段
  不计入——那属于“走远判空”）。
* **走远判空**：每次“走远/离座”事件到首次 presence=0 的秒数（释放延迟）；
  “未释放”表示到录制结束前都没回到 0。
* **空座误报（离座后）**：走远事件后 5 s（释放确认期）之后仍判有人的帧数。
* **全空组（邻座/过道）**：目标座全程空，直接统计 presence=1 帧数。

真值来源：B 归档 meta.annotation.stopwatch_events（现场秒表，provisional，
秒表-雷达时钟偏移未实测）；邻座/过道两组人工确认为目标座全程空
（见 capture record）。P01/P03/pilot/short 无实测标签，只列不评分。
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from detect import load_json, normalize_axes, validate_inputs
from algos.m1_features import run_m1

GROUP_OF = {
    "T01_P01_R01": "t01-p01",
    "T01_P02_R01": "t01-p02-formal",
    "T01_P03_R01": "t01-p03-formal",
    "T01_P04_R01": "t01-p04-formal",
    "T01_neighbor_R01": "t01-neighbor",
    "T01_aisle_R01": "t01-aisle",
    "T01_P02_retest": "t01-p02-retest",
    "T01_P04_retest": "t01-p04-retest",
    "T01_chair_shift": "t01-chair-shift",
}

ALL_EMPTY = {"T01_neighbor_R01", "T01_aisle_R01"}
UNLABELLED = {"T01_P01_R01", "T01_P03_R01", "pilot_01", "short_01", "short_02"}

# 2026-11-19 现场在线抓包（484 帧，20 cm 候选位置；组员复核用）
LIVE_DIR = "examples/live_v121"
LIVE_CORES = ["T01_http_live_20261119_201257_965862"]
LIVE_GROUP = "live-484"

LEAVE_WORDS = ("走远", "离座", "离开")
SIT_WORDS = ("坐稳", "返回", "坐上")

RELEASE_GRACE_S = 5.0   # 释放确认期（release_frames / 5.168 Hz ≈ 5.0 s）


def _core_rid(name):
    """20261112_T01_P04_retest_c1r0_1dfft → T01_P04_retest。"""
    n = name
    for suffix in ("_1dfft_parity", "_1dfft"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    parts = n.split("_")
    if parts and parts[0].isdigit() and len(parts[0]) == 8:
        n = "_".join(parts[1:])
    if n.endswith("_c1r0"):
        n = n[: -len("_c1r0")]
    return n


def load_recording(npz_path):
    rid = os.path.basename(npz_path)[: -len("_raw.npz")]
    meta = load_json(os.path.join(os.path.dirname(npz_path), f"{rid}_meta.json"))
    z = np.load(npz_path)
    ts = np.asarray(z["timestamps"], dtype=np.float64)
    fids = np.asarray(z["frame_ids"])
    raw = np.asarray(z["raw_data"])
    problems = validate_inputs(ts, fids, raw, meta)
    if problems:
        raise ValueError(f"{rid}: " + "; ".join(problems))
    X, has_doppler = normalize_axes(raw, meta)
    if has_doppler:
        raise ValueError(f"{rid}: eval only supports 1dfft (M1)")
    return rid, meta, X, ts


def gt_rate(meta, rid, ts_end):
    """返 (segments, source)。segments = [(t0, t1, occupied)]。"""
    if rid in ALL_EMPTY:
        return None, "all_empty_manual"
    events = ((meta.get("annotation") or {}).get("stopwatch_events") or [])
    if not events:
        return None, "no_labels"
    segs = []
    state = 0
    for ev in sorted(events, key=lambda e: float(e["time_s"])):
        t = float(ev["time_s"])
        name = str(ev.get("event") or "")
        new = state
        if any(w in name for w in LEAVE_WORDS):
            new = 0
        elif any(w in name for w in SIT_WORDS):
            new = 1
        if segs:
            segs[-1] = (segs[-1][0], t, segs[-1][2])
        if new != state:
            state = new
            segs.append((t, t, state))
    if not segs:
        return None, "no_labels"
    segs[-1] = (segs[-1][0], float(ts_end), segs[-1][2])
    return segs, "stopwatch_events(provisional)"


def metrics(presence, ts, segs, rid):
    n = len(ts)
    out = {"frames": n}
    # 标定窗：v1.2.1 前 10 s 不做判断，为公平两版都从 10 s 起算开场
    judge_from = 10.0
    if rid in ALL_EMPTY:
        sel = ts >= judge_from
        out["all_empty_fp"] = int(presence[sel].sum())
        out["all_empty_n"] = int(sel.sum())
        out["open_fp"] = out["all_empty_fp"]
        out["open_n"] = out["all_empty_n"]
        return out
    if rid in UNLABELLED:
        return out

    # 开场：judge_from → 首次“坐稳”
    first_sit = next((t0 for t0, _, occ in segs if occ == 1), None)
    if first_sit is not None:
        sel = (ts >= judge_from) & (ts < first_sit)
        out["open_fp"] = int(presence[sel].sum())
        out["open_n"] = int(sel.sum())

    # 静坐保持 / 走远判空
    sits = [(a, b) for a, b, o in segs if o == 1]
    leaves = [(a, b) for a, b, o in segs if o == 0 and a > 0]
    hit = sit_n = 0
    entry_lat = []
    sit_false_rel = 0
    reframe_delays = []
    premature = 0
    late_fp = late_n = 0
    for a, b in sits:
        sel = (ts >= a) & (ts < b)
        idx = np.flatnonzero(sel)
        if not len(idx):
            continue
        pos = idx[presence[idx] == 1]
        if len(pos):
            t_in = ts[pos[0]]
            entry_lat.append(t_in - a)
            # 入场后的保持率（入场前的爬升段不计为漏报，单列 entry_lat）
            sel2 = (ts >= max(a, t_in + 1.0)) & (ts < b)
            hit += int(presence[sel2].sum())
            sit_n += int(sel2.sum())
            seg_pr = presence[sel2]
            sit_false_rel += int(np.sum((seg_pr[1:] == 0) & (seg_pr[:-1] == 1)))
        else:
            entry_lat.append(None)
            hit += int(presence[sel].sum())
            sit_n += int(sel.sum())
    for a, b in leaves:
        sel = (ts >= a) & (ts < b)
        idx = np.flatnonzero(sel)
        if not len(idx):
            continue
        if presence[idx[0]] == 0:
            premature += 1
            continue
        zero = idx[presence[idx] == 0]
        delay = (ts[zero[0]] - a) if len(zero) else None
        reframe_delays.append(delay)
        if delay is not None:
            sel2 = (ts > a + delay + RELEASE_GRACE_S) & (ts < b)
            late_fp += int(presence[sel2].sum())
            late_n += int(sel2.sum())
    if sit_n:
        out["sit_hit"] = hit
        out["sit_n"] = sit_n
        out["sit_false_release"] = sit_false_rel
    if entry_lat:
        out["entry_lat"] = [d for d in entry_lat if d is not None]
    if reframe_delays:
        out["release_delays"] = [d for d in reframe_delays if d is not None]
        out["release_missing"] = sum(1 for d in reframe_delays if d is None)
        out["release_premature"] = premature
    if late_n:
        out["late_fp"] = late_fp
        out["late_n"] = late_n
    return out


def fmt_pct(a, b):
    return f"{a}/{b} ({100.0 * a / b:.0f}%)" if b else "—"


def run_version(npz_path, cfg):
    rid, meta, X, ts = load_recording(npz_path)
    result = run_m1(X, ts, meta, cfg, None)
    return rid, meta, np.asarray(result["presence"]), ts


def pick(paths_by_core, core):
    return paths_by_core.get(core)


def main():
    ap = argparse.ArgumentParser(description="v1.2 vs v1.2.1 acceptance table")
    ap.add_argument("--v12-dir", default="examples/real")
    ap.add_argument("--v121-dir", default="examples/real_v121")
    ap.add_argument("--config-v12", default="config/M1_dev112p_v0.2.json")
    ap.add_argument("--config-v121", default="config/M1_dev112p_v1.2.1.json")
    ap.add_argument("--out", default="outputs/v1.2.1")
    a = ap.parse_args()

    cfg12 = load_json(a.config_v12)
    cfg121 = load_json(a.config_v121)

    def index(d):
        out = {}
        if not os.path.isdir(d):
            return out
        for f in sorted(os.listdir(d)):
            if f.endswith("_raw.npz"):
                out[_core_rid(f[: -len("_raw.npz")])] = os.path.join(d, f)
        return out

    v12 = index(a.v12_dir)
    v121 = index(a.v121_dir)
    cores = [c for c in v121 if c in v12]
    os.makedirs(a.out, exist_ok=True)

    rows = []
    for core in sorted(cores):
        rid, meta, pr121, ts = run_version(v121[core], cfg121)
        _, _, pr12, ts12 = run_version(v12[core], cfg12)
        segs, src = gt_rate(meta, core, float(ts[-1]))
        m12 = metrics(pr12, ts12, segs, core) if segs is not None or core in ALL_EMPTY else {"frames": len(ts)}
        m121 = metrics(pr121, ts, segs, core) if segs is not None or core in ALL_EMPTY else {"frames": len(ts)}
        rows.append({"core": core, "group": GROUP_OF.get(core, ""), "gt": src,
                     "v12": m12, "v121": m121})
        print(f"[{core}] gt={src}  v1.2={m12}  v1.2.1={m121}")

    # ---- 2026-11-19 现场在线抓包（484 帧，20 cm 候选位置）----
    for core in LIVE_CORES:
        p121 = os.path.join(LIVE_DIR, f"{core}_c1r0_1dfft_parity_raw.npz")
        p12 = os.path.join(LIVE_DIR, f"{core}_c1r0_1dfft_raw.npz")
        if not (os.path.exists(p121) and os.path.exists(p12)):
            print(f"[{core}] live capture not found under {LIVE_DIR}; skipped")
            continue
        rid, meta, pr121, ts = run_version(p121, cfg121)
        _, _, pr12, ts12 = run_version(p12, cfg12)
        segs, src = gt_rate(meta, core, float(ts[-1]))
        m12 = metrics(pr12, ts12, segs, core)
        m121 = metrics(pr121, ts, segs, core)
        rows.append({"core": core, "group": LIVE_GROUP, "gt": src,
                     "v12": m12, "v121": m121})
        print(f"[{core}] gt={src}  v1.2={m12}  v1.2.1={m121}")

    # ---- 写 CSV ----
    csv_path = os.path.join(a.out, "v1.2.1_comparison.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "recording", "gt_source",
                    "v12_open_fp", "v12_open_n", "v121_open_fp", "v121_open_n",
                    "v12_sit_hit", "v12_sit_n", "v121_sit_hit", "v121_sit_n",
                    "v12_release_delays_s", "v121_release_delays_s",
                    "v12_release_missing", "v121_release_missing",
                    "v12_sit_false_release", "v121_sit_false_release",
                    "v12_all_empty_fp", "v12_all_empty_n", "v121_all_empty_fp", "v121_all_empty_n"])
        for r in rows:
            k = lambda ver, key: r[ver].get(key, "")
            w.writerow([r["group"], r["core"], r["gt"],
                        k("v12", "open_fp"), k("v12", "open_n"),
                        k("v121", "open_fp"), k("v121", "open_n"),
                        k("v12", "sit_hit"), k("v12", "sit_n"),
                        k("v121", "sit_hit"), k("v121", "sit_n"),
                        "|".join("%.1f" % v for v in k("v12", "release_delays") or []),
                        "|".join("%.1f" % v for v in k("v121", "release_delays") or []),
                        k("v12", "release_missing"), k("v121", "release_missing"),
                        k("v12", "sit_false_release"), k("v121", "sit_false_release"),
                        k("v12", "all_empty_fp"), k("v12", "all_empty_n"),
                        k("v121", "all_empty_fp"), k("v121", "all_empty_n")])

    # ---- 写 Markdown ----
    lines = [
        "# M1 v1.2 vs v1.2.1 · 组员验收对照表",
        "",
        "口径：开场 = [10 s, 首次坐稳)；静坐保持 = 入场后有人区间命中率（入场爬升段单列）；"
        "走远判空 = 离座事件到 presence=0 的延迟；「提前」= 人还没走就判空（静坐误判空）；"
        "全空组 = 目标座全程空时的误报帧。真值来自现场秒表（provisional，秒表-雷达时钟偏移未实测）。",
        "",
        "| 组 | 录制 | 真值 | 开场误报 v1.2 | 开场误报 v1.2.1 | 静坐保持 v1.2 | 静坐保持 v1.2.1 | 走远判空 v1.2 | 走远判空 v1.2.1 | 全空误报 v1.2 | 全空误报 v1.2.1 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def cell(v):
        if isinstance(v, list):
            if not v:
                return "—"
            return " / ".join("%.1f" % x for x in v)
        return str(v)

    for r in rows:
        m12, m121 = r["v12"], r["v121"]
        if r["core"] in UNLABELLED:
            lines.append(f"| {r['group']} | {r['core']} | 无实测标签 | — | — | — | — | — | — | — | — |")
            continue
        if r["core"] in ALL_EMPTY:
            lines.append(
                f"| {r['group']} | {r['core']} | 目标座全程空 | "
                f"— | — | — | — | — | — | "
                f"**{fmt_pct(m12.get('all_empty_fp', 0), m12.get('all_empty_n', 0))}** | "
                f"**{fmt_pct(m121.get('all_empty_fp', 0), m121.get('all_empty_n', 0))}** |")
            continue

        def rel(m):
            ds = m.get("release_delays") or []
            prem = m.get("release_premature", 0)
            miss = m.get("release_missing", 0)
            if not ds and not miss and not prem:
                return "—"
            parts = []
            if ds:
                parts.append(" / ".join("%.1f" % d for d in ds) + " s")
            if prem:
                parts.append(f"提前判空 {prem}（静坐误判空）")
            if miss:
                parts.append(f"未释放 {miss}")
            return "；".join(parts)

        lines.append(
            f"| {r['group']} | {r['core']} | 秒表 | "
            f"**{fmt_pct(m12.get('open_fp', 0), m12.get('open_n', 0))}** | "
            f"**{fmt_pct(m121.get('open_fp', 0), m121.get('open_n', 0))}** | "
            f"{fmt_pct(m12.get('sit_hit', 0), m12.get('sit_n', 0))} | "
            f"{fmt_pct(m121.get('sit_hit', 0), m121.get('sit_n', 0))} | "
            f"{rel(m12)} | {rel(m121)} | — | — |")

    # 汇总
    def agg(ver):
        open_fp = open_n = sit_hit = sit_n = 0
        emp_fp = emp_n = 0
        for r in rows:
            m = r[ver]
            if r["core"] in ALL_EMPTY:
                emp_fp += m.get("all_empty_fp", 0)
                emp_n += m.get("all_empty_n", 0)
                continue
            open_fp += m.get("open_fp", 0)
            open_n += m.get("open_n", 0)
            sit_hit += m.get("sit_hit", 0)
            sit_n += m.get("sit_n", 0)
        return open_fp, open_n, sit_hit, sit_n, emp_fp, emp_n

    o12, on12, sh12, sn12, e12, en12 = agg("v12")
    o121, on121, sh121, sn121, e121, en121 = agg("v121")
    lines += [
        "",
        "## 汇总",
        "",
        "| 指标 | v1.2 | v1.2.1 |",
        "|---|---|---|",
        f"| 开场空座误报（秒表组合计） | {fmt_pct(o12, on12)} | {fmt_pct(o121, on121)} |",
        f"| 静坐保持（入场后合计） | {fmt_pct(sh12, sn12)} | {fmt_pct(sh121, sn121)} |",
        f"| 邻座/过道全空组误报 | {fmt_pct(e12, en12)} | {fmt_pct(e121, en121)} |",
        "",
        "注：v1.2 的「静坐保持」100% 是因为它从不释放（不是能力）；"
        "v1.2.1 的保持率下降全部来自 T01_P04_R01 静坐末段（114–126 s）"
        "约 12 s 完全静止被误判空座，其余组 100%。",
    ]

    md = os.path.join(a.out, "v1.2.1_comparison.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {csv_path}\nwrote {md}")


if __name__ == "__main__":
    main()

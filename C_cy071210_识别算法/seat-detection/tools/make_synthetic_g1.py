# -*- coding: utf-8 -*-
"""G1 合成自测数据生成器（5 场景 × 2 段 × 2 层级）。
额外：--lifecycle 仅生成 S06 lifecycle 场景（入座→离座→回座，用于 v1.2
snapshot_freeze 释放/重建/回座路径自测；不进 G1 20 条基线）。

场景规格（docs/seat-detection-design-v0.1.md §6）：
  S01 empty / S02 bag(interfere) / S03 sitting_reading(sit) / S04 sitting_still(sit) / S05 passby(walk)
  60 s @ 1 Hz，64 range bins，区域 14-22、主 bin 17；统一前 10 s 空位前缀；
  底噪 σ=0.08/分量 + 静态杂波 0.5 @ bin 17（demo 同源结构）。
  1dfft: [N,64] complex64 → M1；2dfft: [N,16,64] complex64（doppler DC=bin 0）→ B0。
  seed = 20261117 + crc32(scenario)·7 + segment（同段两层级共用）。

输出（--out，默认 seat-detection/examples/recordings/）：
  <rid>_raw.npz / <rid>_meta.json / <rid>_scenario_script.json
  rid = 20261117_P00_{slot}_{token}_{seg:02d}_{1d|2d}

scenario_script.json 是仿真剧本真值（presence 区间左闭右开 + 事件时刻），
命名刻意区别于 D 的 labels.csv——不冒充人工标注；真实数据评价一律以 D 的独立 labels.csv 为准。
"""
import argparse
import json
import os
import sys
import zlib

import numpy as np

N = 60                 # 60 s @ 1 Hz
N_BINS = 64
N_DOP = 16
MAIN = 17              # 合成安装主 bin（区域 14-22）
CLUTTER_AMP = 0.5      # 静态背景杂波（bin 17，demo 同源结构）
NOISE_SIGMA = 0.08     # 每实/虚部分量底噪
PREFIX_S = 10          # 统一空位前缀
BREATH_HZ = 0.25
PAGE_FLIP_T = (20, 35, 47)
PASSBY_T = (10, 16)    # 闭区间帧
LIFECYCLE_T = (10, 28, 46)  # lifecycle：入座/离座/回座（秒，区间左闭右开）
SEED_BASE = 20261117
DATE = "20261117"

SCENARIOS = [
    ("S01", "empty", "empty"),
    ("S02", "bag", "interfere"),
    ("S03", "sitting_reading", "sit"),
    ("S04", "sitting_still", "sit"),
    ("S05", "passby", "walk"),
]

# lifecycle（离座释放回座）自测场景：仅 --lifecycle 显式生成，不进 G1 20 条基线
LIFECYCLE = [("S06", "lifecycle", "lifecycle")]


def frame_components(name, t, rng, phi_b):
    """帧 t 的目标分量列表 (amp, phase, doppler_bin, range_bin)；杂波与噪声不在此列。

    rng 只在翻页帧消耗（调用顺序固定 → 序列可复现）。
    """
    comps = []
    if name == "bag":
        if t >= PREFIX_S:
            comps.append((5.0, phi_b, 0, MAIN))  # 静态书包：无相位调制
    elif name == "sitting_reading":
        if t >= PREFIX_S:
            s = 2.0 * np.pi * BREATH_HZ * (t - PREFIX_S)
            amp = 6.0 * (1.0 + 0.08 * np.sin(s))   # 呼吸 AM
            ph = 1.2 * np.sin(s)                   # 呼吸相位 ±1.2 rad
            if t in PAGE_FLIP_T:
                amp *= 1.3
                ph += rng.uniform(-0.6, 0.6)
                pf = rng.uniform(0.0, 2.0 * np.pi)
                comps.append((3.0, pf, 3, MAIN))              # 翻页运动脉冲 doppler +3
                comps.append((3.0, pf + 0.5, N_DOP - 3, MAIN))  # 及 −3（wrap 到 bin 13）
            comps.append((amp, ph, 0, MAIN))
    elif name == "sitting_still":
        if t >= PREFIX_S:
            s = 2.0 * np.pi * BREATH_HZ * (t - PREFIX_S)
            comps.append((3.0, 0.6 * np.sin(s), 0, MAIN))       # 静坐微动 ±0.6 rad
    elif name == "passby":
        if PASSBY_T[0] <= t <= PASSBY_T[1]:
            b = int(round(5.0 + 35.0 * (t - PASSBY_T[0]) / 6.0))  # 扫 bins 5→40
            comps.append((5.0, 0.7 * (t - PASSBY_T[0]), 5, b))     # doppler bin 5
    elif name == "lifecycle":
        if LIFECYCLE_T[0] <= t < LIFECYCLE_T[1] or LIFECYCLE_T[2] <= t:
            s = 2.0 * np.pi * BREATH_HZ * (t - PREFIX_S)
            comps.append((3.0, 0.6 * np.sin(s), 0, MAIN))       # 静坐微动（同 sitting_still）
    # empty：无目标分量
    return comps


def scenario_script(name, token, seed, level, rid):
    """剧本真值：presence 区间（左闭右开，秒）+ 事件时刻。"""
    if name in ("sitting_reading", "sitting_still"):
        intervals = [[float(PREFIX_S), float(N)]]
        events = [{"t_s": float(PREFIX_S), "type": "enter"}]
        if name == "sitting_reading":
            for ft in PAGE_FLIP_T:
                events.append({"t_s": float(ft), "type": "page_flip"})
    elif name == "bag":
        intervals = []   # 书包不是人：真值为无人
        events = [{"t_s": float(PREFIX_S), "type": "bag_placed"}]
    elif name == "passby":
        intervals = []   # 旁人经过不占座：真值为无人
        events = [{"t_s": float(PASSBY_T[0]), "type": "passby",
                   "until_s": float(PASSBY_T[1])}]
    elif name == "lifecycle":
        intervals = [[float(LIFECYCLE_T[0]), float(LIFECYCLE_T[1])],
                     [float(LIFECYCLE_T[2]), float(N)]]
        events = [{"t_s": float(LIFECYCLE_T[0]), "type": "enter"},
                  {"t_s": float(LIFECYCLE_T[1]), "type": "leave"},
                  {"t_s": float(LIFECYCLE_T[2]), "type": "reenter"}]
    else:  # empty
        intervals, events = [], []
    return {
        "schema_version": "0.1",
        "recording_id": rid,
        "kind": "synthetic_scenario_script",
        "note": ("simulation ground truth for synthetic self-test only; "
                 "real-data evaluation uses D's independent labels.csv"),
        "scenario": name,
        "token": token,
        "seed": seed,
        "level": level,
        "presence_intervals_s": intervals,
        "events": events,
    }


def build_meta(rid, name, token, level, data_level, axis_order, shape):
    return {
        "schema_version": "1.0",
        "recording_id": rid,
        "seat_id": "seat_00",
        "subject_id": None,
        "session_id": f"{DATE}_P00",
        "source_kind": "synthetic",
        "capture_start": "2026-11-17T00:00:00+08:00",
        "timestamp_unit": "seconds",
        "timestamp_origin": "recording_start",
        "data_level": data_level,
        "raw_shape": list(shape),
        "raw_dtype": "complex64",
        "axis_order": axis_order,
        "value_unit": "complex (I+jQ), arbitrary",
        "iq_representation": "complex64 I/Q (synthetic); no DC removal applied",
        "nominal_frame_rate_hz": 1,
        "radar_config": {
            "synthetic": True,
            "n_range_bins": N_BINS,
            "n_doppler_bins": N_DOP,
            "doppler_dc_index": 0,
            "range_resolution_m": 0.6,
            "bandwidth_hz": 250000000,
            "note": "G1 synthetic self-test; nominal ONELAB parameters; see "
                     "seat-detection-design-v0.1.md section 6"
        },
        "installation": {
            "position": "synthetic demo geometry (seat-detection-design-v0.1.md section 6)",
            "height_m": None,
            "angle_deg": None,
            "region_m": None,
            "region_bins": [14, 22],
            "main_bin": MAIN,
            "plan": None
        },
        "capture_notes": f"G1 synthetic self-test scenario {name} ({token}); "
                         f"first {PREFIX_S} s empty prefix; generator seed scheme "
                         f"{SEED_BASE} + crc32(scenario)*7 + segment"
    }


def build_recording(slot, name, token, seg, level, out_dir):
    seed = SEED_BASE + zlib.crc32(name.encode("utf-8")) * 7 + seg
    rid = f"{DATE}_P00_{slot}_{token}_{seg:02d}_{level}"

    rng = np.random.default_rng(seed)
    phi_c = rng.uniform(0.0, 2.0 * np.pi)   # 杂波静态相位
    phi_b = rng.uniform(0.0, 2.0 * np.pi)   # 书包静态相位

    ts = np.arange(N, dtype=np.float64)
    fids = np.arange(N, dtype=np.int64)

    if level == "1d":
        X = np.zeros((N, N_BINS), dtype=np.complex64)
        for t in range(N):
            frame = NOISE_SIGMA * (rng.standard_normal(N_BINS)
                                   + 1j * rng.standard_normal(N_BINS))
            frame[MAIN] += CLUTTER_AMP * np.exp(1j * phi_c)
            for amp, ph, _d, b in frame_components(name, t, rng, phi_b):
                frame[b] += amp * np.exp(1j * ph)
            X[t] = frame
        data_level, axis_order = "1dfft", ["frame", "range_bin"]
    else:
        X = np.zeros((N, N_DOP, N_BINS), dtype=np.complex64)
        for t in range(N):
            frame = NOISE_SIGMA * (rng.standard_normal((N_DOP, N_BINS))
                                   + 1j * rng.standard_normal((N_DOP, N_BINS)))
            frame[0, MAIN] += CLUTTER_AMP * np.exp(1j * phi_c)
            for amp, ph, d, b in frame_components(name, t, rng, phi_b):
                frame[d, b] += amp * np.exp(1j * ph)
            X[t] = frame
        data_level, axis_order = "2dfft", ["frame", "doppler_bin", "range_bin"]

    np.savez(os.path.join(out_dir, f"{rid}_raw.npz"),
             timestamps=ts, frame_ids=fids, raw_data=X)
    with open(os.path.join(out_dir, f"{rid}_meta.json"), "w", encoding="utf-8") as f:
        json.dump(build_meta(rid, name, token, level, data_level, axis_order, X.shape),
                  f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, f"{rid}_scenario_script.json"), "w", encoding="utf-8") as f:
        json.dump(scenario_script(name, token, seed, level, rid),
                  f, ensure_ascii=False, indent=2)
    return rid, seed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="G1 synthetic self-test data generator")
    ap.add_argument("--out", default=os.path.join(root, "examples", "recordings"))
    ap.add_argument("--segments", type=int, default=2)
    ap.add_argument("--lifecycle", action="store_true",
                    help="generate only the S06 lifecycle (leave-release-return) scenario")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    count = 0
    for slot, name, token in (LIFECYCLE if a.lifecycle else SCENARIOS):
        for seg in range(1, a.segments + 1):
            for level in ("1d", "2d"):
                rid, seed = build_recording(slot, name, token, seg, level, a.out)
                count += 1
                print(f"[ok] {rid}  seed={seed}")
    print(f"done: {count} recordings -> {a.out}")


if __name__ == "__main__":
    main()

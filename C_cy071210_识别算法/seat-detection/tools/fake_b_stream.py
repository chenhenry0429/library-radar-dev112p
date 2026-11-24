# -*- coding: utf-8 -*-
"""fake_b_stream —— B 未就绪时的本地 DevFrame 模拟器（联调/自测用，非交付算法）。

按 B↔C 帧级提案的 DevFrame 格式，向 stdout 输出 JSON Lines：
  {"type":"frame","frame_id":0,"timestamp":0.0,
   "raw_frame_b64":..., "raw_shape":[2,2,64,8,2],
   "valid_chirps_b64":..., "valid_shape":[2,2,64]}

合成场景：空座 0–20 s → 静坐 20–60 s（Bin1 呼吸 0.25 Hz + 微动）→ 空座 60 s 之后。
奇/偶 chirp 幅度比 0.34（模拟 tx_polling），带 −4 %/min 慢漂移。

用法（一般由 tools/selftest_field.py 或 A/B 联调脚本调用）：
    python -X utf8 tools/fake_b_stream.py --frames 310 --pace 0
    python -X utf8 tools/fake_b_stream.py --frames 310 --pid-file b.pid --alive-file b.alive
"""
import argparse
import base64
import json
import os
import sys
import time

import numpy as np

FS = 5.167958656330749
PERIOD = 1.0 / FS
BINS = np.array([16000.0, 6000.0, 500.0, 80.0, 40.0, 20.0, 15.0, 10.0])
N_CHIRP = 64


def synth_cube(t, rng):
    """单帧立方 (chip,rx,chirp,bin,iq) int16；parity 0=偶 chirp，1=奇 chirp。"""
    drift = 1.0 - 0.04 / 60.0 * t
    cube = np.zeros((2, 2, N_CHIRP, len(BINS), 2), dtype=np.int16)
    for p in range(2):
        base = BINS * (1.0 if p == 0 else 0.34) * drift
        # 上电快衰减（模拟真实设备暖机），使标定窗的微动底高于后续空座，
        # 与现场数据一致（否则 P20 底会结构性低于空座中位数）。
        amp = base * (1.0 + 0.010 * np.exp(-t / 2.5))
        amp = amp * (1.0 + rng.normal(0.0, 2e-4, size=base.shape))
        if 20.0 <= t < 60.0:  # 静坐：Bin1 呼吸 + 微动
            amp[1] *= 1.0 + 0.012 * np.sin(2 * np.pi * 0.25 * t) \
                + rng.normal(0.0, 2.5e-3)
        phase = rng.normal(0.0, 0.01, size=base.shape) + 0.05 * t
        z = amp * np.exp(1j * phase)
        # 同一 parity 的所有 chirp 用同一值，保证投影后就是上面这条剖面
        for chip in range(2):
            for rx in range(2):
                for k in range(N_CHIRP):
                    if k % 2 == p:
                        cube[chip, rx, k, :, 0] = np.round(z.real).astype(np.int16)
                        cube[chip, rx, k, :, 1] = np.round(z.imag).astype(np.int16)
    return cube


def main():
    ap = argparse.ArgumentParser(description="synthetic B DevFrame stream (JSONL stdout)")
    ap.add_argument("--frames", type=int, default=0,
                    help="0 = all (archive) / 310 (synthetic)")
    ap.add_argument("--archive", default=None,
                    help="改为回放 B 归档（raw.npz + meta.json 目录），而不是合成场景")
    ap.add_argument("--pace", type=float, default=0.0, help="seconds per frame (0 = full speed)")
    ap.add_argument("--seed", type=int, default=20261124)
    ap.add_argument("--pid-file", default=None)
    ap.add_argument("--alive-file", default=None, help="touch periodically to prove liveness")
    ap.add_argument("--zero-mask-at", type=int, default=0,
                    help="frame index with all-zero valid_chirps (0 = none)")
    a = ap.parse_args()

    if a.pid_file:
        with open(a.pid_file, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
    rng = np.random.default_rng(a.seed)
    vc = np.ones((2, 2, N_CHIRP), dtype=bool)
    arc = None
    if a.archive:
        z = np.load(os.path.join(a.archive, "raw.npz"))
        arc = {"raw": np.asarray(z["raw_data"]),
               "vc": np.asarray(z["valid_chirps"]),
               "ts": np.asarray(z["nominal_time_s"], dtype=float)}
        n_arc = arc["raw"].shape[0]
        n_total = n_arc if not a.frames else min(a.frames, n_arc)
        print(f"  archive replay: {n_total} frames from {a.archive}", file=sys.stderr)
    else:
        n_total = a.frames or 310
    for i in range(n_total):
        if arc:
            cube = arc["raw"][i].astype(np.int16)
            mask = arc["vc"][i].astype(bool)
            t = float(arc["ts"][i])
        else:
            t = i * PERIOD
            cube = synth_cube(t, rng)
            mask = vc
        if a.zero_mask_at and i == a.zero_mask_at:
            cube = np.zeros_like(cube)
            mask = np.zeros_like(mask)
        obj = {
            "type": "frame",
            "frame_id": i,
            "timestamp": float(t),
            "raw_frame_b64": base64.b64encode(cube.tobytes()).decode("ascii"),
            "raw_shape": list(cube.shape),
            "valid_chirps_b64": base64.b64encode(mask.tobytes()).decode("ascii"),
            "valid_shape": list(mask.shape),
        }
        sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        if a.alive_file and i % 10 == 0:
            with open(a.alive_file, "w", encoding="ascii") as f:
                f.write(str(i))
        if a.pace > 0:
            time.sleep(a.pace)
    # 模拟「雷达仍在跑、等待停止」：不主动退出，直到被 terminate
    while True:
        if a.alive_file:
            with open(a.alive_file, "w", encoding="ascii") as f:
                f.write("waiting")
        time.sleep(0.5)


if __name__ == "__main__":
    main()

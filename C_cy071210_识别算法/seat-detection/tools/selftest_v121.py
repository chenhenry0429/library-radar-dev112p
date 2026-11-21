# -*- coding: utf-8 -*-
"""v1.2.1 自测（无外部数据；CI 可跑）。

覆盖：
  1. 奇偶投影一致性：在线 project_dev112p_frame(parity=True) == 适配器 chirp_split；
  2. 合成空座→静坐→走远序列上，v1.2.1 满足「开场不误报 / 静坐保持 / 走远判空」；
  3. v1.2 在同一合成序列上的失败模式（仅打印，不作断言——避免跨 numpy 版本抖动）。

运行：
    python -X utf8 tools/selftest_v121.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import numpy as np

from algos.m1_features import run_m1
from algos.m1_v121 import run_m1_v121
from algos.online_detector import project_dev112p_frame
from dev112p_adapter import chirp_split

FS = 5.167958656330749
PERIOD = 1.0 / FS
BINS = np.array([16000.0, 6000.0, 500.0, 80.0, 40.0, 20.0, 15.0, 10.0])


def synth_parity(seed=20261121, duration=110.0, sit=(20.0, 60.0)):
    rng = np.random.default_rng(seed)
    n = int(duration * FS)
    ts = np.arange(n) * PERIOD
    # 慢漂移 −4 %/min（幅度），两种奇偶链幅度比 3:1
    drift = 1.0 - 0.04 / 60.0 * ts
    X = np.zeros((n, 2, len(BINS)), dtype=np.complex64)
    for p in range(2):
        base = BINS * (1.0 if p == 0 else 0.34) * drift[:, None]
        # 空座噪声 0.05 %
        amp = base * (1.0 + rng.normal(0.0, 5e-4, size=base.shape))
        # 静坐：Bin1 呼吸调制 1.2 % @0.25 Hz + 微动 0.25 %
        m = (ts >= sit[0]) & (ts < sit[1])
        breath = 0.012 * np.sin(2 * np.pi * 0.25 * ts) * m
        jitter = rng.normal(0.0, 2.5e-3, size=n) * m
        amp[:, 1] *= (1.0 + breath + jitter)
        phase = rng.normal(0.0, 0.01, size=(n, len(BINS))) + 0.05 * ts[:, None]
        X[:, p, :] = (amp * np.exp(1j * phase)).astype(np.complex64)
    return X, ts


def check_projection():
    rng = np.random.default_rng(7)
    cube = rng.integers(-20000, 20000, size=(2, 2, 64, 8, 2), dtype=np.int16)
    valid = rng.random((2, 2, 64)) > 0.1
    prof = project_dev112p_frame(cube, valid, chip=1, rx=0, parity=True)
    # 适配器口径：[F=1, 2, 8]
    x = cube[1, 0, :, :, 0].astype(np.float32) + 1j * cube[1, 0, :, :, 1].astype(np.float32)
    ref = chirp_split(x[None, ...], valid[1, 0][None, ...])[0]
    assert prof.shape == (2, 8), prof.shape
    assert np.array_equal(prof, ref), "online parity projection != adapter chirp_split"
    # 零有效 chirp 帧必须显式拒识
    try:
        project_dev112p_frame(cube, np.zeros_like(valid), chip=1, rx=0, parity=True)
    except Exception as e:  # noqa: BLE001
        assert "no valid chirp" in str(e)
    else:
        raise AssertionError("zero-valid-chirp frame must be rejected")
    print("[PASS] parity projection == adapter chirp_split (byte-identical)")


def check_v121():
    X, ts = synth_parity()
    cfg = json.load(open(os.path.join(ROOT, "config", "M1_dev112p_v1.2.1.json"),
                        encoding="utf-8"))
    meta = {"installation": {"region_bins": [1, 3], "main_bin": 2}}
    r = run_m1_v121(X, ts, meta, cfg, None)
    pr = r["presence"]
    pre = ts < 20.0
    sitting = (ts >= 25.0) & (ts < 60.0)
    after = ts > 75.0
    assert pr[pre].sum() == 0, f"opening false alarm: {int(pr[pre].sum())} frames"
    assert pr[sitting].mean() > 0.95, f"sit hold {pr[sitting].mean():.3f}"
    assert pr[after].sum() == 0, f"no release after walk-away: {int(pr[after].sum())} frames"
    zero = np.flatnonzero((pr[1:] == 0) & (pr[:-1] == 1))
    rel_t = ts[zero[0]] - 60.0 if len(zero) else None
    assert rel_t is not None and rel_t <= 20.0, f"release latency {rel_t}"
    print(f"[PASS] v1.2.1 synthetic: opening 0 FP, sit hold {pr[sitting].mean():.0%}, "
          f"release {rel_t:.1f}s after walk-away")
    return X, ts, meta


def show_v12(X, ts, meta):
    cfg = json.load(open(os.path.join(ROOT, "config", "M1_dev112p_v0.2.json"),
                        encoding="utf-8"))
    merged = X.mean(axis=1)  # v1.2 单链口径
    r = run_m1(merged, ts, meta, cfg, None)
    pr = r["presence"]
    print(f"[INFO] v1.2 on the same synthetic sequence: opening FP "
          f"{int(pr[ts < 20.0].sum())}/{(ts < 20.0).sum()}, "
          f"sit hold {pr[(ts >= 25) & (ts < 60)].mean():.0%}, "
          f"post-walk FP {int(pr[ts > 75].sum())}/{int((ts > 75).sum())}")


if __name__ == "__main__":
    check_projection()
    X, ts, meta = check_v121()
    show_v12(X, ts, meta)
    print("selftest_v121: all checks passed")

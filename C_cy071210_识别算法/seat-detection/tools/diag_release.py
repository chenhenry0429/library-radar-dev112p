# -*- coding: utf-8 -*-
"""diag_release —— 判空后基线/微动诊断（离座倒计时被取消问题的复现工具）。

用法：
    python -X utf8 tools/diag_release.py --recording <parity_raw.npz> \
        [--config config/M1_dev112p_v1.2.1.json] [--from 60] [--to 200]

输出（逐帧）：timestamp、floor（空座噪声底）、m（10 s 微动比）、m4、f1n、
presence；并在切换点打印 release / re-entry 时刻与当时的前 10 s 窗口统计，
用于回答「判空后重建的空座基线是否偏低 / 十秒后 m 为什么仍高于入座阈值」。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from detect import load_json, normalize_axes
from algos.m1_v121 import M1V121State


def main():
    ap = argparse.ArgumentParser(description="post-release baseline / micro-motion trace")
    ap.add_argument("--recording", required=True)
    ap.add_argument("--config", default=os.path.join("config", "M1_dev112p_v1.2.1.json"))
    ap.add_argument("--from", dest="t0", type=float, default=None)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--step", type=int, default=1, help="print every N frames")
    a = ap.parse_args()

    rid = os.path.basename(a.recording)[: -len("_raw.npz")]
    meta = load_json(os.path.join(os.path.dirname(a.recording), f"{rid}_meta.json"))
    z = np.load(a.recording)
    X, _ = normalize_axes(z["raw_data"], meta)
    ts = np.asarray(z["timestamps"], float)
    cfg = load_json(a.config)

    st = M1V121State(cfg, meta, None, X.shape[-1], complex_input=np.iscomplexobj(X),
                     dt_med=float(np.median(np.diff(ts))))
    floors, m, m4, f1n, pres = [], [], [], [], []
    for i in range(len(ts)):
        st.step(X[i], float(ts[i]))
        floors.append(st.floor)
        m.append(st.m_trace[i])
        m4.append(st.m4_trace[i])
        f1n.append(st.f1n[i])
        pres.append(int(st.presence[i]))

    t0 = a.t0 if a.t0 is not None else float(ts[0])
    t1 = a.t1 if a.t1 is not None else float(ts[-1])
    print(f"# {rid}: frames={len(ts)} floor_calib={st.floor:.6f} config={a.config}")
    print("#     t     floor       m     m4   f1n  pres")
    for i in range(len(ts)):
        if not (t0 <= ts[i] <= t1):
            continue
        changed = i > 0 and pres[i] != pres[i - 1]
        if a.step <= 1 or changed or i % a.step == 0:
            flag = ""
            if changed:
                flag = "  <== %s" % ("RE-ENTRY" if pres[i] == 1 else "release")
            print("%7.2f  %.6f %6.2f %6.2f %5.2f  %2d%s"
                  % (ts[i], floors[i], m[i], m4[i], f1n[i], pres[i], flag))

    # 事件摘要
    print("\n# transitions")
    for i in range(1, len(pres)):
        if pres[i] != pres[i - 1]:
            kind = "re-entry" if pres[i] == 1 else "release"
            print("  %8.2f s  %s  floor=%.6f m=%.2f m4=%.2f f1n=%.2f"
                  % (ts[i], kind, floors[i], m[i], m4[i], f1n[i]))
    # 判空后 30 s 的 floor/m 统计（看基线是否漂低、m 是否回升）
    for i in range(1, len(pres)):
        if pres[i] == 0 and pres[i - 1] == 1:
            t_r = ts[i]
            sel = (ts > t_r) & (ts <= t_r + 30.0)
            if sel.any():
                print("  after release @%.1f s: floor %.6f -> %.6f, m med %.2f max %.2f"
                      % (t_r, floors[i], float(np.median(np.asarray(floors)[sel])),
                         float(np.median(np.asarray(m)[sel])),
                         float(np.max(np.asarray(m)[sel]))))


if __name__ == "__main__":
    main()

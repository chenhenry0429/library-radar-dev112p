# -*- coding: utf-8 -*-
"""规范 v1 §5 输出一致性校验（8 条约束 + 行级枚举/语法校验）。

用法：
    from algos.selfcheck import check_result_csv
    ok, errors, warns = check_result_csv(csv_path, meta_path=None, npz_path=None)

独立运行：
    python algos/selfcheck.py <rid>_result.csv [--meta <rid>_meta.json]
                                          [--npz <rid>_raw.npz]

8 条约束（编号与设计文档 §3.3 对应）：
  C1  N 帧 N 行；frame_id 从 0 连续；timestamp 原样复制、float64 往返、严格递增、非负
  C2  presence=0  → target_count=0 且 posture=not_applicable
  C3  presence=-1 → target_count=-1、posture=unknown、两个置信度均空
  C4  presence=1  → target_count 为正整数或 -1
  C5  quality≠ok  → presence=-1、posture=unknown、两个置信度均空
  C6  calibration_status≠valid 且 presence=1 → posture=unknown
  C7  posture ∈ 三类姿态 → calibration_status=valid 且 presence=1
  C8  posture ∈ {unknown,not_applicable} → posture_confidence 空
"""
import csv
import json
import os
import sys

HEADER = ["frame_id", "timestamp", "presence", "presence_confidence",
          "target_count", "posture", "posture_confidence",
          "calibration_status", "quality"]

PRESENCE = {"-1", "0", "1"}
POSTURE = {"reference", "forward", "backward", "unknown", "not_applicable"}
CAL = {"uncalibrated", "calibrating", "valid", "invalid"}
QUALITY = {"ok", "low_signal", "invalid_frame"}
POSTURE_TRUE = {"reference", "forward", "backward"}


def _parse_conf(s):
    """置信度：空返回 None；否则 [0,1] 浮点。非法返回 'BAD'。"""
    if s == "":
        return None
    try:
        v = float(s)
    except ValueError:
        return "BAD"
    return v if 0.0 <= v <= 1.0 else "BAD"


def check_result_csv(csv_path, meta_path=None, npz_path=None):
    """校验一个 result.csv，返回 (ok, errors, warnings)。"""
    errors, warns = [], []

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return False, ["result.csv is empty"], []
    if rows[0] != HEADER:
        return False, [f"header mismatch (expect 9 fixed columns): {rows[0]}"], []
    data = [r for r in rows[1:] if any(c.strip() != "" for c in r)]
    n = len(data)
    for i, r in enumerate(data):
        if len(r) != 9:
            errors.append(f"row {i + 2}: has {len(r)} fields, expect 9")

    # ---------- C1：frame_id 连续 + timestamp 往返/递增/非负 ----------
    prev_ts = None
    for i, r in enumerate(data):
        rowno = i + 2
        fid, ts = r[0], r[1]
        try:
            int(fid)
        except ValueError:
            errors.append(f"row {rowno}: frame_id not int: {fid!r}")
        try:
            ts_f = float(ts)
        except ValueError:
            errors.append(f"row {rowno}: timestamp not float: {ts!r}")
        else:
            if repr(ts_f) != ts:
                errors.append(f"row {rowno}: timestamp {ts!r} not float64 round-trip ({ts_f!r})")
            if ts_f < 0:
                errors.append(f"row {rowno}: negative timestamp {ts_f}")
            if prev_ts is not None and ts_f <= prev_ts:
                errors.append(f"row {rowno}: timestamp not strictly increasing ({prev_ts} -> {ts_f})")
            prev_ts = ts_f
    try:
        fids = [int(r[0]) for r in data]
    except (ValueError, IndexError):
        fids = None
    if fids is not None and fids != list(range(n)):
        errors.append("C1: frame_id not sequential from 0")

    # ---------- 行级枚举与约束 ----------
    for i, r in enumerate(data):
        rowno = i + 2
        if len(r) != 9:
            continue
        _fid, _ts, pr, prc, tc, po, poc, cal, q = r

        if pr not in PRESENCE:
            errors.append(f"row {rowno}: illegal presence {pr!r}")
        if po not in POSTURE:
            errors.append(f"row {rowno}: illegal posture {po!r}")
        if cal not in CAL:
            errors.append(f"row {rowno}: illegal calibration_status {cal!r}")
        if q not in QUALITY:
            errors.append(f"row {rowno}: illegal quality {q!r}")

        prc_v = _parse_conf(prc)
        poc_v = _parse_conf(poc)
        if prc_v == "BAD":
            errors.append(f"row {rowno}: presence_confidence not empty/[0,1]: {prc!r}")
        if poc_v == "BAD":
            errors.append(f"row {rowno}: posture_confidence not empty/[0,1]: {poc!r}")

        try:
            tc_i = int(tc)
            tc_ok = (tc_i == -1) or (tc_i >= 0)
        except ValueError:
            tc_i, tc_ok = None, False
        if not tc_ok:
            errors.append(f"row {rowno}: target_count not (non-negative int or -1): {tc!r}")

        # C2 / C3 / C4
        if pr == "0":
            if tc_i != 0:
                errors.append(f"row {rowno}: C2 presence=0 but target_count={tc!r}")
            if po != "not_applicable":
                errors.append(f"row {rowno}: C2 presence=0 but posture={po!r}")
        elif pr == "-1":
            if tc_i != -1:
                errors.append(f"row {rowno}: C3 presence=-1 but target_count={tc!r}")
            if po != "unknown":
                errors.append(f"row {rowno}: C3 presence=-1 but posture={po!r}")
            if prc != "" or poc != "":
                errors.append(f"row {rowno}: C3 presence=-1 but confidences not empty")
        elif pr == "1":
            if not (tc_i == -1 or tc_i >= 1):
                errors.append(f"row {rowno}: C4 presence=1 but target_count={tc!r}")

        # C5 quality
        if q != "ok" and not (pr == "-1" and po == "unknown" and prc == "" and poc == ""):
            errors.append(f"row {rowno}: C5 quality={q!r} requires presence=-1/unknown/empty-conf")

        # C6 标定未生效时不得输出姿态
        if cal != "valid" and pr == "1" and po != "unknown":
            errors.append(f"row {rowno}: C6 cal={cal!r} presence=1 but posture={po!r}")

        # C7 三类姿态的前置
        if po in POSTURE_TRUE and not (cal == "valid" and pr == "1"):
            errors.append(f"row {rowno}: C7 posture={po!r} requires cal=valid and presence=1 "
                          f"(got cal={cal!r}, presence={pr!r})")

        # C8 姿态置信度仅在三类真值时允许非空
        if po in ("unknown", "not_applicable") and poc != "":
            errors.append(f"row {rowno}: C8 posture={po!r} but posture_confidence={poc!r}")

        # 软告警：presence 已知却缺置信度（规范未禁止，但建议给出）
        if pr in ("0", "1") and prc == "":
            warns.append(f"row {rowno}: presence={pr} with empty presence_confidence")

    # ---------- 与 raw.npz / meta 的行数、帧号、时间戳逐一比对 ----------
    if npz_path and os.path.exists(npz_path):
        import numpy as np
        try:
            z = np.load(npz_path)
            ts_arr = np.asarray(z["timestamps"], dtype=np.float64)
            fid_arr = np.asarray(z["frame_ids"], dtype=np.int64)
        except Exception as e:  # noqa: BLE001
            errors.append(f"cannot read npz {npz_path}: {e}")
        else:
            if n != len(ts_arr):
                errors.append(f"C1: row count {n} != npz frames {len(ts_arr)}")
            elif fids is not None:
                for i in range(n):
                    if fids[i] != int(fid_arr[i]):
                        errors.append(f"C1: row {i + 2} frame_id {fids[i]} != npz {int(fid_arr[i])}")
                        break
                for i in range(n):
                    if float(data[i][1]) != float(ts_arr[i]):
                        errors.append(f"C1: row {i + 2} timestamp {data[i][1]} != npz {float(ts_arr[i])!r}")
                        break
    elif meta_path and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        shape = meta.get("raw_shape")
        if isinstance(shape, (list, tuple)) and shape and n != int(shape[0]):
            errors.append(f"C1: row count {n} != meta raw_shape[0] {int(shape[0])}")

    return (len(errors) == 0), errors, warns


if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
    ap = argparse.ArgumentParser(description="offline interface v1 result.csv self-check")
    ap.add_argument("csv", help="path to <rid>_result.csv")
    ap.add_argument("--meta", default=None)
    ap.add_argument("--npz", default=None)
    a = ap.parse_args()
    npz = a.npz
    if npz is None and a.meta:
        cand = os.path.splitext(a.meta)[0].replace("_meta", "_raw") + ".npz"
        if os.path.exists(cand):
            npz = cand
    ok, errors, warns = check_result_csv(a.csv, a.meta, npz)
    print(f"{a.csv}: {'PASS' if ok else 'FAIL'}  ({len(errors)} errors, {len(warns)} warnings)")
    for e in errors[:50]:
        print(f"  ERROR: {e}")
    for w in warns[:20]:
        print(f"  warn : {w}")
    sys.exit(0 if ok else 1)

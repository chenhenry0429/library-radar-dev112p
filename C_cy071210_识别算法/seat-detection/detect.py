# -*- coding: utf-8 -*-
"""detect.py —— 离线接口 v1 统一入口 CLI。

用法：
    python detect.py --recording <path/to/<rid>_raw.npz | 目录>
                     [--algorithm M1|B0] [--config <json>] [--region-params <json>]
                     [--posture-config <json>] [--out <dir>] [--emit-scores] [--strict]

行为（设计文档 §10）：
  * 目录输入 = 批处理目录内全部 *_raw.npz（按名排序）。
  * --algorithm 缺省按 meta.data_level 推断：1dfft→M1，2dfft→B0；dsraw → 明确报错（待 DS10012RN）。
  * 轴规整：按 meta.axis_order 转置为 [frame,(channel),(doppler),range]；channel 非相干功率合并。
  * M1 允许 2dfft 输入（doppler 功率合并 → 实数退化模式，低可靠、如实记录）；
    B0 需 doppler 轴（1dfft → 报错）。
  * --posture-config（仅 M1）：加载 M2 姿态 v0.1 管线填充 posture/posture_confidence/
    calibration_status 列（gated baseline：参考标定 + 特征 + 对比度质量门 + 拒识；
    门失败时 posture 全程 unknown、cal=invalid，原因写 algorithm.json notes）。
    缺省不加载 → posture 列保持接口占位（unknown / not_applicable）。
  * 输出 <rid>_result.csv + <rid>_algorithm.json（与输入同目录或 --out）；
    timestamp 用 repr(float) 保证 float64 往返；写盘后立即 selfcheck，FAIL → 非零退出。
  * --emit-scores 另写辅助文件 <rid>_scores.csv（非接口文件，仅分析用）。
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import sys

import numpy as np

from algos.m1_features import run_m1
from algos.b0_pointcloud import run_b0
from algos.m2_posture import run_m2
from algos.region import load_region_params
from algos.selfcheck import check_result_csv

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(ROOT, "config")

HEADER = ["frame_id", "timestamp", "presence", "presence_confidence",
          "target_count", "posture", "posture_confidence",
          "calibration_status", "quality"]

KNOWN_AXES = {"frame", "chirp_parity", "channel", "doppler_bin", "range_bin"}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def md5_8(obj):
    return hashlib.md5(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]


def normalize_axes(raw, meta):
    """按 axis_order 规整为 [frame,(chirp_parity),(channel),(doppler),range]。

    channel 轴做非相干功率合并（等效幅值 sqrt(Σ|X|²)——CFAR/功率特征语义不变）。
    chirp_parity 轴保留（v1.2.1 奇偶分开处理，见 algos/m1_v121.py）。
    返回 (X, has_doppler)。
    """
    order = [str(a).lower() for a in (meta.get("axis_order") or [])]
    if not order:
        raise ValueError("meta.axis_order missing -- data layout must be explicit (spec section 4)")
    raw = np.asarray(raw)
    if raw.ndim != len(order):
        raise ValueError(f"axis_order {order} length != raw_data ndim {raw.ndim}")
    unknown = [a for a in order if a not in KNOWN_AXES]
    if unknown:
        raise ValueError(f"unknown axis names in axis_order: {unknown} (known: {sorted(KNOWN_AXES)})")
    if "frame" not in order or "range_bin" not in order:
        raise ValueError("axis_order must contain 'frame' and 'range_bin'")
    desired = [a for a in ("frame", "chirp_parity", "doppler_bin", "range_bin", "channel")
               if a in order]
    X = np.transpose(raw, [order.index(a) for a in desired])
    if "channel" in desired:
        X = np.sqrt(np.sum(np.abs(X.astype(np.complex128)) ** 2, axis=-1))
    return X, ("doppler_bin" in desired)


def validate_inputs(ts, fids, raw, meta):
    """规范 §3/§4/§8 的输入硬校验；任何失败都不开始处理。"""
    errors = []
    n = len(ts)
    if n == 0:
        errors.append("empty recording (0 frames)")
        return errors
    if len(fids) != n or raw.shape[0] != n:
        errors.append(f"length mismatch: timestamps {n}, frame_ids {len(fids)}, raw_data {raw.shape[0]}")
        return errors
    ts = np.asarray(ts, dtype=np.float64)
    if np.any(np.diff(ts) <= 0):
        errors.append("timestamps not strictly increasing")
    if np.any(ts < 0):
        errors.append("negative timestamps")
    if [int(v) for v in fids] != list(range(n)):
        errors.append("frame_ids not sequential from 0 (spec: archive row numbers)")
    shape = meta.get("raw_shape")
    if isinstance(shape, (list, tuple)) and list(shape) != list(raw.shape):
        errors.append(f"meta.raw_shape {shape} != actual {list(raw.shape)}")
    dtype_name = str(meta.get("raw_dtype") or "").lower()
    is_complex_meta = dtype_name.startswith("complex")
    if is_complex_meta and not np.iscomplexobj(raw):
        errors.append(f"meta.raw_dtype {meta.get('raw_dtype')} but raw_data is real-valued")
    if not meta.get("data_level"):
        errors.append("meta.data_level missing")
    return errors


def _fmt_conf(c):
    return "" if c is None else repr(float(c))


def build_rows(fids, ts, presence, conf, target_count, quality,
               posture_out=None, posture_conf_out=None, calib_out=None):
    """posture_out 为 None → 接口占位（v1 缺省）；否则用 M2 v0.1 输出。"""
    rows = []
    for i in range(len(fids)):
        pr = int(presence[i])
        if posture_out is None:
            po = "not_applicable" if pr == 0 else "unknown"
            poc, cal = "", "uncalibrated"
        else:
            po, poc, cal = posture_out[i], posture_conf_out[i], calib_out[i]
        rows.append([
            int(fids[i]),
            repr(float(ts[i])),
            pr,
            _fmt_conf(conf[i]),
            int(target_count[i]),
            po,
            poc,
            cal,
            quality[i],
        ])
    return rows


def write_result_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def write_scores_csv(path, fids, ts, fields):
    """fields: dict[col_name, list]；辅助文件，非接口。"""
    cols = list(fields)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frame_id", "timestamp"] + cols)
        for i in range(len(fids)):
            w.writerow([int(fids[i]), repr(float(ts[i]))]
                       + [fields[c][i] for c in cols])


def load_algo_config(algorithm, explicit_path):
    if explicit_path:
        cfg = load_json(explicit_path)
        return cfg, explicit_path
    default = os.path.join(CONFIG_DIR, f"{algorithm}_default.json")
    if os.path.exists(default):
        return load_json(default), default
    return None, f"{algorithm}_default.json (missing; built-in defaults used)"


def infer_algorithm(meta):
    dl = meta.get("data_level")
    if dl == "1dfft":
        return "M1"
    if dl == "2dfft":
        return "B0"
    raise ValueError(
        f"data_level {dl!r}: offline v1 supports 1dfft/2dfft only "
        "(dsraw pending DS10012RN raw protocol; spec: do not guess unknown formats)")


def process_recording(npz_path, args, region_params):
    rid = os.path.basename(npz_path)
    if rid.endswith("_raw.npz"):
        rid = rid[: -len("_raw.npz")]
    base = os.path.dirname(os.path.abspath(npz_path))

    meta_path = os.path.join(base, f"{rid}_meta.json")
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"meta.json missing beside npz: {meta_path}")
    meta = load_json(meta_path)

    z = np.load(npz_path)  # allow_pickle=False by default (spec: no object arrays)
    for key in ("timestamps", "frame_ids", "raw_data"):
        if key not in z.files:
            raise ValueError(f"raw.npz missing required key {key!r}")
    ts = np.asarray(z["timestamps"], dtype=np.float64)
    fids = np.asarray(z["frame_ids"])
    raw = np.asarray(z["raw_data"])

    problems = validate_inputs(ts, fids, raw, meta)
    if problems:
        raise ValueError("input validation failed: " + "; ".join(problems))

    X, has_doppler = normalize_axes(raw, meta)
    algorithm = args.algorithm or infer_algorithm(meta)

    # ---- 算法路由 ----
    degraded_input = False
    notes = []
    if algorithm == "M1":
        if has_doppler:  # 2dfft → doppler 功率合并 → 实数退化
            X = np.sqrt(np.sum(np.abs(X.astype(np.complex128)) ** 2, axis=1))
            degraded_input = True
            notes.append("2dfft input collapsed to power-sum range profile (real degraded mode)")
        if not np.iscomplexobj(X):
            degraded_input = True
        cfg, cfg_src = load_algo_config("M1", args.config)
        result = run_m1(X, ts, meta, cfg, region_params)
        presence = result["presence"]
        conf = result["conf"]
        quality = result["quality"]
        target_count = [1 if p == 1 else (0 if p == 0 else -1) for p in presence]
        windows = (cfg or {}).get("windows", {})
        window_s = max([float(v) for v in windows.values()] or [4.0])
        thresholds = (cfg or {}).get("thresholds", {})
        smoothing = (f"dual-threshold hysteresis T_ON={thresholds.get('t_on', 0.5)} "
                     f"T_OFF={thresholds.get('t_off', 0.35)}, confirm_frames="
                     f"{(cfg or {}).get('hysteresis_confirm_frames', 1)}")
        # 版本标签可由 config 覆盖（默认保持 v1.1；v1.2 snapshot 配置需正确标识）
        algorithm_version = str((cfg or {}).get("algorithm_version", "M1 v1.1"))
        model_version = str((cfg or {}).get("model_version", "M1-v1.1"))
        # ---- M2 姿态 v0.1（可选，--posture-config）----
        posture_out = posture_conf_out = calib_out = None
        posture_enabled = False
        posture_gate_pass = None
        if getattr(args, "posture_config", None):
            m2_cfg = load_json(args.posture_config)
            m2 = run_m2(X, ts, presence, quality, m2_cfg, result["region"])
            posture_out = m2["posture"]
            posture_conf_out = m2["posture_conf"]
            calib_out = m2["calib_status"]
            posture_enabled = True
            posture_gate_pass = bool(m2["gate_pass"])
            notes.append(
                "posture: M2 v0.1 gated baseline; gate_pass="
                f"{m2['gate_pass']}; " + " | ".join(m2["notes"]))
            algorithm_version = algorithm_version + " + M2 v0.1 (gated)"
            model_version = model_version + "+M2-v0.1"
    elif algorithm == "B0":
        if not has_doppler:
            raise ValueError("B0 requires a doppler axis (2dfft); use --algorithm M1 for 1dfft input")
        cfg, cfg_src = load_algo_config("B0", args.config)
        result = run_b0(X, ts, meta, cfg, region_params)
        presence = result["presence"]
        conf = result["conf"]
        quality = result["quality"]
        target_count = result["target_count"]
        window_s = 0.0
        hys = (cfg or {}).get("hysteresis", {})
        smoothing = (f"count hysteresis t_on_frames={hys.get('t_on_frames', 2)} "
                     f"t_off_frames={hys.get('t_off_frames', 3)} (per-frame CFAR, no temporal window)")
        algorithm_version, model_version = "B0 v0.1 (candidate-3)", "B0-v0.1-c3"
    else:
        raise ValueError(f"unknown algorithm {algorithm!r}")

    notes.extend(result.get("warnings") or [])

    # ---- 写 result.csv / algorithm.json ----
    out_dir = args.out or base
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f"{rid}_result.csv")
    algo_path = os.path.join(out_dir, f"{rid}_algorithm.json")

    write_result_csv(csv_path, build_rows(fids, ts, presence, conf, target_count,
                                           quality, posture_out, posture_conf_out,
                                           calib_out))

    effective = {
        "algorithm": algorithm,
        "config": cfg if cfg is not None else "built-in defaults",
        "config_source": cfg_src,
        "region_params": region_params if region_params is not None else {},
        "degraded_input": degraded_input,
        "region": {k: result["region"][k] for k in ("lo", "hi", "main_bin", "source")},
    }
    if posture_enabled:
        effective["posture_config"] = load_json(args.posture_config)
        effective["posture_gate_pass"] = posture_gate_pass
    with open(algo_path, "w", encoding="utf-8") as f:
        json.dump({
            "schema_version": "1.0",
            "recording_id": rid,
            "algorithm_version": algorithm_version,
            "parameter_version": md5_8(effective),
            "capabilities": {
                "presence": True,
                "posture": posture_enabled,
                "posture_semantics": (
                    "M2 v0.1 gated baseline: pipeline implemented (reference "
                    "calibration, centroid/energy features, contrast quality "
                    "gate, threshold classification with reject guards). "
                    "posture=true means the pipeline ran; recordings failing "
                    "the contrast gate output posture=unknown with "
                    "calibration_status=invalid for all occupied frames "
                    "(explicit rejection, not placeholder). See notes."),

            },
            "config": effective,
            "window_s": window_s,
            "smoothing": smoothing,
            "model_version": model_version,
            "calibrations": [],
            "notes": notes,
        }, f, ensure_ascii=False, indent=2)

    # ---- 辅助分数文件（非接口，仅分析用） ----
    if args.emit_scores:
        if algorithm == "M1":
            write_scores_csv(os.path.join(out_dir, f"{rid}_scores.csv"),
                             fids, ts,
                             {"score": [repr(float(v)) for v in result["score"]],
                              "f1n": [repr(float(v)) for v in result["f1n"]],
                              "f2n": [repr(float(v)) for v in result["f2n"]],
                              "f3n": [repr(float(v)) for v in result["f3n"]],
                              "gate": [repr(float(v)) for v in result["gate"]]})
        else:  # B0
            write_scores_csv(os.path.join(out_dir, f"{rid}_scores.csv"),
                             fids, ts,
                             {"points_per_frame": [int(v) for v in result["points_per_frame"]]})

    # ---- 立即自检（含与 npz 的行数/帧号/时间戳逐一比对） ----
    ok, errors, warns = check_result_csv(csv_path, meta_path, npz_path)
    for w in warns[:5]:
        print(f"  warn: {w}")
    if not ok:
        for e in errors[:20]:
            print(f"  ERROR: {e}")
    print(f"[{'PASS' if ok else 'FAIL'}] {rid}  algorithm={algorithm}"
          f"{' (degraded)' if degraded_input else ''}  -> {csv_path}")
    return ok


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="seat-detection offline CLI (interface v1)")
    ap.add_argument("--recording", required=True,
                    help="path to <rid>_raw.npz or a directory containing *_raw.npz")
    ap.add_argument("--algorithm", choices=["M1", "B0"], default=None,
                    help="default: inferred from meta.data_level (1dfft->M1, 2dfft->B0)")
    ap.add_argument("--config", default=None, help="algorithm config JSON override")
    ap.add_argument("--posture-config", default=None,
                    help="M1 only: M2 posture v0.1 config JSON; enables posture "
                         "pipeline (gated baseline with explicit rejection)")
    ap.add_argument("--region-params", default=os.path.join(CONFIG_DIR, "region_params_v0.1.json"))
    ap.add_argument("--out", default=None, help="output dir (default: beside input npz)")
    ap.add_argument("--emit-scores", action="store_true",
                    help="also write auxiliary <rid>_scores.csv (not part of the interface)")
    ap.add_argument("--strict", action="store_true",
                    help="abort the whole batch on first failure")
    a = ap.parse_args()

    target = os.path.abspath(a.recording)
    if os.path.isdir(target):
        paths = sorted(glob.glob(os.path.join(target, "*_raw.npz")))
        if not paths:
            print(f"no *_raw.npz found in {target}")
            sys.exit(1)
    else:
        paths = [target]

    region_params = load_region_params(a.region_params) if os.path.exists(a.region_params) else None
    if region_params is None:
        print("note: region_params not found -> geometry fallback disabled, "
              "meta_explicit or full-range degraded will be used")

    failures = 0
    for p in paths:
        try:
            ok = process_recording(p, a, region_params)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {os.path.basename(p)}: {e}")
            ok = False
        if not ok:
            failures += 1
            if a.strict:
                print("strict mode: aborting batch")
                break
    print(f"done: {len(paths) - failures}/{len(paths)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

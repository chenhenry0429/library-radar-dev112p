"""B/C/A offline v1 command line; synthetic demonstration is never measured data."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import zipfile
import numpy as np
from contract import (ContractError, SCHEMA, load_b, load_c, paths, read_json, save_b,
                      sha256, write_json, write_result)
from offline_state_machine import DEFAULT_CONFIG, run_a


def synthetic_meta(rid, raw):
    return dict(schema_version=SCHEMA, recording_id=rid, seat_id="SEAT01", subject_id="PDEMO",
        session_id="S01", source_kind="synthetic", capture_start="2026-11-03T10:00:00+08:00",
        timestamp_unit="seconds", timestamp_origin="recording_start", timestamp_source="synthetic fixture clock",
        data_level="synthetic_numeric_fixture_not_radar", raw_shape=list(raw.shape), raw_dtype=str(raw.dtype),
        axis_order=["frame", "synthetic_channel", "synthetic_bin"], value_unit="arbitrary_fixture_unit",
        iq_representation="real synthetic values; not measured ADC/IQ", nominal_frame_rate_hz=10.0,
        radar_config=dict(model="DEV112P", firmware_version=None, channel_mapping=None,
            sample_rate_hz=None, sweep_config=None, firmware_filter=None),
        installation=dict(location="synthetic single-seat fixture", height_m=None, angle_deg=None,
            roi_definition="synthetic target seat only; no physical installation"),
        capture_notes="DEMO ONLY. Synthetic arrays and evidence are independently scripted. No acquisition, decoding, trained classifier, posture accuracy, or real calibration is demonstrated.")


def synthetic_algorithm(rid, calibrations):
    return dict(schema_version=SCHEMA, recording_id=rid, algorithm_version="synthetic-evidence-fixture-1.0",
        parameter_version="demo-1", capabilities=dict(presence=True, posture=True),
        config=dict(source="scripted synthetic evidence, NOT inferred from raw_data", causal_fixture=True),
        thresholds={}, window_s=0.0, temporal_smoothing="none; scripted per-frame evidence",
        model_version=None, calibrations=calibrations)


def calibration(cid, start, end, invalid=None):
    return dict(calibration_id=cid, seat_id="SEAT01", start_timestamp=float(start),
        end_timestamp=float(end), valid_from_timestamp=float(end),
        invalid_from_timestamp=invalid, invalidation_reason="scripted departure invalidation" if invalid is not None else None)


def evidence(i, t, presence, posture="unknown", cal="uncalibrated", quality="ok"):
    return dict(frame_id=i, timestamp=float(t), presence=presence,
        presence_confidence=0.95 if presence != -1 else None,
        target_count={-1: -1, 0: 0, 1: 1}[presence],
        posture="not_applicable" if presence == 0 else ("unknown" if presence == -1 else posture),
        posture_confidence=0.9 if presence == 1 and posture in {"reference", "forward", "backward"} else None,
        calibration_status=cal, quality=quality)


def make_demo(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # 10 Hz; a six-second data gap is explicit. Final session is intentionally incomplete.
    times = np.array([i / 10 for i in range(1941) if not 1040 <= i < 1100], dtype=np.float64)
    raw = np.random.default_rng(112).normal(size=(len(times), 2, 16)).astype(np.float32)
    rid = "20261103_PDEMO_S01_lifecycle_01"
    folder = save_b(root, synthetic_meta(rid, raw), raw, times)
    cals = [calibration("DEMO-CAL-01", 6, 16, 58.0), calibration("DEMO-CAL-02", 68, 80, 115.0)]
    rows = []
    for i, t in enumerate(times):
        if t < 4 or 58 <= t < 65 or 115 <= t < 185:
            r = evidence(i, t, 0)
        else:
            cal = "uncalibrated"
            pose = "unknown"
            if 6 <= t < 16 or 68 <= t < 80:
                cal = "calibrating"
            if 16 <= t < 58 or 80 <= t < 115:
                cal = "valid"
                pose = "reference"
                if 18 <= t < 38 or 83 <= t < 104:
                    pose = "forward"
                elif 42 <= t < 58:
                    pose = "backward"
                if 34 <= t < 35:
                    pose = "unknown"
            r = evidence(i, t, 1, pose, cal)
        rows.append(r)
    p = paths(folder)
    write_result(p["result"], rows)
    write_json(p["algorithm"], synthetic_algorithm(rid, cals))
    config = dict(DEFAULT_CONFIG, data_gap_s=0.5)
    cfg = root / "demo_config.json"
    write_json(cfg, config)
    load_c(folder)
    a_out = run_a(folder, cfg, root / "output", "demo_run_01")
    # Exact five-row legal-combination example from the supplied PDF, with required companions.
    rid2 = "20261103_PDEMO_S01_mixed_02"
    t2 = np.arange(5, dtype=np.float64) / 10
    raw2 = np.zeros((5, 2, 16), dtype=np.float32)
    f2 = save_b(root, synthetic_meta(rid2, raw2), raw2, t2)
    r2 = [evidence(0, 0, -1, quality="low_signal"), evidence(1, .1, 0), evidence(2, .2, 1),
          evidence(3, .3, 1, cal="calibrating"), evidence(4, .4, -1, cal="invalid", quality="invalid_frame")]
    for r, conf in zip(r2, (None, .95, .92, .93, None)):
        r["presence_confidence"] = conf
    write_result(paths(f2)["result"], r2)
    write_json(paths(f2)["algorithm"], synthetic_algorithm(rid2, []))
    load_c(f2)
    (root / "DEMO_ONLY.txt").write_text("仅用于接口和状态机联调的模拟数据。不是实测，不可计算雷达识别准确率。\n", encoding="utf-8")
    return folder, a_out


def pack(folder, destination, stage="c"):
    folder, destination = Path(folder), Path(destination)
    if stage == "b":
        _, meta = load_b(folder)
        keys = ("raw", "meta")
    else:
        _, meta, _ = load_c(folder)
        keys = ("raw", "meta", "result", "algorithm")
    p = paths(folder)
    files = {f"{folder.name}/{p[k].name}": p[k] for k in keys}
    code_root = Path(__file__).parent
    for name in ("read_data.py", "contract.py", "verify_transfer.py", "run_python.cmd", "README_组员离线读取.txt"):
        files[name] = code_root / name
    manifest = dict(schema_version=SCHEMA, recording_id=meta["recording_id"], stage=stage,
        source_kind=meta["source_kind"], files={n: sha256(f) for n, f in files.items()})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as z:
        for name, file in files.items():
            z.write(file, name)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo", help="generate synthetic B/C fixtures and A outputs; no radar needed")
    d.add_argument("--output", default="DEMO_ONLY")
    v = sub.add_parser("validate", help="strictly validate closed B or B+C files")
    v.add_argument("folder")
    v.add_argument("--stage", choices=["b", "c"], default="c")
    a = sub.add_parser("run-a", help="process C evidence, never labels or raw classification")
    a.add_argument("folder")
    a.add_argument("--config", required=True)
    a.add_argument("--output", default="output")
    a.add_argument("--run-id", default=None)
    b = sub.add_parser("import-decoded", help="archive already decoded fixed-shape arrays; not a serial decoder")
    b.add_argument("--raw", required=True, help="numeric .npy, actual decoded payload")
    b.add_argument("--timestamps", required=True, help="float64 .npy relative measured seconds; never nominal-frame synthesis")
    b.add_argument("--meta", required=True, help="complete verified metadata, no unknown layout")
    b.add_argument("--device-frame-ids")
    b.add_argument("--output", default="recordings")
    z = sub.add_parser("pack", help="create SHA256 manifest ZIP for manual offline handover")
    z.add_argument("folder")
    z.add_argument("--output", required=True)
    z.add_argument("--stage", choices=["b", "c"], default="c")
    args = parser.parse_args()
    if args.command == "demo":
        folder, out = make_demo(args.output)
        print("SYNTHETIC ONLY - no real radar data or recognition validation")
        print("B+C:", folder.resolve())
        print("A:", out.resolve())
    elif args.command == "validate":
        if args.stage == "b":
            arrays, meta = load_b(args.folder)
            n = len(arrays["timestamps"])
        else:
            rows, meta, _ = load_c(args.folder)
            n = len(rows)
        print(f"VALID {args.stage.upper()}: {n} frames, source_kind={meta['source_kind']}")
    elif args.command == "run-a":
        run_id = args.run_id or datetime.now().strftime("run_%Y%m%dT%H%M%S_%f")
        print(run_a(args.folder, args.config, args.output, run_id).resolve())
    elif args.command == "import-decoded":
        raw = np.load(args.raw, allow_pickle=False)
        times = np.load(args.timestamps, allow_pickle=False)
        device = np.load(args.device_frame_ids, allow_pickle=False) if args.device_frame_ids else None
        print(save_b(args.output, read_json(args.meta), raw, times, device).resolve())
    elif args.command == "pack":
        print(pack(args.folder, args.output, args.stage).resolve())


if __name__ == "__main__":
    try:
        main()
    except (ContractError, OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(2)

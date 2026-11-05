"""Offline v1 file contract. No device decoder or radar classifier is implied."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
import numpy as np

SCHEMA = "1.0"
RESULT_COLUMNS = "frame_id,timestamp,presence,presence_confidence,target_count,posture,posture_confidence,calibration_status,quality".split(",")
META_KEYS = "schema_version recording_id seat_id subject_id session_id source_kind capture_start timestamp_unit timestamp_origin data_level raw_shape raw_dtype axis_order value_unit nominal_frame_rate_hz radar_config installation capture_notes".split()
ALGORITHM_KEYS = "schema_version recording_id algorithm_version parameter_version capabilities config thresholds window_s temporal_smoothing model_version calibrations".split()
POSTURES = {"reference", "forward", "backward", "unknown", "not_applicable"}
CALIBRATIONS = {"uncalibrated", "calibrating", "valid", "invalid"}
QUALITIES = {"ok", "low_signal", "invalid_frame"}


class ContractError(ValueError):
    pass


def fail(path, location, message):
    raise ContractError(f"{path} [{location}]: {message}")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, OSError) as e:
        fail(path, "JSON", str(e))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def recording_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", value):
        raise ContractError("recording_id must be a safe ASCII file identifier")
    return value


def paths(folder):
    folder = Path(folder)
    rid = recording_id(folder.name)
    return {k: folder / f"{rid}_{k}.{ext}" for k, ext in
            (("raw", "npz"), ("meta", "json"), ("result", "csv"), ("algorithm", "json"))}


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_raw(arrays, meta, path="raw.npz", meta_path="meta.json"):
    for key in ("timestamps", "frame_ids", "raw_data"):
        if key not in arrays:
            fail(path, key, "missing required array")
    t, ids, raw = (arrays[k] for k in ("timestamps", "frame_ids", "raw_data"))
    if t.dtype != np.dtype("float64") or t.ndim != 1 or not len(t):
        fail(path, "timestamps", "requires nonempty float64[N]")
    n = len(t)
    bad = np.flatnonzero(~np.isfinite(t) | (t < 0))
    if len(bad):
        fail(path, f"array row {bad[0] + 1}", "timestamp must be finite and nonnegative")
    bad = np.flatnonzero(np.diff(t) <= 0)
    if len(bad):
        fail(path, f"array row {bad[0] + 2}", "timestamps must be strictly increasing; no repair performed")
    if ids.dtype != np.dtype("int64") or ids.shape != (n,):
        fail(path, "frame_ids", "requires int64[N]")
    bad = np.flatnonzero(ids != np.arange(n, dtype=np.int64))
    if len(bad):
        fail(path, f"array row {bad[0] + 1}", "frame_ids must equal 0..N-1")
    if raw.ndim < 1 or raw.shape[0] != n or any(x == 0 for x in raw.shape):
        fail(path, "raw_data", "requires numeric [N,...] with nonempty axes")
    if raw.dtype.kind not in "iufc":
        fail(path, "raw_data", "requires numeric non-object dtype")
    for k, a in arrays.items():
        if a.dtype.hasobject:
            fail(path, k, "object/ragged arrays forbidden")
        if k not in {"timestamps", "frame_ids", "raw_data", "device_frame_ids"}:
            fail(path, k, "unrecognized v1 array")
    if "device_frame_ids" in arrays:
        d = arrays["device_frame_ids"]
        if d.shape != (n,) or d.dtype.kind not in "iu":
            fail(path, "device_frame_ids", "requires integer[N]; separate from saved frame index")
    if not isinstance(meta, dict):
        fail(meta_path, "root", "requires JSON object")
    for key in META_KEYS:
        if key not in meta:
            fail(meta_path, key, "missing required field")
    if meta["schema_version"] != SCHEMA:
        fail(meta_path, "schema_version", f"requires {SCHEMA}")
    recording_id(meta["recording_id"])
    for k in ("seat_id", "session_id", "capture_start", "data_level", "value_unit"):
        if not isinstance(meta[k], str) or not meta[k].strip():
            fail(meta_path, k, "must be explicitly defined before handover")
    if meta["subject_id"] is not None and not isinstance(meta["subject_id"], str):
        fail(meta_path, "subject_id", "anonymous string or null required")
    if meta["source_kind"] not in {"measured", "synthetic"}:
        fail(meta_path, "source_kind", "requires measured/synthetic")
    try:
        dt = datetime.fromisoformat(meta["capture_start"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("time zone required")
    except ValueError as e:
        fail(meta_path, "capture_start", str(e))
    if meta["timestamp_unit"] != "seconds" or meta["timestamp_origin"] != "recording_start":
        fail(meta_path, "timestamps", "unit=seconds, origin=recording_start required")
    if meta["raw_shape"] != list(raw.shape) or meta["raw_dtype"] != str(raw.dtype):
        fail(meta_path, "raw_shape/raw_dtype", "must match actual preserved payload")
    axes = meta["axis_order"]
    if not isinstance(axes, list) or len(axes) != raw.ndim or any(not isinstance(a, str) or not a for a in axes):
        fail(meta_path, "axis_order", "one explicit axis name for each raw dimension required")
    if axes[0] != "frame" or len(set(axes)) != len(axes):
        fail(meta_path, "axis_order", "first axis must be frame; names must be unique")
    if raw.dtype.kind == "c" and not meta.get("iq_representation"):
        fail(meta_path, "iq_representation", "complex payload requires explicit IQ convention")
    rate = meta["nominal_frame_rate_hz"]
    if rate is not None and (not _number(rate) or rate <= 0):
        fail(meta_path, "nominal_frame_rate_hz", "positive finite value or null required")
    for k in ("radar_config", "installation"):
        if not isinstance(meta[k], dict):
            fail(meta_path, k, "object required; unknown entries must be null")
    if not isinstance(meta["capture_notes"], str):
        fail(meta_path, "capture_notes", "string required")
    return n


def load_b(folder):
    p = paths(folder)
    meta = read_json(p["meta"])
    try:
        with np.load(p["raw"], allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
    except (ValueError, OSError) as e:
        fail(p["raw"], "NPZ", str(e))
    validate_raw(arrays, meta, p["raw"], p["meta"])
    if meta["recording_id"] != Path(folder).name:
        fail(p["meta"], "recording_id", "must match directory and file prefix")
    return arrays, meta


def save_b(root, meta, raw_data, timestamps, device_frame_ids=None):
    """Already decoded payload only. Never infer timestamps/layout from nominal rate."""
    rid = recording_id(meta["recording_id"])
    folder = Path(root) / rid
    arrays = {"timestamps": np.asarray(timestamps), "frame_ids": np.arange(len(timestamps), dtype=np.int64),
              "raw_data": np.asarray(raw_data)}
    if device_frame_ids is not None:
        arrays["device_frame_ids"] = np.asarray(device_frame_ids)
    validate_raw(arrays, meta)
    folder.mkdir(parents=True, exist_ok=False)
    p = paths(folder)
    with p["raw"].open("xb") as f:
        np.savez_compressed(f, **arrays)
    write_json(p["meta"], meta)
    return folder


def validate_algorithm(alg, meta, path="algorithm.json"):
    if not isinstance(alg, dict):
        fail(path, "root", "JSON object required")
    for k in ALGORITHM_KEYS:
        if k not in alg:
            fail(path, k, "missing required field")
    if alg["schema_version"] != SCHEMA or alg["recording_id"] != meta["recording_id"]:
        fail(path, "schema_version/recording_id", "does not match B metadata")
    for k in ("algorithm_version", "parameter_version"):
        if not isinstance(alg[k], str) or not alg[k]:
            fail(path, k, "nonempty string required")
    if not isinstance(alg["capabilities"], dict) or any(type(alg["capabilities"].get(k)) is not bool for k in ("presence", "posture")):
        fail(path, "capabilities", "presence/posture booleans required")
    if not isinstance(alg["config"], dict) or not isinstance(alg["thresholds"], dict):
        fail(path, "config/thresholds", "objects required")
    if not _number(alg["window_s"]) or alg["window_s"] < 0 or not isinstance(alg["temporal_smoothing"], str):
        fail(path, "window_s/temporal_smoothing", "nonnegative seconds and description required")
    if not isinstance(alg["calibrations"], list):
        fail(path, "calibrations", "list required")
    seen = set()
    intervals = []
    for i, c in enumerate(alg["calibrations"]):
        loc = f"calibrations[{i}]"
        keys = "calibration_id seat_id start_timestamp end_timestamp valid_from_timestamp invalid_from_timestamp invalidation_reason".split()
        if not isinstance(c, dict) or any(k not in c for k in keys):
            fail(path, loc, "missing calibration fields")
        if not isinstance(c["calibration_id"], str) or not c["calibration_id"] or c["calibration_id"] in seen:
            fail(path, loc, "calibration_id must be unique nonempty string")
        seen.add(c["calibration_id"])
        if c["seat_id"] != meta["seat_id"]:
            fail(path, loc, "seat_id mismatch")
        a, b, v, end = (c[k] for k in ("start_timestamp", "end_timestamp", "valid_from_timestamp", "invalid_from_timestamp"))
        if not all(_number(x) for x in (a, b, v)) or not (0 <= a <= b <= v):
            fail(path, loc, "require 0 <= start <= end <= valid_from; no retroactive validity")
        if end is not None and (not _number(end) or end < v):
            fail(path, loc, "invalid_from must be >= valid_from or null")
        if end is not None and not c["invalidation_reason"]:
            fail(path, loc, "invalidation reason required")
        if any(max(v, x) < min(end if end is not None else math.inf, y) for x, y in intervals):
            fail(path, loc, "overlapping active calibration intervals are ambiguous")
        intervals.append((v, end if end is not None else math.inf))
    return alg


def active_calibration(alg, t):
    for c in alg["calibrations"]:
        if c["valid_from_timestamp"] <= t and (c["invalid_from_timestamp"] is None or t < c["invalid_from_timestamp"]):
            return c
    return None


def _int(s, path, row, col):
    if not isinstance(s, str) or not re.fullmatch(r"-?\d+", s):
        fail(path, f"CSV row {row}, {col}", "integer required")
    return int(s)


def _float(s, path, row, col, optional=False):
    if optional and s == "":
        return None
    try:
        v = float(s)
        if not math.isfinite(v):
            raise ValueError()
        return v
    except (TypeError, ValueError):
        fail(path, f"CSV row {row}, {col}", "finite number required")


def load_result(path, arrays, meta, alg):
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != RESULT_COLUMNS:
            fail(path, "CSV row 1", "fixed nine-column header required, in specified order")
        for line, d in enumerate(reader, 2):
            if None in d or any(v is None for v in d.values()):
                fail(path, f"CSV row {line}", "wrong field count")
            r = dict(d)
            for k in ("frame_id", "presence", "target_count"):
                r[k] = _int(d[k], path, line, k)
            r["timestamp"] = _float(d["timestamp"], path, line, "timestamp")
            for k in ("presence_confidence", "posture_confidence"):
                r[k] = _float(d[k], path, line, k, True)
                if r[k] is not None and not 0 <= r[k] <= 1:
                    fail(path, f"CSV row {line}, {k}", "must be [0,1] or blank")
            loc = f"CSV row {line}"
            i = len(rows)
            if i >= len(arrays["timestamps"]):
                fail(path, loc, "more result rows than raw frames")
            if r["frame_id"] != int(arrays["frame_ids"][i]) or r["timestamp"] != float(arrays["timestamps"][i]):
                fail(path, loc, "frame_id/timestamp must exactly round-trip and match raw row")
            p, count, pose, cal, q = (r[k] for k in ("presence", "target_count", "posture", "calibration_status", "quality"))
            if p not in {-1, 0, 1} or count < -1 or pose not in POSTURES or cal not in CALIBRATIONS or q not in QUALITIES:
                fail(path, loc, "illegal enumeration or target_count")
            if p == 0 and (count != 0 or pose != "not_applicable"):
                fail(path, loc, "absent requires count=0, posture=not_applicable")
            if p == -1 and (count != -1 or pose != "unknown" or r["presence_confidence"] is not None):
                fail(path, loc, "unknown presence requires count=-1, posture=unknown, blank confidence")
            if p == 1 and (count == 0 or pose == "not_applicable" or (cal != "valid" and pose != "unknown")):
                fail(path, loc, "inconsistent present/calibration/posture combination")
            if pose in {"unknown", "not_applicable"} and r["posture_confidence"] is not None:
                fail(path, loc, "unknown/not_applicable posture requires blank confidence")
            if q != "ok" and (p != -1 or pose != "unknown" or r["presence_confidence"] is not None or r["posture_confidence"] is not None):
                fail(path, loc, "bad quality must be unknown with blank confidences")
            if not alg["capabilities"]["presence"] and p != -1:
                fail(path, loc, "presence evidence exceeds declared capabilities")
            if not alg["capabilities"]["posture"] and (cal != "uncalibrated" or pose in {"reference", "forward", "backward"}):
                fail(path, loc, "posture unsupported: use uncalibrated and unknown/not_applicable")
            if cal == "valid" and active_calibration(alg, r["timestamp"]) is None:
                fail(path, loc, "valid status has no current calibration record")
            rows.append(r)
    if len(rows) != len(arrays["timestamps"]):
        fail(path, f"CSV row {len(rows) + 2}", "missing result row; no padding performed")
    return rows


def write_result(path, rows):
    """Use Python repr(float) for binary64 round-trip; None becomes empty CSV field."""
    with Path(path).open("x", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_COLUMNS, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: (repr(float(row[k])) if k == "timestamp" else row[k]) for k in RESULT_COLUMNS})


def load_c(folder):
    arrays, meta = load_b(folder)
    p = paths(folder)
    alg = validate_algorithm(read_json(p["algorithm"]), meta, p["algorithm"])
    rows = load_result(p["result"], arrays, meta, alg)
    return rows, meta, alg

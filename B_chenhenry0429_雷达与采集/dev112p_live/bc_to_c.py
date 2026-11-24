"""Connect the B frame journal to C's live SeatDetector and optionally send rows to A."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

import numpy as np

from dev112p_live import SCHEMA, decode_arrays, follow_frames, json_line


RAW_AXIS = ["chip", "rx", "chirp", "range_fft_bin", "iq_component"]


def read_session(directory, timeout_s=20.0):
    path = directory / "session.json"
    deadline = time.monotonic() + timeout_s
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"B session metadata did not arrive: {path}")
        time.sleep(0.025)
    return json.loads(path.read_text(encoding="utf-8"))


def handshake(session, seat_id, installation):
    shape = session.get("raw_shape")
    if (session.get("schema_version") != SCHEMA or session.get("data_level") != "1dfft"
            or session.get("raw_dtype") != "<i2" or session.get("axis_order") != RAW_AXIS
            or not isinstance(shape, list) or len(shape) != 5 or shape[-1] != 2
            or any(type(size) is not int or size < 1 for size in shape)
            or session.get("valid_chirps_shape") != shape[:3]):
        raise ValueError("B session must declare an int16 DEV112P 1DFFT cube and matching mask")
    period = float(session["profile"]["frame_period_s"])
    if (not math.isfinite(period) or period <= 0
            or session.get("timestamp_source") != "nominal_device_counter"):
        raise ValueError("B session must declare the positive frame period and nominal device time")
    rid = session["recording_id"]
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", rid):
        raise ValueError("Invalid recording_id")
    shared = {
        "recording_id": rid,
        "seat_id": seat_id,
        "data_level": "1dfft",
        "radar_config": {
            "nominal_frame_period_s": period,
            "tx_polling": session["profile"]["tx_polling"],
            "range_bin_distance_m": None,
        },
        "installation": installation,
        "timestamp_unit": "seconds",
        "timestamp_origin": "recording_start",
        "timestamp_source": "nominal_device_counter",
        "replay": session["replay"],
    }
    b_meta = {**shared, "schema_version": "bc-devframe/0.1", "raw_shape": shape,
              "raw_dtype": "int16", "axis_order": RAW_AXIS, "valid_chirps_shape": shape[:3],
              "config_sha256": session["config_sha256"]}
    # The detector's constructor consumes the C projection shape/dtype, while
    # feed_dev112p accepts the unchanged raw cube and does the projection in C.
    c_meta = {**shared, "schema_version": "1.0", "source_kind": "measured",
              "raw_shape": [1, shape[3]], "raw_dtype": "complex64",
              "axis_order": ["frame", "range_bin"], "shape_scope": "one projected frame",
              "session_frame_count": None}
    return b_meta, c_meta


@dataclass(frozen=True)
class DevFrame:
    raw_frame: np.ndarray
    valid_chirps: np.ndarray
    frame_id: int
    timestamp: float
    observed_frame_id: int | None


def devframes(records, session, period):
    """Turn observed frame groups into nominal ticks. Gaps are unknown placeholders."""
    zero_raw = np.zeros(session["raw_shape"], dtype=np.int16)
    zero_valid = np.zeros(session["valid_chirps_shape"], dtype=bool)
    zero_raw.flags.writeable = False
    zero_valid.flags.writeable = False
    previous_tick, previous_device = -1, None
    for observed_id, record in enumerate(records):
        if record["frame_id"] != observed_id or record["timestamp_source"] != "nominal_device_counter":
            raise ValueError("B observed sequence or timestamp source changed within the session")
        raw, valid = decode_arrays(record, session)
        if int(valid.sum()) != record["valid_chirp_count"]:
            raise ValueError(f"Mask count disagrees with B observed frame {observed_id}")
        timestamp = float(record["timestamp"])
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("B timestamp is not a finite nonnegative nominal second")
        tick = round(timestamp / period)
        device = record["device_frame_id"]
        if (type(device) is not int or not 0 <= device < 16
                or not math.isclose(tick * period, timestamp, rel_tol=0, abs_tol=1e-8)):
            raise ValueError("B device counter or nominal timestamp is invalid")
        if previous_device is None:
            if tick != 0:
                raise ValueError("First observed frame must start at nominal tick 0")
        else:
            step = (device - previous_device) % 16
            if step == 0 or tick - previous_tick != step:
                raise ValueError("Device counter and nominal tick disagree. Start a new session.")
        for missing_tick in range(previous_tick + 1, tick):
            yield DevFrame(zero_raw, zero_valid, missing_tick, missing_tick * period, None)
        yield DevFrame(raw, valid, tick, timestamp, observed_id)
        previous_tick, previous_device = tick, device


def prepare(b_session, seat_id, installation, output, c_root, *, config_path=None):
    sys.path.insert(0, str(c_root.resolve()))
    from algos.online_detector import SeatDetector
    from tools.live_seat import write_algorithm_online

    if config_path is None:
        config_path = c_root / "config" / "M1_dev112p_v0.2.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    b_meta, c_meta = handshake(b_session, seat_id, installation)
    detector = SeatDetector(c_meta, m1_config=config)
    rid = c_meta["recording_id"]
    output.mkdir(parents=True, exist_ok=True)
    for name, description in (("devframe_meta", b_meta), ("meta", c_meta)):
        path = output / f"{rid}_{name}.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != description:
            raise ValueError(f"Prepared metadata differs for {path}; use a new output directory")
        if not path.exists():
            path.write_bytes(json_line(description))
    algorithm_path = output / f"{rid}_algorithm_online.json"
    write_algorithm_online(algorithm_path, rid, detector, config, None,
                           c_meta["radar_config"]["nominal_frame_period_s"], config_path.name)
    return detector, c_meta, algorithm_path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("journal", type=Path, help="B directory containing session.json and frames.ndjson")
    cli.add_argument("output", type=Path, help="online metadata and nine-field CSV output directory")
    cli.add_argument("--c-root", type=Path, required=True, help="C seat-detection directory")
    cli.add_argument("--config", type=Path, help="explicit C config file (default: M1 v1.2)")
    cli.add_argument("--seat-id", default="T01")
    cli.add_argument("--region-bins", type=int, nargs=2, metavar=("LO", "HI"))
    cli.add_argument("--main-bin", type=int)
    cli.add_argument("--prepare", action="store_true", help="write handshake and A metadata then exit")
    cli.add_argument("--post-to-a", action="store_true", help="send to A on this computer")
    cli.add_argument("--a-port", type=int, default=8766)
    args = cli.parse_args()
    if args.prepare and args.post_to_a:
        cli.error("--prepare and --post-to-a are separate steps")
    if not 1 <= args.a_port <= 65535:
        cli.error("--a-port is outside 1..65535")
    if (args.main_bin is not None and (args.region_bins is None
            or not args.region_bins[0] <= args.main_bin <= args.region_bins[1])):
        cli.error("--main-bin requires the containing --region-bins")
    token = os.environ.get("RADAR_A_FRAME_TOKEN")
    if args.post_to_a and not token:
        cli.error("set RADAR_A_FRAME_TOKEN to A's session token")
    installation = {}
    if args.region_bins is not None:
        installation["region_bins"] = args.region_bins
    if args.main_bin is not None:
        installation["main_bin"] = args.main_bin
    try:
        session = read_session(args.journal)
        detector, meta, algorithm = prepare(session, args.seat_id, installation,
                                            args.output, args.c_root, config_path=args.config)
        rid = meta["recording_id"]
        print(f"A metadata ready: {args.output / (rid + '_meta.json')} and {algorithm}", flush=True)
        if args.prepare:
            return 0

        from algos.online_detector import RESULT_COLUMNS
        from tools.live_seat import post_row

        period = meta["radar_config"]["nominal_frame_period_s"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{args.a_port}/api/frame"
        gaps = 0
        with (args.output / f"{rid}_result.csv").open("x", encoding="utf-8", newline="") as sink, (
                args.output / f"{rid}_gap_diagnostics.ndjson").open("xb") as diagnostics:
            writer = csv.DictWriter(sink, fieldnames=RESULT_COLUMNS)
            writer.writeheader()
            sink.flush()
            for frame in devframes(follow_frames(args.journal), session, period):
                row = detector.feed_dev112p(frame.raw_frame, frame.valid_chirps,
                                            frame.timestamp, frame_id=frame.frame_id)
                if args.post_to_a:
                    post_row(opener, url, token, row)
                writer.writerow(row)
                sink.flush()
                if frame.observed_frame_id is None:
                    gaps += 1
                    diagnostics.write(json_line({"frame_id": frame.frame_id, "timestamp": frame.timestamp,
                                                 "reason": "inferred_modulo16_gap_minimum",
                                                 "raw_received": False}))
                    diagnostics.flush()
        print(f"{rid}: {detector.frame_count} rows, {detector.processed_count} processed, "
              f"{gaps} gap placeholders")
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"B-to-C session failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

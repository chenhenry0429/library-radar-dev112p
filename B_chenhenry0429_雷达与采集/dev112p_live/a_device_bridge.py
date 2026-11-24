"""Connect a new DEV112P ONELAB recording to A's device launcher."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Lock, Thread
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from bc_to_c import devframes, prepare, read_session
from dev112p_live import Profile, describe_session, follow_frames, next_recording, produce, write_json


INSTALLATION = {"region_bins": [1, 3], "main_bin": 2}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def prepare_session(session, directory, args):
    detector, meta, algorithm_path = prepare(
        session, "T01", INSTALLATION, directory, args.c_root, config_path=args.config,
    )
    meta["device_profile"] = session["profile"]
    algorithm = read_json(algorithm_path)
    algorithm["time_base"] = {
        "source": "nominal_frame_period",
        "frame_period_s": meta["radar_config"]["nominal_frame_period_s"],
    }
    write_json(directory / f"{meta['recording_id']}_meta.json", meta)
    write_json(algorithm_path, algorithm)
    return detector, meta, algorithm


def configure(args):
    if args.device_config.exists():
        raise FileExistsError(f"Device config already exists: {args.device_config}")
    config = args.reference_ini.read_bytes()
    profile = Profile.from_ini(config)
    rid = "DEV112P_T01_reference"
    templates = args.device_config.parent / "dev112p_device"
    prepare_session(describe_session(profile, config, rid), templates, args)
    if args.onelab_control is not None:
        read_json(args.onelab_control)
    command = [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()),
               "--recordings", str(args.recordings), "--output", str(args.output),
               "--c-root", str(args.c_root), "--config", str(args.config)]
    if args.onelab_control is not None:
        command += ["--onelab-control", str(args.onelab_control)]
    write_json(args.device_config, {
        "command": command,
        "meta": f"dev112p_device/{rid}_meta.json",
        "algorithm": f"dev112p_device/{rid}_algorithm_online.json",
        "managed_acquisition": True,
        "allow_nominal_live_time": True,
        "first_frame_timeout_s": args.first_frame_timeout,
        "graceful_stop_timeout_s": 12 if args.onelab_control is not None else 2.5,
    })
    print(f"A 配置已生成：{args.device_config}", flush=True)


def control_onelab(path, action):
    if sys.platform != "win32":
        raise RuntimeError("ONELAB 一键控制需要 Windows")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    helper = Path(__file__).with_name("onelab_control.ps1")
    try:
        subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-File", str(helper),
             "-Action", action, "-Config", str(path)],
            check=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"ONELAB {action} 失败，请查看启动窗口") from error


def watch_stop(path, stop, stop_capture):
    while not stop.wait(0.05):
        if path.is_file():
            stop_capture()
            return


def forward_frames(b_dir, c_dir, args, url, token, stop, stop_file, reference_algo):
    session = read_session(b_dir, timeout_s=4)
    detector, meta, algorithm = prepare_session(session, c_dir, args)
    if algorithm["parameter_version"] != reference_algo["parameter_version"]:
        raise ValueError("C 参数与 A 的会话配置不同，请重新生成本机配置")
    from algos.online_detector import RESULT_COLUMNS
    from tools.live_seat import post_row

    opener = build_opener(ProxyHandler({}))
    period = meta["radar_config"]["nominal_frame_period_s"]
    accepted = 0
    with (c_dir / f"{meta['recording_id']}_result.csv").open(
            "x", encoding="utf-8", newline="") as sink:
        writer = csv.DictWriter(sink, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for frame in devframes(follow_frames(b_dir), session, period):
            if stop.is_set():
                break
            row = detector.feed_dev112p(frame.raw_frame, frame.valid_chirps,
                                        frame.timestamp, frame.frame_id)
            try:
                frame_id = post_row(opener, url, token, row, retry_max=1, retry_base=0.2)
            except RuntimeError:
                if stop.is_set() or stop_file.is_file():
                    break
                raise
            accepted += 1
            writer.writerow(row)
            sink.flush()
            if accepted == 1 or accepted % 50 == 0:
                print(f"B→C→A 已确认 frame={frame_id}", flush=True)
    return accepted


def capture(args):
    url, token = os.environ["RADAR_A_FRAME_URL"], os.environ["RADAR_A_FRAME_TOKEN"]
    address = urlsplit(url)
    if (address.scheme != "http" or address.hostname != "127.0.0.1" or
            address.path != "/api/frame" or not address.port or address.username or
            address.password or address.query or address.fragment or not token):
        raise ValueError("需要 A 下发的本机收帧地址和令牌")
    rid = os.environ["RADAR_A_RECORDING_ID"]
    if not rid.startswith("DEV112P_") or not rid.removeprefix("DEV112P_").isalnum():
        raise ValueError("无效的 DEV112P 会话编号")
    reference_meta = read_json(os.environ["RADAR_A_META"])
    reference_algo = read_json(os.environ["RADAR_A_ALGORITHM"])
    stop_file = Path(os.environ["RADAR_A_STOP_FILE"])
    stop, control_lock = Event(), Lock()
    managed_recording = False
    stop_error = None

    def stop_capture():
        nonlocal managed_recording, stop_error
        with control_lock:
            if managed_recording:
                managed_recording = False
                try:
                    control_onelab(args.onelab_control, "Stop")
                except (OSError, RuntimeError) as error:
                    stop_error = error
                    print(f"停止采集失败：{error}", file=sys.stderr, flush=True)
            stop.set()

    signal.signal(signal.SIGINT, lambda *_: stop_file.touch())
    Thread(target=watch_stop, args=(stop_file, stop, stop_capture), daemon=True).start()

    try:
        args.recordings.mkdir(parents=True, exist_ok=True)
        previous = set(args.recordings.glob("*/RadarData_1.dat"))
        if args.onelab_control is None:
            print(f"等待 ONELAB 新录制：{args.recordings}", flush=True)
        else:
            with control_lock:
                if stop.is_set() or stop_file.is_file():
                    return
                control_onelab(args.onelab_control, "Start")
                managed_recording = True
        capture_recording(args, rid, url, token, reference_meta, reference_algo,
                          previous, stop, stop_file, stop_capture)
    finally:
        stop_capture()
    if stop_error is not None:
        raise stop_error


def capture_recording(args, rid, url, token, reference_meta, reference_algo,
                      previous, stop, stop_file, stop_capture):
    source = next_recording(args.recordings, previous, stop)
    if source is None:
        return
    config = (source / "FILE_Radar_Config.ini").read_bytes()
    profile = Profile.from_ini(config)
    if describe_session(profile, config, rid)["profile"] != reference_meta["device_profile"]:
        raise ValueError("本次 ONELAB 配置与 A 的 DEV112P 模板不同，请确认录制配置")
    output = args.output / rid
    b_dir, c_dir = output / "B", output / "C"
    with ThreadPoolExecutor(max_workers=1) as pool:
        producer = pool.submit(produce, source, b_dir, rid, stop)
        try:
            accepted = forward_frames(b_dir, c_dir, args, url, token, stop, stop_file,
                                      reference_algo)
        finally:
            stop_capture()
        summary = producer.result()
    write_json(output / "A_delivery.json", {
        "recording_id": rid, "B_frames": summary["frames"], "A_accepted": accepted,
        "B_end_reason": summary["reason"], "timestamp_source": "nominal_device_counter",
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, required=True, help="ONELAB save directory")
    parser.add_argument("--output", type=Path, required=True, help="B and C capture output")
    parser.add_argument("--c-root", type=Path, required=True, help="C seat-detection directory")
    parser.add_argument("--config", type=Path, required=True, help="C M1 JSON config")
    parser.add_argument("--device-config", type=Path, help="create A's local JSON at this path")
    parser.add_argument("--reference-ini", type=Path, help="approved ONELAB configuration")
    parser.add_argument("--first-frame-timeout", type=float, default=60)
    parser.add_argument("--onelab-control", type=Path,
                        help="local Windows ONELAB executable, window and recording control selectors")
    args = parser.parse_args()
    if (args.device_config is None) != (args.reference_ini is None):
        parser.error("--device-config and --reference-ini must be supplied together")
    for name in ("recordings", "output", "c_root", "config"):
        setattr(args, name, getattr(args, name).resolve())
    try:
        if args.onelab_control is not None:
            args.onelab_control = args.onelab_control.resolve(strict=True)
        if args.device_config is not None:
            configure(args)
        else:
            capture(args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"DEV112P 采集入口失败：{error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

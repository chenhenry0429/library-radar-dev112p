"""A 端实时入口 v0.4.0。运行 python live_monitor.py --help。"""
import argparse
import csv
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
from secrets import token_urlsafe
import sys
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from contract import RESULT_COLUMNS, parse_result_row, read_json, recording_id, validate_algorithm
from offline_state_machine import StateMachine, validate_config
from live_feedback import LiveFeedback
from radar_launcher import RadarLauncher

DEMO_ID = "20261103_PDEMO_S01_lifecycle_01"
DEMO = ROOT.parent / "DEMO_ONLY" / "release_demo" / DEMO_ID / f"{DEMO_ID}_result.csv"
ASSETS = {
    "/": ("live_viewer/monitor.html", "text/html"),
    "/monitor.css": ("live_viewer/monitor.css", "text/css"),
    "/monitor.js": ("live_viewer/monitor.js", "text/javascript"),
    "/student/": ("live_viewer/student.html", "text/html"),
    "/student/student.css": ("live_viewer/student.css", "text/css"),
    "/student/student.js": ("live_viewer/student.js", "text/javascript"),
    "/live/": ("live_viewer/index.html", "text/html"),
    "/live.js": ("live_viewer/live.js", "text/javascript"),
    "/live.css": ("live_viewer/live.css", "text/css"),
    "/offline/": ("offline_viewer/index.html", "text/html"),
    **{f"/offline/{name}": (f"offline_viewer/{name}", "text/javascript") for name in (
        "demo-data.js", "acceptance-data.js", "seat-display.js"
    )},
}


class LiveMonitor:
    def __init__(self, meta, algorithm, config, *, replay_rows=None, speed=1., nominal_period=None):
        self.machine = StateMachine(meta, algorithm, config)
        self.meta, self.algorithm, self.config = meta, algorithm, config
        self.replay_rows, self.speed = replay_rows, speed
        self.nominal_period = nominal_period
        self.lock, self.stop = Lock(), Event()
        self.worker = None
        self.latest, self.received_at = None, None
        self.feedback = LiveFeedback(config, speed)
        self.phase = "waiting"

    def push(self, payload):
        with self.lock:
            previous = self.latest
            expected = previous["frame_id"] + 1 if previous else 0
            if set(payload) != set(RESULT_COLUMNS):
                raise ValueError("帧须包含结果接口的九个字段")
            if any(type(value) not in (str, int, float, type(None)) for value in payload.values()):
                raise ValueError("帧字段须为字符串、数值或 null")
            fields = {key: "" if value is None else str(value) for key, value in payload.items()}
            row = parse_result_row(fields, self.algorithm, "live input", expected + 2)
            validate_position(row, expected, previous)
            if self.nominal_period is not None and not math.isclose(
                row["timestamp"], expected * self.nominal_period, rel_tol=0., abs_tol=1e-8
            ):
                raise ValueError("名义时间须与声明的帧周期和帧号一致")
            now = monotonic()
            self.feedback.observe_silence(previous, self.received_at, now, self.phase)
            interrupted = self.replay_rows is None and self.feedback.interrupted
            self.machine.step(row, interrupted=interrupted)
            self.latest = self.machine.timeline.pop()
            self.feedback.accept(previous, self.latest, row, self.machine.events)
            self.machine.events.clear()
            del self.machine.sessions[:-1]
            self.received_at, self.phase = now, "receiving"
            return row["frame_id"]

    def replay(self):
        start = monotonic()
        origin = self.replay_rows[0]["timestamp"]
        for row in self.replay_rows:
            due = start + (row["timestamp"] - origin) / self.speed
            if self.stop.wait(max(0., due - monotonic())):
                return
            self.push(row)
        with self.lock:
            self.phase = "ended"

    def snapshot(self):
        with self.lock:
            if self.replay_rows is not None and self.worker is None:
                self.worker = Thread(target=self.replay, daemon=True)
                self.worker.start()
            now = monotonic()
            age = None if self.received_at is None else now - self.received_at
            self.feedback.observe_silence(self.latest, self.received_at, now, self.phase)
            phase = "interrupted" if self.phase == "receiving" and self.feedback.interrupted else self.phase
            state = self.latest
            available = bool(phase == "receiving" and state and not state["occupancy_pending"]
                             and not state["review_required"] and state["occupancy_state"] in ("idle", "released"))
            return dict(mode="replay" if self.replay_rows is not None else "live",
                        source_kind=self.meta["source_kind"], phase=phase, available=available,
                        seat_id=self.meta["seat_id"], recording_id=self.meta["recording_id"],
                        algorithm_version=self.algorithm["algorithm_version"],
                        capabilities=[key for key, enabled in self.algorithm["capabilities"].items() if enabled],
                        nominal_time=self.nominal_period is not None, state=state,
                        silence_review=self.feedback.review, last_frame_age_s=age,
                        parameter_version=self.algorithm["parameter_version"], config=self.config,
                        known_issues=self.algorithm.get("known_issues", []),
                        evidence=self.feedback.evidence, events=list(self.feedback.events))


def handler_for(monitor, token, launcher=None):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5.)
            super().setup()

        def respond(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, payload, status=200):
            self.respond(json.dumps(payload, ensure_ascii=False).encode(), "application/json", status)

        def local_host(self):
            port = self.server.server_port
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):
            if not self.local_host():
                self.send_json({"error": "仅接受本机监测地址"}, 403)
                return
            if self.path == "/api/state":
                snapshot = (launcher or monitor).snapshot()
                if launcher is None:
                    snapshot["detector"] = "external" if token else "replay"
                self.send_json(snapshot)
            elif self.path in ASSETS:
                name, mime = ASSETS[self.path]
                self.respond((ROOT / name).read_bytes(), mime)
            else:
                self.send_json({"error": "地址不存在"}, 404)

        def do_POST(self):
            if not self.local_host():
                self.send_json({"error": "仅接受本机监测地址"}, 403)
                return
            if self.path in ("/api/detection/start", "/api/detection/stop"):
                origin = f"http://{self.headers['Host']}"
                if self.headers.get("Origin") != origin or self.headers.get("X-Radar-Control") != "1":
                    self.send_json({"error": "仅接受本机页面操作"}, 403)
                    return
                if launcher is None:
                    self.send_json({"detector": "external" if token else "replay"}, 409)
                    return
                detector = launcher.start(self.server.server_port) if self.path.endswith("/start") else launcher.stop()
                self.send_json({"detector": detector})
                return
            if self.path != "/api/frame":
                self.send_json({"error": "地址不存在"}, 404)
                return
            if token is None and launcher is None:
                self.send_json({"error": "回放模式下请使用已加载的录制"}, 409)
                return
            authorization = self.headers.get("Authorization", "").encode()
            if launcher is None and not compare_digest(authorization, f"Bearer {token}".encode()):
                self.send_json({"error": "需要接入令牌"}, 401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 4096:
                    raise ValueError("需要不超过 4096 字节的 JSON 帧")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != set(RESULT_COLUMNS):
                    raise ValueError("帧须包含现有结果接口的九个字段")
                frame_id = launcher.push(payload, authorization) if launcher is not None else monitor.push(payload)
            except PermissionError:
                self.send_json({"error": "需要当前检测会话的接入令牌"}, 401)
                return
            except (ValueError, TypeError, TimeoutError) as error:
                self.send_json({"error": str(error)}, 400)
                return
            self.send_json({"accepted_frame_id": frame_id})

        def log_message(self, format, *args):
            pass

    return Handler


def validate_position(row, expected, previous):
    stamp = row["timestamp"]
    if row["frame_id"] != expected or stamp < 0 or (previous and stamp <= previous["timestamp"]):
        raise ValueError(f"预期帧号 {expected}，时间须非负且严格递增")


def load_recorded_rows(path, algorithm, nominal_period):
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != RESULT_COLUMNS:
            raise ValueError("回放文件须使用共享接口的固定九列表头")
        for row_number, fields in enumerate(reader, 2):
            row = parse_result_row(fields, algorithm, path, row_number)
            validate_position(row, len(rows), rows[-1] if rows else None)
            if nominal_period is not None and not math.isclose(
                row["timestamp"], row["frame_id"] * nominal_period, rel_tol=0., abs_tol=1e-8
            ):
                raise ValueError("回放时间与声明的名义帧周期不一致")
            rows.append(row)
    if not rows:
        raise ValueError("回放文件没有数据行")
    return rows


def load_metadata(meta_path, algorithm_path, *, replay, allow_nominal_live=False):
    meta = read_json(meta_path)
    algorithm = read_json(algorithm_path)
    recording_id(meta["recording_id"])
    if meta["schema_version"] != "1.0" or not isinstance(meta["seat_id"], str) or not meta["seat_id"]:
        raise ValueError("需要 v1 采集描述和座位编号")
    if meta["source_kind"] not in ("synthetic", "measured"):
        raise ValueError("source_kind 须为 synthetic 或 measured")
    time_base = algorithm.get("time_base") or {}
    nominal = time_base.get("source") == "nominal_frame_period"
    period = None
    if nominal and replay and meta["timestamp_unit"] in ("s", "seconds"):
        # P01 v0.2 使用旧 A 侧描述。只在名义时间回放入口转译。
        period = float(time_base["frame_period_s"])
        if not math.isfinite(period) or period <= 0:
            raise ValueError("名义帧周期须为正数")
        if isinstance(algorithm["capabilities"], list):
            algorithm["capabilities"] = {key: key in algorithm["capabilities"] for key in ("presence", "posture")}
            algorithm["thresholds"] = algorithm.get("config", {}).get("thresholds", {})
            algorithm["temporal_smoothing"] = algorithm["smoothing"]
            algorithm["calibrations"] = [
                {**cal, "invalidation_reason": cal.get("invalid_reason")} for cal in algorithm["calibrations"]
            ]
    elif nominal and allow_nominal_live and (
        meta["timestamp_unit"], meta["timestamp_origin"], meta.get("timestamp_source")
    ) == ("seconds", "recording_start", "nominal_device_counter"):
        period = float(time_base["frame_period_s"])
        if not math.isfinite(period) or period <= 0:
            raise ValueError("名义帧周期须为正数")
    elif nominal or (meta["timestamp_unit"], meta["timestamp_origin"]) != ("seconds", "recording_start"):
        raise ValueError("在线接入和标准回放要求 seconds / recording_start，名义时间仅用于回放")
    return meta, validate_algorithm(algorithm, meta, algorithm_path), period


def load_device_session(path, config):
    settings = read_json(path)
    meta, algorithm, period = load_metadata(
        path.parent / settings["meta"], path.parent / settings["algorithm"], replay=False,
        allow_nominal_live=settings.get("allow_nominal_live_time") is True,
    )
    if meta["source_kind"] != "measured":
        raise ValueError("现场检测须使用实测会话描述")
    if settings.get("managed_acquisition") is True:
        rid = f"DEV112P_{uuid4().hex}"
        meta["recording_id"] = algorithm["recording_id"] = rid
    return settings, LiveMonitor(meta, algorithm, config, nominal_period=period)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, help="按时间间隔发送现有 *_result.csv，默认使用合成样例")
    parser.add_argument("--live", action="store_true", help="通过本机 HTTP 接收 C 的逐帧结果")
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--algorithm", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "offline_config.json")
    parser.add_argument("--speed", type=float, default=1., help="仅改变回放速度")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--device-config", type=Path, default=ROOT / "radar_device.local.json",
                        help="首页开始检测使用的本机启动配置")
    args = parser.parse_args()
    if not math.isfinite(args.speed) or not 0.1 <= args.speed <= 20:
        parser.error("回放速度须在 0.1 到 20 之间")
    if args.live and (args.replay or not args.meta or not args.algorithm or args.speed != 1):
        parser.error("--live 需要 --meta 和 --algorithm，且不能同时使用回放选项")
    try:
        replay = args.replay or DEMO
        if not args.live and not replay.name.endswith("_result.csv"):
            parser.error("回放文件须以 _result.csv 结尾")
        prefix = str(replay).removesuffix("_result.csv")
        meta, algorithm, nominal_period = load_metadata(
            args.meta or prefix + "_meta.json", args.algorithm or prefix + "_algorithm.json", replay=not args.live
        )
        config = validate_config(read_json(args.config))
        rows = None if args.live else load_recorded_rows(replay, algorithm, nominal_period)
        monitor = LiveMonitor(meta, algorithm, config, replay_rows=rows, speed=args.speed, nominal_period=nominal_period)
        token = (os.environ.get("RADAR_A_FRAME_TOKEN") or token_urlsafe(24)) if args.live else None
        launcher = None if args.live or args.replay else RadarLauncher(
            monitor, args.device_config, lambda path: load_device_session(path, config)
        )
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(monitor, token, launcher)) as server:
            print(f"打开 http://127.0.0.1:{server.server_port}/", flush=True)
            if args.live and "RADAR_A_FRAME_TOKEN" not in os.environ:
                print(f"本机 C 接入令牌：{token}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                if launcher is not None:
                    launcher.stop()
                monitor.stop.set()
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"启动失败：{error}\n")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""selftest_field —— 现场入口 radar_field 的端到端自测（无硬件）。

链路：fake_b_stream（合成 DevFrame JSONL）→ radar_field（投影+识别+HTTP）
→ 本脚本内置的 stub A（/api/frame + /api/state）→ 断言 result.csv / accepted。

覆盖：
  1. 生成 algorithm_online.json，recording_id / capabilities 正确；
  2. 430 帧全链路：帧号连续、POST 全部 accepted、CSV 行数/时间戳正确；
  3. 行为：20 s 前无人、静坐段有人、走后判空（合成场景）；
  4. 零掩码帧 → invalid_frame(presence=-1)，不推进状态机；
  5. recording_id 不一致 → 非零退出；
  6. 退出时 B 子进程被终止（alive 文件停止更新）。

运行：python -X utf8 tools/selftest_field.py
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.request import ProxyHandler, build_opener
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import numpy as np

CONFIG = os.path.join(ROOT, "config", "M1_dev112p_v1.2.1.json")
RID = "T01_fake_field"
TOKEN = "selftest-token"
FRAMES = 430


class StubA:
    def __init__(self):
        self.rows = []
        self.last = -1
        self.token_ok = True
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/api/frame":
                    return self._json(404, {"error": "not found"})
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    outer.token_ok = False
                    return self._json(401, {"error": "unauthorized"})
                n = int(self.headers.get("Content-Length") or 0)
                row = json.loads(self.rfile.read(n).decode())
                if row.get("frame_id") != outer.last + 1:
                    return self._json(400, {"error": "frame_id gap"})
                outer.rows.append(row)
                outer.last = row["frame_id"]
                self._json(200, {"accepted_frame_id": row["frame_id"]})

            def do_GET(self):
                if self.path != "/api/state":
                    return self._json(404, {"error": "not found"})
                self._json(200, {"state": {"frame_id": outer.last}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/api/frame"


def write_meta(path):
    meta = {
        "schema_version": "1.0",
        "recording_id": RID,
        "seat_id": "T01",
        "source_kind": "synthetic",
        "data_level": "1dfft",
        "raw_shape": [2, 2, 64, 8, 2],  # cube: chip, rx, chirp, range_fft_bin, iq
        "raw_dtype": "int16",
        "axis_order": ["chip", "rx", "chirp", "range_fft_bin", "iq_component"],
        "radar_config": {"nominal_frame_period_s": 0.1935},
        "installation": {"region_bins": [1, 3], "main_bin": 2},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def run_field(tmp, stub, extra=(), meta=None, algorithm=None, max_frames=FRAMES,
              source_extra=()):
    meta = meta or os.path.join(tmp, "meta.json")
    algorithm = algorithm or os.path.join(tmp, "algorithm_online.json")
    out = os.path.join(tmp, "out")
    os.makedirs(out, exist_ok=True)
    if stub is not None:  # 每轮是新的 A 会话：帧号从 0 开始
        stub.last = -1
        stub.rows = []
        stub.token_ok = True
    pid = os.path.join(tmp, "b.pid")
    alive = os.path.join(tmp, "b.alive")
    if os.path.exists(alive):
        os.remove(alive)
    src = (f'"{sys.executable}" "{os.path.join(HERE, "fake_b_stream.py")}" '
           f'--frames {FRAMES} --pid-file "{pid}" --alive-file "{alive}" '
           + " ".join(source_extra))
    env = dict(os.environ)
    env["RADAR_A_FRAME_URL"] = stub.url if stub else ""
    env["RADAR_A_FRAME_TOKEN"] = TOKEN
    cmd = [sys.executable, "-X", "utf8", os.path.join(HERE, "radar_field.py"),
           "--meta", meta, "--algorithm", algorithm, "--config", CONFIG,
           "--out", out, "--source-cmd", src, "--emit-scores",
           "--max-frames", str(max_frames)] + list(extra)
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return p, out, alive


def read_result(path):
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def check_basic(tmp, stub):
    p, out, alive = run_field(tmp, stub)
    print(p.stdout[-1200:])
    assert p.returncode == 0, f"radar_field rc={p.returncode}\n{p.stderr}"
    rows = read_result(os.path.join(out, f"{RID}_result.csv"))
    assert len(rows) == FRAMES, len(rows)
    assert [int(r["frame_id"]) for r in rows] == list(range(FRAMES))
    ts = [float(r["timestamp"]) for r in rows]
    assert all(b > a for a, b in zip(ts, ts[1:])), "timestamps not increasing"
    assert len(stub.rows) == FRAMES, f"posted {len(stub.rows)}"
    assert stub.token_ok, "bearer token not checked"
    # 行为断言（合成场景：空座 0–20 s，静坐 20–60 s）
    pr = np.array([int(r["presence"]) for r in rows])
    assert pr[ts.index(min(ts, key=lambda x: abs(x - 10))) : int(20 / 0.1935)].sum() == 0, \
        "opening false alarm"
    sit = pr[int(25 / 0.1935): int(58 / 0.1935)]
    assert sit.mean() > 0.95, f"sit hold {sit.mean():.2f}"
    assert (pr[int(65 / 0.1935):] == 0).any(), "never released after walk-away"
    assert pr[int(80 / 0.1935):].sum() == 0, "still occupied 20 s after walk-away"
    # B 子进程应被终止：alive 文件不再更新
    assert os.path.exists(alive)
    m1 = os.path.getmtime(alive)
    time.sleep(1.2)
    m2 = os.path.getmtime(alive)
    assert m1 == m2, "fake B still alive after radar_field exit"
    print("[PASS] field E2E: 430 frames, accepted, behavior + child cleanup OK")


def check_zero_mask(tmp, stub):
    p, out, alive = run_field(tmp, stub, source_extra=("--zero-mask-at", "100"))
    assert p.returncode == 0, p.stdout[-800:]
    rows = read_result(os.path.join(out, f"{RID}_result.csv"))
    r = rows[100]
    assert r["quality"] == "invalid_frame" and r["presence"] == "-1", r
    # 状态机不被零掩码帧推进：100 帧前后仍为同态
    assert rows[99]["presence"] == rows[101]["presence"], "invalid frame advanced state"
    print("[PASS] zero-mask frame -> invalid_frame, state machine untouched")


def check_bad_recording_id(tmp, stub):
    bad = os.path.join(tmp, "bad_algorithm.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"recording_id": "OTHER", "capabilities": {"presence": True}}, f)
    p, out, alive = run_field(tmp, stub, algorithm=bad, max_frames=10)
    assert p.returncode != 0, "mismatched recording_id must fail"
    assert "recording_id" in (p.stdout + p.stderr), p.stdout[-500:]
    print("[PASS] recording_id mismatch rejected")


def check_emit_algorithm(tmp):
    meta = os.path.join(tmp, "meta.json")
    algo = os.path.join(tmp, "algo.json")
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(HERE, "radar_field.py"), "--meta", meta,
                        "--algorithm", algo, "--config", CONFIG, "--emit-algorithm"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr
    doc = json.load(open(algo, encoding="utf-8"))
    assert doc["recording_id"] == RID
    assert doc["capabilities"] == {"presence": True, "posture": False}, doc["capabilities"]
    print("[PASS] algorithm_online.json generated (rid + capabilities)")


def check_a_service(tmp):
    a_root = Path(HERE).parents[2] / "A_stone140_应用与业务"
    sys.path.insert(0, str(a_root))
    try:
        from live_monitor import LiveMonitor, handler_for, load_metadata, read_json, validate_config
    finally:
        sys.path.pop(0)

    meta_path, algo_path = Path(tmp) / "meta.json", Path(tmp) / "algo.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(timestamp_unit="seconds", timestamp_origin="recording_start")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    meta, algorithm, _ = load_metadata(meta_path, algo_path, replay=False)
    config = validate_config(read_json(a_root / "offline_config.json"))
    monitor = LiveMonitor(meta, algorithm, config)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(monitor, TOKEN)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            endpoint = SimpleNamespace(url=origin + "/api/frame")
            result, out, _ = run_field(tmp, endpoint, algorithm=str(algo_path),
                                       source_extra=("--zero-mask-at", "100"))
            assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
            opener = build_opener(ProxyHandler({}))
            with opener.open(origin + "/api/state", timeout=5) as response:
                snapshot = json.load(response)
            rows = read_result(os.path.join(out, f"{RID}_result.csv"))
            assert len(rows) == FRAMES and snapshot["state"]["frame_id"] == FRAMES - 1
            assert rows[100]["quality"] == "invalid_frame"
            assert snapshot["mode"] == "live" and snapshot["source_kind"] == "synthetic"
            assert snapshot["capabilities"] == ["presence"]
            assert any(event["event_type"] == "occupancy_change" and event["to_state"] == "in_use"
                       for event in snapshot["events"]), snapshot["events"]
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[PASS] real A HTTP service accepted 430 frames, validated the zero-mask row and displayed occupancy")


def main():
    with TemporaryDirectory(prefix="field-test-") as tmp:
        write_meta(os.path.join(tmp, "meta.json"))
        check_emit_algorithm(tmp)
        stub = StubA()
        stub.start()
        try:
            check_basic(tmp, stub)
            check_zero_mask(tmp, stub)
            check_bad_recording_id(tmp, stub)
        finally:
            stub.stop()
            stub.httpd.server_close()
        check_a_service(tmp)
    print("selftest_field: all checks passed")


if __name__ == "__main__":
    main()

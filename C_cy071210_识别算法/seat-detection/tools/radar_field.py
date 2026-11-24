# -*- coding: utf-8 -*-
"""radar_field —— C 现场入口（B 实时帧流 → SeatDetector → A 本机服务）。

对应 A 的《2026-11-24 A_B_C 一键检测真机联调》里 C 的交付项：
把 B 的实时帧流接到 `SeatDetector.feed_dev112p`，前台运行，复用九字段结果与
串行 POST 回执；每轮从帧 0 开始；**雷达停止后由本入口释放采集资源**（终止
B 子进程、关闭管道、落盘结果）。

两种启动方式
------------
A 页面「开始检测」按 `radar_device.local.json` 的 `command` 启动本程序，
并注入环境变量：`RADAR_A_FRAME_URL`、`RADAR_A_FRAME_TOKEN`、`RADAR_A_META`、
`RADAR_A_ALGORITHM`。现场典型命令：

    python -X utf8 tools/radar_field.py ^
        --source-cmd "\"C:\\Program Files\\Python312\\python.exe\" B目录\\detect_live.py" ^
        --config config/M1_dev112p_v1.2.1.json --emit-scores

也可以不带 `--source-cmd`，从 stdin 读 B 的输出（管道方式）：

    B程序 --jsonl | python -X utf8 tools/radar_field.py --config ... --emit-scores

帧格式（B→C，JSON Lines，一行一个）
----------------------------------
    {"type":"frame","frame_id":0,"timestamp":0.0,
     "raw_frame": [[[[[...]]]]],          # int16 (chip,rx,chirp,range_fft_bin,iq)
     "valid_chirps": [[[...]]]}           # bool  (chip,rx,chirp)

也支持紧凑编码（大帧推荐）：
    {"type":"frame","frame_id":0,"timestamp":0.0,
     "raw_frame_b64":"<int16 little-endian, C order>","raw_shape":[2,2,64,8,2],
     "valid_chirps_b64":"<uint8 0/1, C order>","valid_shape":[2,2,64]}

- `frame_id` 必须从 0 起严格连续；跳号即报错（协议错乱，需重启 A/C 会话）。
- `timestamp` 为采集起点相对秒、严格递增；缺省用 `frame_id × nominal_frame_period_s`。
- 丢帧/坏帧请发送 **全零 `valid_chirps`** 的同号帧：C 输出 `invalid_frame`
  （presence=-1）且不推进状态机，与 B↔C 帧级提案一致。
- 可选 `{"type":"meta", ...}` 行会被忽略；若带 `recording_id` 则必须与 meta 文件一致。

退出与资源释放
--------------
EOF / Ctrl-C / SIGTERM / 子进程退出都会进入 `finally`：
 1) 若 B 子进程仍在运行 → terminate → 等 5 s → kill；
 2) 关闭管道；
 3) 写 `<rid>_result.csv`、`<rid>_scores.csv`（可选）、`<rid>_session.json`；
 4) 打印帧数 / 告警 / 退出码。
"""
import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import numpy as np

from detect import (build_rows, load_json, write_result_csv, write_scores_csv)
from algos.online_detector import SeatDetector
from algos.region import load_region_params
from live_seat import post_row, write_algorithm_online


# --------------------------------------------------------------------------- #
def _split_cmd(cmd):
    if os.name == "nt":
        parts = shlex.split(cmd, posix=False)
        return [p.strip('"') for p in parts]
    return shlex.split(cmd)


def _b64_array(b64, shape, dtype):
    buf = base64.b64decode(b64)
    arr = np.frombuffer(buf, dtype=dtype)
    return arr.reshape(tuple(int(v) for v in shape))


def decode_frame(obj):
    """DevFrame dict → (raw_frame int16, valid_chirps bool, frame_id, timestamp|None)。"""
    fid = obj.get("frame_id")
    if fid is None:
        raise ValueError("frame missing frame_id")
    if "raw_frame_b64" in obj:
        raw = _b64_array(obj["raw_frame_b64"], obj["raw_shape"], "<i2")
    else:
        raw = np.asarray(obj["raw_frame"], dtype=np.int16)
    if "valid_chirps_b64" in obj:
        vc = _b64_array(obj["valid_chirps_b64"], obj["valid_shape"], np.uint8).astype(bool)
    else:
        vc = np.asarray(obj.get("valid_chirps"), dtype=bool)
    ts = obj.get("timestamp")
    return raw, vc, int(fid), (None if ts is None else float(ts))


def iter_frames(stream):
    """逐行读取 JSONL DevFrame（跳过 meta 行/空行）。"""
    for line in stream:
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("type") == "meta":
            continue
        yield obj


def check_algorithm_file(path, meta):
    """校验 A 已载入的 algorithm_online.json 与本次 meta 同 recording_id。"""
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            f"algorithm file not found: {path!r}; generate it first with "
            "--emit-algorithm (A reads it at startup)")
    doc = load_json(path)
    rid = str((meta or {}).get("recording_id") or "")
    if doc.get("recording_id") != rid:
        raise ValueError(
            f"algorithm recording_id {doc.get('recording_id')!r} != meta {rid!r}; "
            "the two descriptions must refer to the same recording")
    return doc


def terminate_source(proc, timeout=5.0):
    """释放 B 采集资源：terminate → wait → kill。"""
    if proc is None:
        return
    if proc.poll() is None:
        print("  info: stopping B source process ...")
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
    try:
        if proc.stdout:
            proc.stdout.close()
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser(description="C field entry: B frame stream -> SeatDetector -> A")
    ap.add_argument("--meta", default=os.environ.get("RADAR_A_META"),
                    help="session meta json (default: $RADAR_A_META)")
    ap.add_argument("--algorithm", default=os.environ.get("RADAR_A_ALGORITHM"),
                    help="algorithm_online.json path (default: $RADAR_A_ALGORITHM)")
    ap.add_argument("--config", default=os.path.join("config", "M1_dev112p_v1.2.1.json"))
    ap.add_argument("--region-params", default=os.path.join("config", "region_params_v0.1.json"))
    ap.add_argument("--out", default=None, help="result dir (default: meta's dir)")
    ap.add_argument("--source-cmd", default=os.environ.get("RADAR_B_CMD"),
                    help="B 解码程序命令（其 stdout 输出 JSONL DevFrame）；缺省从 stdin 读")
    ap.add_argument("--emit-algorithm", action="store_true",
                    help="只生成 algorithm_online.json 后退出（A 启动前先跑一次）")
    ap.add_argument("--emit-scores", action="store_true")
    ap.add_argument("--max-frames", type=int, default=0, help="0 = unlimited (debug)")
    ap.add_argument("--first-frame-timeout", type=float, default=20.0,
                    help="warn if no first frame within N s (A shows 20 s timeout)")
    a = ap.parse_args()

    if not a.meta:
        ap.error("--meta or $RADAR_A_META is required")
    meta = load_json(a.meta)
    rid = str(meta.get("recording_id") or "")
    if not rid:
        raise SystemExit("meta.recording_id missing")
    cfg = load_json(a.config)
    region_params = load_region_params(a.region_params) if os.path.exists(a.region_params) else None
    dt = float((meta.get("radar_config") or {}).get("nominal_frame_period_s") or 0.0) or None

    det = SeatDetector(meta, m1_config=cfg, region_params=region_params, frame_period_s=dt)
    out_dir = a.out or os.path.dirname(os.path.abspath(a.meta))
    os.makedirs(out_dir, exist_ok=True)
    algo_path = a.algorithm or os.path.join(out_dir, f"{rid}_algorithm_online.json")

    # ---- 算法说明：显式生成 / 已存在则校验（不覆盖 A 已载入的那份）----
    if a.emit_algorithm:
        write_algorithm_online(algo_path, rid, det, cfg, region_params,
                               dt or 0.0, a.config)
        print(f"[OK] algorithm_online.json -> {algo_path}")
        return 0
    if os.path.exists(algo_path):
        check_algorithm_file(algo_path, meta)
        print(f"[OK] algorithm_online.json verified -> {algo_path}")
    else:
        write_algorithm_online(algo_path, rid, det, cfg, region_params,
                               dt or 0.0, a.config)
        print(f"  warn: algorithm_online.json was missing; generated -> {algo_path}")
        print("  warn: A reads it at startup -- restart A so it loads this file")

    url = os.environ.get("RADAR_A_FRAME_URL")
    token = os.environ.get("RADAR_A_FRAME_TOKEN")
    opener = None
    if url:
        if not token:
            raise SystemExit("$RADAR_A_FRAME_TOKEN missing while $RADAR_A_FRAME_URL is set")
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        print(f"[OK] POST -> {url} (Bearer token set)")
    else:
        print("  note: no $RADAR_A_FRAME_URL; local mode (write CSV only, no POST)")

    # ---- B 来源 ----
    proc = None
    rows, fids, ts_list, fed = [], [], [], []
    err = None
    try:
        if a.source_cmd:
            cmd = _split_cmd(a.source_cmd)
            print(f"[OK] starting B source: {cmd}")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=None,
                                    text=True, encoding="utf-8", errors="replace",
                                    bufsize=1)
            stream = proc.stdout
        else:
            print("[OK] reading DevFrame JSONL from stdin")
            stream = sys.stdin

        t_start = time.perf_counter()
        first = None
        eof = False
        for obj in iter_frames(stream):
            raw, vc, fid, ts = decode_frame(obj)
            if ts is None:
                ts = fid * (dt or 0.1935)
            n_before = det.processed_count
            row = det.feed_dev112p(raw, vc, ts, frame_id=fid)
            fed.append(det.processed_count > n_before)
            if opener is not None:
                post_row(opener, url, token, row, retry_max=8, retry_base=1.0)
            rows.append(row)
            fids.append(fid)
            ts_list.append(ts)
            if first is None:
                first = time.perf_counter()
                print(f"  first frame accepted: frame_id={fid} ts={ts:.3f}s "
                      f"({first - t_start:.1f}s after start)")
            if len(rows) % 200 == 0:
                print(f"  fed {len(rows)} frames, last frame_id={fid}, "
                      f"presence={row['presence']}")
            if a.max_frames and len(rows) >= a.max_frames:
                break
        else:
            eof = True
        if first is None:
            print(f"  warn: no frame received within {a.first_frame_timeout:.0f}s")
        if proc is not None and eof:
            try:
                rc = proc.wait(timeout=5)
                print(f"  info: B source exited rc={rc}")
            except subprocess.TimeoutExpired:
                print("  info: B source still running after EOF; will terminate")
    except KeyboardInterrupt:
        print("  info: interrupted by user")
    except Exception as e:  # noqa: BLE001
        err = e
        print(f"[FAIL] {type(e).__name__}: {e}")
    finally:
        terminate_source(proc)
        _write_outputs(out_dir, rid, rows, fids, ts_list, fed, det, a.emit_scores,
                       a.config, meta, det.warnings)
        print(f"[OK] {rid}: {len(rows)} frames, {len(det.warnings)} warnings")
        for w in det.warnings[:5]:
            print(f"  warn: {w}")
    return 0 if err is None else 1


def _write_outputs(out_dir, rid, rows, fids, ts_list, fed, det, emit_scores,
                   config_src, meta, warnings):
    if not rows:
        return
    result_path = os.path.join(out_dir, f"{rid}_result.csv")
    write_result_csv(result_path, build_rows(
        fids, ts_list,
        [r["presence"] for r in rows],
        [r["presence_confidence"] for r in rows],
        [r["target_count"] for r in rows],
        [r["quality"] for r in rows]))
    scores_path = None
    if emit_scores:
        st = det.state
        scores_path = os.path.join(out_dir, f"{rid}_scores.csv")
        cols = {"score": [], "f1n": [], "f2n": [], "f3n": [], "gate": []}
        si = 0
        for ok in fed:
            if ok:
                cols["score"].append(repr(float(st.score[si])))
                cols["f1n"].append(repr(float(st.f1n[si])))
                cols["f2n"].append(repr(float(st.f2n[si])))
                cols["f3n"].append(repr(float(st.f3n[si])))
                cols["gate"].append(repr(float(st.gate[si])))
                si += 1
            else:  # 投影失败、未进入状态机的帧：分数留空
                for k in cols:
                    cols[k].append("")
        write_scores_csv(scores_path, fids, ts_list, cols)
    session = det.session_summary()
    session.update({
        "schema_version": "1.0",
        "recording_id": rid,
        "mode": "field",
        "config_source": config_src,
        "frames": len(rows),
        "result_csv": os.path.basename(result_path),
        "scores_csv": os.path.basename(scores_path) if scores_path else None,
        "warnings": list(warnings),
    })
    with open(os.path.join(out_dir, f"{rid}_session.json"), "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())

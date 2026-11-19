# -*- coding: utf-8 -*-
"""live_seat v0.1 —— 在线入口（algos/online_detector.py）的驱动：回放回归 / 转发 A。

用法
----
0) 生成 A --live 启动用的在线 algorithm.json（contract v1 新格式，然后退出）：
     python -X utf8 tools/live_seat.py --replay <rid>_raw.npz \
         --config config/M1_dev112p_v0.2.json --emit-algorithm
   A 侧：python live_monitor.py --live --meta <rid>_meta.json \
             --algorithm <rid>_algorithm_online.json
   （detect.py 的离线 algorithm.json 是旧回放格式 —— smoothing 键、无顶层
    thresholds —— 仅适配 A 的名义时间转译回放；A --live 走 contract
    validate_algorithm 严格校验，必须用 _algorithm_online.json。）

1) 回放回归（与离线 detect.py 不带 --posture-config 的输出逐字节对齐）：
      python -X utf8 tools/live_seat.py --replay <rid>_raw.npz \
          --config config/M1_dev112p_v0.2.json \
          --out <dir> --emit-scores --expect <离线输出目录>

   离线基线生成（同数据、无 --posture-config）：
      python -X utf8 detect.py <rid>_raw.npz --algorithm M1 \
          --config config/M1_dev112p_v0.2.json --emit-scores --out <dir>

2) 回放并逐帧转发 A（串行 POST，等响应再发下一帧；A 契约）：
      python -X utf8 tools/live_seat.py --replay <rid>_raw.npz \
          --config config/M1_dev112p_v0.2.json \
          --post-url http://127.0.0.1:8766/api/frame --token <RADAR_A_FRAME_TOKEN> \
          [--interval 0.1935] [--retry-max 8] [--retry-base 1.0]

说明
----
* 回放走 feed_frame（已投影剖面，等价 B 交付链的 C 侧投影结果）；
  B 原始单帧立方的投影等价性由 algos/online_detector.py 的
  project_dev112p_frame 与适配器 chirp_mean 的字节比对覆盖（本目录回归脚本）。
* frame_period_s 取 float(np.median(np.diff(ts)))，与离线 run_m1 完全一致
  （均匀名义时戳下 = 0.1935；不能用 1/5.168 近似）。
* 转发模式下 CSV 仍照常写出（事后审计）；每次运行都会重写
  <rid>_algorithm_online.json（幂等，供 A 启动）。
* POST 失败规则（A↔C 收口，2026-11-19）：
  - HTTP 状态错误（400/401/…）：不重试，立即中止 —— 数据/配置问题，
    按 A 契约修正当前帧或重启会话；盲目重试只会重复同一条错误。
  - 网络层失败（URLError/超时）：指数退避重试同一帧（--retry-max 次），
    重试前经 GET /api/state 对齐（A 契约：不确定请求是否成功时先查
    state.frame_id）；若 A 实际已接受该帧（响应丢失）则不再重发，
    避免重复帧 400。
  - 重试耗尽：中止会话并提示 —— A 契约要求重启 A 与 C 会话（帧号归零）。
  - 不静默丢帧。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from detect import (HEADER, build_rows, load_json, md5_8, normalize_axes,
                    validate_inputs, write_result_csv, write_scores_csv)
from algos.online_detector import RESULT_COLUMNS, SeatDetector


def _state_url(post_url):
    """POST 地址 → 同源 GET /api/state（A 契约的对齐查询端点）。"""
    root = post_url.rstrip("/")
    return root[: root.rfind("/")] + "/state"


def _frame_already_accepted(opener, post_url, token, fid):
    """GET /api/state 对齐：A 是否已接受第 fid 帧（响应丢失场景）。

    查询本身失败（A 不可达）按“未接受”处理，交还重试循环。
    """
    req = urllib.request.Request(_state_url(post_url),
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with opener.open(req, timeout=5) as resp:
            snap = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    accepted = (snap.get("state") or {}).get("frame_id")
    if accepted == fid:
        print(f"  info: frame {fid} already accepted by A (response lost); continue")
        return True
    return False


def post_row(opener, url, token, row, retry_max=8, retry_base=1.0):
    """串行 POST 一帧（A 契约：九字段 JSON；成功响应后才发下一帧）。

    失败规则见模块 docstring（A↔C 收口 2026-11-19）：HTTP 状态错误不重试；
    网络层失败退避重试 + GET /api/state 对齐；耗尽即中止（不静默丢帧）。
    返回 A 回执的 accepted_frame_id（与帧号不一致视为会话错乱，立即报错）。
    """
    body = json.dumps({k: row[k] for k in RESULT_COLUMNS}, ensure_ascii=False)
    fid = int(row["frame_id"])
    attempts = 0
    while True:
        req = urllib.request.Request(
            url, data=body.encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"})
        try:
            with opener.open(req, timeout=10) as resp:
                accepted = json.loads(resp.read().decode("utf-8")).get(
                    "accepted_frame_id")
            if accepted != fid:
                raise RuntimeError(
                    f"frame {fid} POST accepted_frame_id mismatch: {accepted!r} "
                    "(session out of sync; restart A and C sessions)")
            return accepted
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"frame {fid} POST -> HTTP {e.code}: {detail} "
                "(HTTP errors are not retried: fix the frame/config or restart "
                "the A and C sessions)") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            attempts += 1
            if attempts > retry_max:
                raise RuntimeError(
                    f"frame {fid} POST unreachable after {retry_max} retries: {e}; "
                    "session aborted -- restart A and C sessions "
                    "(frame_id resets to 0 per contract)") from e
            wait = retry_base * (2 ** (attempts - 1))
            print(f"  warn: frame {fid} POST unreachable ({e}); "
                  f"retry {attempts}/{retry_max} in {wait:.1f}s")
            if _frame_already_accepted(opener, url, token, fid):
                return fid
            time.sleep(wait)


def write_algorithm_online(path, rid, det, cfg, region_params, dt_med,
                           config_source):
    """写 <rid>_algorithm_online.json —— A --live 启动载入的 contract v1 新格式。

    与 detect.py 离线 algorithm.json 的差异（A↔C 收口 2026-11-19）：
    离线是旧回放格式（smoothing 键、无顶层 thresholds），仅适配 A 的名义
    时间转译回放；A --live 走 contract validate_algorithm 严格校验
    （ALGORITHM_KEYS 全集 + capabilities 两个纯 bool），必须用本格式。
    capabilities 只放 presence/posture 纯 bool（A 端把 truthy 键渲染为
    “已启用能力”），扩展语义进 notes。
    """
    st = det.state
    mv = str((cfg or {}).get("model_version") or "M1")
    av = str((cfg or {}).get("algorithm_version") or "M1")
    effective = {
        "config": cfg if cfg is not None else "built-in defaults",
        "config_source": config_source,
        "region_params": region_params if region_params is not None else {},
        "region": {k: st.region[k] for k in ("lo", "hi", "main_bin", "source")},
        "degraded_input": st.degraded,
        "frame_period_s": dt_med,
    }
    doc = {
        "schema_version": "1.0",
        "recording_id": rid,
        "algorithm_version": f"{av} + online v0.1",
        "parameter_version": md5_8(effective) + "+online-v0.1",
        "capabilities": det.capabilities(),
        "config": {
            "algorithm": "M1",
            "m1_config": cfg if cfg is not None else "built-in defaults",
            "region_params": region_params if region_params is not None else {},
            "region": effective["region"],
            "degraded_input": st.degraded,
            "frame_period_s": dt_med,
            "config_source": config_source,
        },
        "thresholds": {"t_on": st.t_on, "t_off": st.t_off},
        "window_s": st.win_gate,
        "temporal_smoothing": (
            f"dual-threshold hysteresis T_ON={st.t_on} T_OFF={st.t_off}, "
            f"confirm_frames={st.confirm}"),
        "model_version": f"{mv}+online-v0.1",
        "calibrations": [],
        "notes": [
            "generated by tools/live_seat.py for A --live startup "
            "(live_monitor.py --live --meta <rid>_meta.json --algorithm "
            "<this file>); offline detect.py algorithm.json is the legacy "
            "replay format",
            det.capabilities_semantics(),
            "session diagnostics (region/bg_mode/warnings) in "
            f"<rid>_session.json written by the same run",
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return path


def load_replay(npz_path):
    """载入规范 npz + 同目录 meta（与 detect.py 同一约定）。"""
    rid = os.path.basename(npz_path)
    if rid.endswith("_raw.npz"):
        rid = rid[: -len("_raw.npz")]
    base = os.path.dirname(os.path.abspath(npz_path))
    meta_path = os.path.join(base, f"{rid}_meta.json")
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"meta.json missing beside npz: {meta_path}")
    meta = load_json(meta_path)

    z = np.load(npz_path)
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
    if has_doppler:
        raise ValueError(
            "online v0.1 supports 1dfft profiles only (M1); "
            "2dfft/B0 stays offline (detect.py)")
    return rid, meta, X, ts, fids


def byte_compare(path_a, path_b):
    """逐字节比对；返回 (ok, 详情)。"""
    with open(path_a, "rb") as f:
        a = f.read()
    with open(path_b, "rb") as f:
        b = f.read()
    if a == b:
        return True, f"OK {os.path.basename(path_a)} ({len(a)} bytes, identical)"
    la, lb = a.split(b"\n"), b.split(b"\n")
    for i, (x, y) in enumerate(zip(la, lb), 1):
        if x != y:
            break
    return False, (f"DIFF {os.path.basename(path_a)}: first mismatch at line {i}\n"
                   f"  live    : {x[:200]!r}\n  baseline: {y[:200]!r}\n"
                   f"  ({len(a)} vs {len(b)} bytes)")


def main():
    ap = argparse.ArgumentParser(description="online SeatDetector driver: replay regression / forward to A")
    ap.add_argument("--replay", required=True, help="canonical <rid>_raw.npz (1dfft)")
    ap.add_argument("--config", help="M1 config json (e.g. config/M1_dev112p_v0.2.json)")
    ap.add_argument("--region-params", help="region params json (same as detect.py)")
    ap.add_argument("--out", help="output dir (default: beside npz)")
    ap.add_argument("--emit-scores", action="store_true",
                    help="write <rid>_scores.csv (aux, same columns as offline)")
    ap.add_argument("--expect", metavar="DIR",
                    help="byte-compare result/scores against offline outputs in DIR")
    ap.add_argument("--post-url", default=None,
                    help="e.g. http://127.0.0.1:8766/api/frame (serial POST per frame)")
    ap.add_argument("--token", default=None, help="bearer token for --post-url")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="sleep seconds between frames (pacing; 0 = full speed)")
    ap.add_argument("--emit-algorithm", action="store_true",
                    help="only write <rid>_algorithm_online.json (contract v1 "
                         "format for A --live startup) and exit; no frames fed")
    ap.add_argument("--retry-max", type=int, default=8,
                    help="POST network-failure retries per frame (default 8)")
    ap.add_argument("--retry-base", type=float, default=1.0,
                    help="exponential backoff base seconds (default 1.0)")
    args = ap.parse_args()

    rid, meta, X, ts, fids = load_replay(args.replay)
    n = len(X)
    if n < 2:
        rc = (meta.get("radar_config") or {}).get("nominal_frame_period_s")
        if not rc:
            raise ValueError("single-frame replay needs a frame period "
                             "(meta.radar_config.nominal_frame_period_s missing)")
        dt_med = float(rc)
    else:
        dt_med = float(np.median(np.diff(ts)))

    cfg = load_json(args.config) if args.config else None
    region_params = load_json(args.region_params) if args.region_params else None
    det = SeatDetector(meta, m1_config=cfg, region_params=region_params,
                       frame_period_s=dt_med)

    out_dir = args.out or os.path.dirname(os.path.abspath(args.replay))
    os.makedirs(out_dir, exist_ok=True)

    # ---- A --live 启动用的在线 algorithm.json（contract v1 新格式；每次运行幂等重写） ----
    algo_path = os.path.join(out_dir, f"{rid}_algorithm_online.json")
    write_algorithm_online(algo_path, rid, det, cfg, region_params, dt_med,
                           args.config or "built-in defaults")
    print(f"[OK] wrote {os.path.basename(algo_path)} (A --live: "
          "live_monitor.py --live --meta <rid>_meta.json --algorithm <this file>)")
    if args.emit_algorithm:
        return

    opener = None
    if args.post_url:
        if not args.token:
            raise ValueError("--post-url requires --token")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    t0 = time.perf_counter()
    for i in range(n):
        row = det.feed_frame(X[i], float(ts[i]), frame_id=int(fids[i]))
        if opener is not None:
            post_row(opener, args.post_url, args.token, row,
                     retry_max=args.retry_max, retry_base=args.retry_base)
        if args.interval > 0:
            time.sleep(args.interval)
        if (i + 1) % 200 == 0 or i + 1 == n:
            print(f"  fed {i + 1}/{n} frames "
                  f"({time.perf_counter() - t0:.1f}s, presence={row['presence']})")

    # ---- 写 result.csv（build_rows 同一函数 → 与离线不带 --posture-config 逐字节一致） ----
    st = det.state
    csv_path = os.path.join(out_dir, f"{rid}_result.csv")
    write_result_csv(csv_path, build_rows(fids, ts, st.presence, st.conf,
                                          [1 if p == 1 else (0 if p == 0 else -1)
                                           for p in st.presence],
                                          st.quality))

    scores_path = None
    if args.emit_scores:
        scores_path = os.path.join(out_dir, f"{rid}_scores.csv")
        write_scores_csv(scores_path, fids, ts, {
            "score": [repr(float(v)) for v in st.score],
            "f1n": [repr(float(v)) for v in st.f1n],
            "f2n": [repr(float(v)) for v in st.f2n],
            "f3n": [repr(float(v)) for v in st.f3n],
            "gate": [repr(float(v)) for v in st.gate],
        })

    # ---- 会话说明（诊断用；A 启动时读的是 meta+algorithm.json，与此文件无关） ----
    summary = det.session_summary()
    summary.update({
        "schema_version": "1.0",
        "recording_id": rid,
        "mode": "replay",
        "frame_period_s": dt_med,
        "frames_fed": det.frame_count,
        "frames_processed": det.processed_count,
        "config_source": args.config or "built-in defaults",
        "region_params": region_params if region_params is not None else {},
        "capabilities": det.capabilities(),
    })
    with open(os.path.join(out_dir, f"{rid}_session.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"[OK] {rid}: {det.processed_count}/{det.frame_count} frames processed, "
          f"{len(det.warnings)} warnings")
    for w in det.warnings:
        print(f"  warn: {w}")

    # ---- 回归比对（可选） ----
    if args.expect:
        exp_dir = args.expect
        failures = []
        checks = [byte_compare(csv_path, os.path.join(exp_dir, f"{rid}_result.csv"))]
        if scores_path is not None:
            checks.append(byte_compare(scores_path, os.path.join(exp_dir, f"{rid}_scores.csv")))
        for ok, msg in checks:
            print(f"  {msg}")
            if not ok:
                failures.append(msg)
        if failures:
            print(f"[FAIL] {rid}: regression mismatch")
            sys.exit(1)
        print(f"[PASS] {rid}: online replay == offline (byte-identical)")


if __name__ == "__main__":
    main()

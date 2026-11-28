"""用同一份 A 配置复验同一录制的两版 C 逐帧结果。"""

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


APP_DIR = "A_stone140_应用与业务"
MILESTONES = {
    "idle": "确认空座",
    "in_use": "确认占用",
    "leave_wait": "启动倒计时",
    "returned": "取消倒计时",
    "released": "释放座位",
}


def load_a(repo):
    app = repo.resolve() / APP_DIR
    if not (app / "live_monitor.py").is_file() or not (app.parent / "contract.py").is_file():
        raise ValueError("--repo-root 须指向包含 A 应用目录和共享契约的仓库根目录")
    sys.path.insert(0, str(app))
    import live_monitor
    return live_monitor


def read_case(a, meta_path, algorithm_path, result_path):
    meta, algorithm, period = a.load_metadata(meta_path, algorithm_path, replay=True)
    clock_source = meta.get("timestamp_source")
    if clock_source == "nominal_device_counter":
        frame_period = meta["radar_config"]["nominal_frame_period_s"]
        if (isinstance(frame_period, bool) or not isinstance(frame_period, (float, int))
                or not math.isfinite(frame_period) or frame_period <= 0):
            raise ValueError("采集描述的名义帧周期须为有限正数")
        if period is not None and not math.isclose(frame_period, period, rel_tol=0, abs_tol=1e-12):
            raise ValueError("采集描述与算法描述声明的名义帧周期不同")
        period = frame_period
    return meta, algorithm, period, a.load_recorded_rows(result_path, algorithm, period)


def compare_clock(baseline, candidate, frame_limit):
    if frame_limit is not None:
        if not 0 < frame_limit <= min(len(baseline), len(candidate)):
            raise ValueError("--frame-limit 须为正数，且两版都覆盖指定的帧数")
        baseline, candidate = baseline[:frame_limit], candidate[:frame_limit]
    if len(baseline) != len(candidate):
        raise ValueError("两版帧数不同。可用 --frame-limit 明确指定共同前缀帧数")
    for old, new in zip(baseline, candidate):
        if (old["frame_id"], old["timestamp"]) != (new["frame_id"], new["timestamp"]):
            raise ValueError(f"第 {old['frame_id']} 帧的帧号或时间戳不一致")
    return baseline, candidate


def replay(a, meta, algorithm, config, rows):
    machine = a.StateMachine(meta, algorithm, config)
    timeline, events = machine.run(rows)
    milestones = {key: [] for key in MILESTONES}
    for event in events:
        if event["event_type"] != "occupancy_change":
            continue
        stage = event["to_state"]
        if (event["from_state"], stage) == ("leave_wait", "in_use"):
            stage = "returned"
        if stage in milestones:
            milestones[stage].append({key: event[key] for key in ("frame_id", "timestamp", "session_id")})
    return timeline, events, {
        "algorithm_version": algorithm["algorithm_version"],
        "parameter_version": algorithm["parameter_version"],
        "milestones": milestones,
        "end_state": timeline[-1]["occupancy_state"],
        "end_release_remaining_s": timeline[-1]["release_remaining_s"],
    }


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(args):
    a = load_a(args.repo_root)
    from contract import sha256
    from offline_state_machine import EVENT_COLUMNS, TIMELINE_COLUMNS, VERSION

    paths = {key: getattr(args, key) for key in (
        "meta", "baseline", "baseline_algorithm", "candidate", "candidate_algorithm", "config"
    )}
    inputs = {key: {"file": path.name, "sha256": sha256(path)} for key, path in paths.items()}
    old_meta, old_alg, old_period, old_all = read_case(a, args.meta, args.baseline_algorithm, args.baseline)
    new_meta, new_alg, new_period, new_all = read_case(a, args.meta, args.candidate_algorithm, args.candidate)
    if old_period != new_period:
        raise ValueError("两版声明的名义时间口径不同")
    old_rows, new_rows = compare_clock(old_all, new_all, args.frame_limit)

    saved_config = a.read_json(args.config)
    if "config" in saved_config:
        if saved_config.get("recording_id") != old_meta["recording_id"]:
            raise ValueError("现场快照与采集描述的 recording_id 不一致")
        saved_config = saved_config["config"]
    config = a.validate_config(saved_config)
    old_timeline, old_events, old_summary = replay(a, old_meta, old_alg, config, old_rows)
    new_timeline, new_events, new_summary = replay(a, new_meta, new_alg, config, new_rows)

    if any(sha256(path) != inputs[key]["sha256"] for key, path in paths.items()):
        raise ValueError("输入文件在复验期间有变化")
    report = {
        "recording_id": old_meta["recording_id"],
        "source_kind": old_meta["source_kind"],
        "state_machine_version": VERSION,
        "time_basis": "nominal_frame_period" if old_period is not None else "recording_timestamp",
        "time_scope": "recorded frame time; EOF does not advance timers",
        "compared_frames": len(old_rows),
        "last_frame_id": old_rows[-1]["frame_id"],
        "baseline_input_frames": len(old_all),
        "candidate_input_frames": len(new_all),
        "config": config,
        "inputs": inputs,
        "baseline": old_summary,
        "candidate": new_summary,
    }
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="compare-replay-", dir=output.parent) as folder:
        staging = Path(folder) / "result"
        staging.mkdir()
        write_csv(staging / "baseline_timeline.csv", TIMELINE_COLUMNS, old_timeline)
        write_csv(staging / "baseline_events.csv", EVENT_COLUMNS, old_events)
        write_csv(staging / "candidate_timeline.csv", TIMELINE_COLUMNS, new_timeline)
        write_csv(staging / "candidate_events.csv", EVENT_COLUMNS, new_events)
        (staging / "comparison.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
        staging.rename(output)
    print(f"{old_meta['recording_id']} · 对照 {len(old_rows)} 帧")
    for key, label in MILESTONES.items():
        before = ", ".join(str(e["frame_id"]) for e in old_summary["milestones"][key]) or "未发生"
        after = ", ".join(str(e["frame_id"]) for e in new_summary["milestones"][key]) or "未发生"
        print(f"{label}（帧号）  基线 {before}  →  候选 {after}")
    print(f"结果目录：{output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="含 A 和共享状态机的仓库根目录")
    parser.add_argument("--meta", type=Path, required=True, help="两版结果共享的采集描述")
    parser.add_argument("--baseline", type=Path, required=True, help="基线 C 结果 CSV")
    parser.add_argument("--baseline-algorithm", type=Path, required=True, help="基线算法描述 JSON")
    parser.add_argument("--candidate", type=Path, required=True, help="候选 C 结果 CSV")
    parser.add_argument("--candidate-algorithm", type=Path, required=True, help="候选算法描述 JSON")
    parser.add_argument("--config", type=Path, required=True, help="固定 A 配置或含 config 的现场快照")
    parser.add_argument("--output", type=Path, required=True, help="新建输出目录")
    parser.add_argument("--frame-limit", type=int, help="只对照前 N 帧。省略时要求两版帧数一致")
    try:
        run(parser.parse_args())
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"复验失败：{error}\n")


if __name__ == "__main__":
    main()

"""独立占用标注 v0.1.0。只读取采集元数据/现场记录，不读取算法预测。"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

VERSION = "0.1.0"
FIELDS = "start_timestamp end_timestamp seat_id presence_label posture_label event_label annotator".split()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def finite(value):
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("时间须为有限数")
    return n


def read_labels(path):
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError("labels.csv 表头不匹配")
        for line, row in enumerate(reader, 2):
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("列数不匹配")
                a, b = finite(row["start_timestamp"]), finite(row["end_timestamp"])
                if not 0 <= a < b:
                    raise ValueError("区间须非负、起点小于终点")
                if row["presence_label"] not in ("0", "1", "unknown"):
                    raise ValueError("presence_label 须为 0/1/unknown")
                if row["posture_label"] not in ("unknown", "not_applicable", "reference", "forward", "backward"):
                    raise ValueError("姿态枚举无效")
                if row["presence_label"] == "0" and row["posture_label"] != "not_applicable":
                    raise ValueError("空座姿态须 not_applicable")
                if row["presence_label"] == "unknown" and row["posture_label"] != "unknown":
                    raise ValueError("未知占用姿态须 unknown")
                if not row["seat_id"] or not row["annotator"]:
                    raise ValueError("缺少座位或整理者")
                if rows and (row["seat_id"] != rows[0]["seat_id"] or a < rows[-1]["end_timestamp"]):
                    raise ValueError("座位不一致或区间重叠/倒序")
                row.update(start_timestamp=a, end_timestamp=b)
                rows.append(row)
            except (ValueError, TypeError) as e:
                raise ValueError(f"{path}: 第 {line} 行: {e}") from e
    if not rows:
        raise ValueError("空标签文件")
    return rows


def build(meta_path, record_path, out, target_empty=False, after_s=2., before_s=5., tail_s=2.):
    """事件后/前留白是分析策略，不是已测得同步误差界。"""
    if min(after_s, before_s, tail_s) < 0:
        raise ValueError("留白参数不能为负")
    meta = load(meta_path)
    if meta["source_kind"] != "measured":
        raise ValueError("该生成入口仅用于带独立现场记录的 measured 归档")
    events = meta.get("annotation", {}).get("stopwatch_events", [])
    period = finite(meta["radar_config"]["nominal_frame_period_s"])
    end = (int(meta["raw_shape"][0])-1)*period
    if period <= 0 or end <= period:
        raise ValueError("帧周期/帧数无效")
    cores = []
    if target_empty:
        # 必须由操作者据记录显式传入，不从算法或文件名推断。
        if "目标座全程空着" not in Path(record_path).read_text(encoding="utf-8-sig"):
            raise ValueError("现场记录没有目标座全程空着的明确声明")
        cores = [(period, end, "0", "observer_confirmed_target_empty")]
    else:
        previous = -1
        for i, event in enumerate(events):
            t = finite(event["time_s"])
            if t <= previous:
                raise ValueError("现场事件时间须严格递增")
            previous = t
            text = event["event"]
            state = "0" if ("走远" in text or text == "离座") else "1" if any(x in text for x in ("坐稳", "到位", "回正")) else None
            if state is None:
                continue
            a = t+after_s
            b = finite(events[i+1]["time_s"])-before_s if i+1 < len(events) else end-tail_s
            if b > a:
                cores.append((a, b, state, "field_event_core:"+text))
    rows = []
    def append(a,b,p,reason):
        if b > a:
            rows.append(dict(start_timestamp=round(a,6),end_timestamp=round(b,6),seat_id=meta["seat_id"],
                             presence_label=p,posture_label="not_applicable" if p=="0" else "unknown",
                             event_label=reason,annotator="D_assisted_from_B_field_log"))
    cursor = 0.
    for a,b,p,reason in cores:
        a,b = max(cursor,a),min(end,b)
        if b <= a:
            continue
        append(cursor,a,"unknown","unverified_or_transition")
        append(a,b,p,reason)
        cursor=b
    append(cursor,end,"unknown","unverified_or_transition")
    dest=Path(out)
    dest.mkdir(parents=True,exist_ok=False)
    with (dest/"labels.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    save(dest/"annotation.json",dict(schema_version="1.0",tool_version=VERSION,recording_id=meta["recording_id"],
         subject_id=meta["subject_id"],seat_id=meta["seat_id"],source_kind="measured",
         status="field_log_derived_pending_review",label_clock="nominal_recording" if target_empty else "stopwatch",
         alignment_confirmed=bool(target_empty),alignment_reason="constant target-empty evidence" if target_empty else "stopwatch alignment to radar not measured",
         timing_offset_s=0 if target_empty else None,synchronization_uncertainty_s=None,
         guards=dict(after_event_s=after_s,before_next_event_s=before_s,tail_s=tail_s),
         guards_are_measured_error_bound=False,posture_annotation_supported=False,
         event_latency_evaluation_supported=False,
         sources=[dict(file=Path(p).name,sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in (meta_path,record_path)],
         warning="Intervals are conservatively derived from field observations, not algorithm results. Guards do not prove clock alignment; confirm before final evaluation."))
    read_labels(dest/"labels.csv")
    return dest


def validate_split(value):
    seen={}
    for split in ("development","validation","test"):
        for person in value.get(split,[]):
            if person in seen:
                raise ValueError(f"人员 {person} 同时出现在 {seen[person]} 和 {split}")
            seen[person]=split
    return True


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    b=sub.add_parser("build");b.add_argument("meta");b.add_argument("record");b.add_argument("output");b.add_argument("--target-empty",action="store_true")
    v=sub.add_parser("validate");v.add_argument("labels")
    args=p.parse_args()
    try:
        if args.command=="build": print(build(args.meta,args.record,args.output,args.target_empty))
        else: print(f"标签校验通过：{len(read_labels(args.labels))} 个区间")
    except (ValueError,KeyError,OSError) as e:
        p.exit(1,f"失败：{e}\n")

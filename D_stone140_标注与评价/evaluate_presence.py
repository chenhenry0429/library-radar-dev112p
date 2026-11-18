"""只做独立占用评价；不输入推理管线、不自动调阈值、不伪造事件延迟。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from label_tools import finite, load, read_labels, save


def evaluate(result_path,labels_path,annotation_path,meta_path,algorithm_path,max_gap_s,
             offset_s=None,allow_provisional=False):
    labels=read_labels(labels_path); ann=load(annotation_path); meta=load(meta_path); algo=load(algorithm_path)
    if ann["recording_id"] != meta["recording_id"] or algo["recording_id"] != meta["recording_id"]:
        raise ValueError("recording_id 不一致")
    if ann["source_kind"] != meta["source_kind"]:
        raise ValueError("禁止混合 synthetic 与 measured")
    if any(r["seat_id"]!=meta["seat_id"] for r in labels):
        raise ValueError("座位不一致")
    if finite(max_gap_s)<=0:
        raise ValueError("max_gap_s 须大于 0")
    provisional=not ann.get("alignment_confirmed",False) or ann.get("status")!="reviewed"
    if provisional and not allow_provisional:
        raise ValueError("标注/时间对齐待确认；如仅调试，显式 --allow-provisional --offset-s 数值")
    if offset_s is None:
        offset_s=ann.get("timing_offset_s")
    if offset_s is None:
        raise ValueError("必须明确给出标签秒表 → 预测时间的 offset-s，不默认假设 0")
    offset_s=finite(offset_s)
    rows=[]
    with Path(result_path).open(encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f)
        if not {"frame_id","timestamp","presence","quality"}.issubset(reader.fieldnames or []):
            raise ValueError("结果缺少必填列")
        for line,r in enumerate(reader,2):
            t=finite(r["timestamp"]); fid=int(r["frame_id"]);pred=int(r["presence"])
            if fid!=len(rows) or t<0 or (rows and t<=rows[-1]["t"]):
                raise ValueError(f"第 {line} 行帧号/时间无效")
            if pred not in (-1,0,1) or r["quality"] not in ("ok","low_signal","invalid_frame"):
                raise ValueError(f"第 {line} 行枚举无效")
            rows.append(dict(t=t,pred=pred if r["quality"]=="ok" else -1))
    if not rows: raise ValueError("空预测结果")
    if "raw_shape" in meta and len(rows)!=meta["raw_shape"][0]:
        raise ValueError("预测行数与本录制 meta.raw_shape 不一致")
    # 每帧按 timestamp 落在左闭右开标签区间内计数。标签未知和未覆盖分列。
    matrix={"tn":0,"fp":0,"fn":0,"tp":0,"unknown_empty":0,"unknown_occupied":0}
    duration={k:0. for k in matrix};excluded_unknown=0;excluded_uncovered=0;gap_s=0.
    def truth_at(t):
        return next((r["presence_label"] for r in labels if r["start_timestamp"]+offset_s<=t<r["end_timestamp"]+offset_s),None)
    def key(truth,pred):
        if pred==-1:return "unknown_empty" if truth=="0" else "unknown_occupied"
        return {("0",0):"tn",("0",1):"fp",("1",0):"fn",("1",1):"tp"}[(truth,pred)]
    for i,r in enumerate(rows):
        truth=truth_at(r["t"])
        if truth is None: excluded_uncovered+=1
        elif truth=="unknown": excluded_unknown+=1
        else: matrix[key(truth,r["pred"])]+=1
        if i+1==len(rows):continue  # 不推算末帧之后的时间
        end=rows[i+1]["t"];dt=end-r["t"]
        if dt>max_gap_s:
            gap_s+=dt;continue  # 缺口整段不填充为上一状态
        for label in labels:
            if label["presence_label"]=="unknown":continue
            overlap=max(0.,min(end,label["end_timestamp"]+offset_s)-max(r["t"],label["start_timestamp"]+offset_s))
            duration[key(label["presence_label"],r["pred"])]+=overlap
    def ratio(a,b):return a/b if b else None
    known=sum(matrix[k] for k in ("tn","fp","fn","tp"));total=sum(matrix.values())
    negatives=matrix["tn"]+matrix["fp"]+matrix["unknown_empty"]
    positives=matrix["tp"]+matrix["fn"]+matrix["unknown_occupied"]
    nominal=algo.get("time_base",{}).get("source")=="nominal_frame_period" or meta.get("timing",{}).get("measured_frame_timestamps","absent") is None
    return dict(schema_version="1.0",recording_id=meta["recording_id"],source_kind=meta["source_kind"],
        status="provisional_not_acceptance" if provisional else "reviewed_frame_evaluation",
        algorithm_version=algo.get("algorithm_version"),parameter_version=algo.get("parameter_version"),
        offset_s=offset_s,frame_confusion=matrix,time_confusion_s=duration,
        scored_frames=total,classified_frames=known,excluded_unknown_label_frames=excluded_unknown,
        excluded_uncovered_frames=excluded_uncovered,excluded_gap_s=gap_s,
        coverage=ratio(known,total),accuracy_on_classified=ratio(matrix["tp"]+matrix["tn"],known),
        false_positive_fraction_all_empty=ratio(matrix["fp"],negatives),
        false_negative_fraction_all_occupied=ratio(matrix["fn"],positives),
        unknown_empty_fraction=ratio(matrix["unknown_empty"],negatives),
        unknown_occupied_fraction=ratio(matrix["unknown_occupied"],positives),
        occupancy_not_detected_fraction=ratio(matrix["fn"]+matrix["unknown_occupied"],positives),
        event_latency_s=None,event_latency_reason="No verified event-boundary/clock alignment; interval cores are not action onset labels.",
        time_metrics_basis="nominal seconds, not actual response time" if nominal else "supplied timestamp seconds",
        window_policy="causal hold only between adjacent frames within max_gap_s; no tail extrapolation",
        max_gap_s=max_gap_s, inputs=[dict(file=Path(p).name,sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in (result_path,labels_path,annotation_path,meta_path,algorithm_path)])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("result","labels","annotation","meta","algorithm","output"):p.add_argument(name)
    p.add_argument("--max-gap-s",type=float,required=True);p.add_argument("--offset-s",type=float);p.add_argument("--allow-provisional",action="store_true")
    a=p.parse_args()
    try:
        report=evaluate(a.result,a.labels,a.annotation,a.meta,a.algorithm,a.max_gap_s,a.offset_s,a.allow_provisional)
        dest=Path(a.output);dest.mkdir(parents=True,exist_ok=False);save(dest/"metrics.json",report)
        (dest/"report.md").write_text("# 占用评价\n\n状态："+report["status"]+"\n\n```json\n"+json.dumps(report,ensure_ascii=False,indent=2)+"\n```\n",encoding="utf-8")
        print(dest)
    except (ValueError,KeyError,OSError) as e:p.exit(1,f"评价失败：{e}\n")

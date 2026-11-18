"""依据独立 B 端记录生成本地标注包，不读取任何算法输出。"""
import argparse
from pathlib import Path
from label_tools import build,load,save,validate_split,VERSION,read_labels

RECORDINGS=["formal/T01_P02_R01","formal/T01_P04_R01","formal/T01_neighbor_R01","formal/T01_aisle_R01",
            "retests/T01_P02_retest","retests/T01_P04_retest","retests/T01_chair_shift"]


def prepare(evidence,out):
    evidence,out=Path(evidence),Path(out)
    out.mkdir(parents=True,exist_ok=False)
    summary=[]
    for folder in RECORDINGS:
        meta=load(evidence/folder/"meta.json")
        empty=folder in ("formal/T01_neighbor_R01","formal/T01_aisle_R01")
        dest=build(evidence/folder/"meta.json",evidence/folder/"采集记录.md",out/meta["recording_id"],empty)
        rows=read_labels(dest/"labels.csv")
        summary.append(dict(recording_id=meta["recording_id"],interval_count=len(rows),
             known_label_seconds=sum(r["end_timestamp"]-r["start_timestamp"] for r in rows if r["presence_label"]!="unknown"),
             alignment="constant_empty_evidence" if empty else "pending_stopwatch_alignment",
             final_evaluation="not_performed_missing_matched_C_predictions"))
    # 既有参与者/数据已经用于分析，不再回头指定为独立测试集。
    split=dict(schema_version="1.0",development=["P01","P02","P03","P04"],validation=[],test=[],
               reason="Existing participants/recordings exposed during development; no independent holdout yet.",
               future_test_requirement="new participant not used for tuning after algorithm freeze",
               subjectless_controls=["T01_neighbor_R01","T01_aisle_R01","T01_chair_shift"])
    validate_split(split);save(out/"split.json",split)
    save(out/"summary.json",dict(version=VERSION,labels_independent_of_predictions=True,recordings=summary))
    lines=["# D 标注整理结果 v0.1.0","","已从 B 的现场记录生成 7 份 labels.csv 与 annotation.json；没有读取 C 的预测作为标签依据。", "", "| 录制 | 区间数 | 已知标签时长（标签时钟秒） | 对齐状态 |", "| --- | ---: | ---: | --- |"]
    for s in summary:lines.append(f"| {s['recording_id']} | {s['interval_count']} | {s['known_label_seconds']:.3f} | {s['alignment']} |")
    lines += ["","姿态全部未知（空座为不适用）；现场动作到位记录不能证明整个区间的精确躯干角度。", "", "P02/P04 标签保留秒表时钟，未强行改写为雷达时钟；事件后 2 秒、下一事件前 5 秒和尾部 2 秒为保守分析策略，不是同步误差已被测得的保证。所有过渡或证据不足区间写 unknown。", "", "邻座/过道的空座标签依据现场明确确认；可评价其目标座假占用，但需相同录制的 C 预测。当前 C main 的 P00 examples 不能代替这些实测录制。", "", "全部既有参与者归入 development；没有独立测试集。不伪造识别率或延迟，未从公开仓库下载到的匹配预测不能靠 P01 基线替代。", ""]
    (out/"标注整理结果.md").write_text("\n".join(lines),encoding="utf-8")
    return out


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("evidence");p.add_argument("output");a=p.parse_args()
    try:print(prepare(a.evidence,a.output))
    except (ValueError,KeyError,OSError) as e:p.exit(1,f"生成失败：{e}\n")

"""独立 NPZ/结果诊断；不修改输入。数值接近与严格相等分开报告。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np


def check(raw_path,result_path,meta_path):
    meta=json.loads(Path(meta_path).read_text(encoding="utf-8-sig"))
    with Path(result_path).open(encoding="utf-8-sig",newline="") as f:rows=list(csv.DictReader(f))
    if not rows:raise ValueError("空结果")
    actual_ids=np.array([int(r["frame_id"]) for r in rows],dtype=np.int64)
    actual_t=np.array([float(r["timestamp"]) for r in rows],dtype=np.float64)
    with np.load(raw_path,allow_pickle=False) as z:
        data,ts,ids=z["raw_data"],z["timestamps"],z["frame_ids"]
        if data.dtype.hasobject or not np.issubdtype(data.dtype,np.number):raise ValueError("载荷不是固定数值数组")
        if ts.dtype!=np.dtype('float64') or ids.dtype!=np.dtype('int64'):raise ValueError("时间/帧号 dtype 不匹配")
        if ts.shape!=(len(rows),) or ids.shape!=ts.shape or data.shape[0]!=len(rows):raise ValueError("数组/CSV 长度不一致")
        if list(data.shape)!=meta["raw_shape"] or str(data.dtype)!=meta["raw_dtype"]:raise ValueError("载荷与 meta 不一致")
        if not np.isfinite(ts).all() or not np.isfinite(actual_t).all() or not (np.diff(ts)>0).all() or not (np.diff(actual_t)>0).all():raise ValueError("时间必须有限、严格递增")
        ids_ok=bool(np.array_equal(ids,actual_ids) and np.array_equal(ids,np.arange(len(rows))))
        exact=bool(np.array_equal(ts,actual_t));error=float(np.max(np.abs(ts-actual_t)))
        report=dict(recording_id=meta["recording_id"],frame_count=len(rows),raw_shape=list(data.shape),raw_dtype=str(data.dtype),
                    ids_equal=ids_ok,timestamps_exact=exact,max_timestamp_difference_s=error,
                    timestamps_within_1e_12=error<=1e-12,tolerance_is_diagnostic_not_schema_change=True,
                    status="strict_pass" if ids_ok and exact else "numerically_close_not_exact" if ids_ok and error<=1e-12 else "mismatch",
                    source_kind=meta["source_kind"],timestamp_origin=meta.get("timestamp_origin"),
                    limitation="nominal time alignment does not prove wall-clock synchronization or absence of packet loss",
                    inputs=[dict(file=Path(p).name,sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in (raw_path,result_path,meta_path)])
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['raw','result','meta','output']:p.add_argument(n)
    a=p.parse_args()
    try:
        report=check(a.raw,a.result,a.meta);dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
        print(report['status']);p.exit(0 if report['status']=='strict_pass' else 2)
    except (ValueError,KeyError,OSError) as e:p.exit(1,f"诊断失败：{e}\n")

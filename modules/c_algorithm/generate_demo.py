# -*- coding: utf-8 -*-
"""
M1-demo 生成器：lifecycle 演示的"真实结果"生成（规范第 4/5/6 节同一接口）

管线：
  1) 合成 1dfft 复数距离谱（静态背景杂波 + 呼吸微动 + 动作帧 + 区域外路过干扰）
  2) 简化 M1：区域限定 → 背景估计与冻结 → F1 去背景能量 / F2 相位方差 / F3 呼吸频段占比
     → 窗内存在率门控 → 双阈值滞回 → 置信度映射
  3) lifecycle 剧本骨架（calibration_status / posture 与示例完全一致）
  4) 自检：判决序列 == 剧本序列；规范 5 条输出约束；N 帧 N 行；时间递增

输出（outputs/offline_demo/）：
  20261103_P01_S01_lifecycle_01_raw.npz        合成输入（timestamps/frame_ids/raw_data）
  20261103_P01_S01_lifecycle_01_meta.json     输入元数据（占位值 → 真实值）
  20261103_P01_S01_lifecycle_01_result.csv    M1 管线真实输出（151 行 × 9 列）
  20261103_P01_S01_lifecycle_01_algorithm.json 算法元数据（真实参数 + 哈希）
"""
import json, hashlib, os, sys, io
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SEED = 20261103
rng = np.random.default_rng(SEED)
OUT = os.path.join("outputs", "offline_demo")
os.makedirs(OUT, exist_ok=True)
RID = "20261103_P01_S01_lifecycle_01"

# ---------------- lifecycle 剧本（与示例文件一致，A 端依赖） ----------------
N = 151
ts = np.arange(N, dtype=np.float64)
fids = np.arange(N, dtype=np.int64)
present_script = np.zeros(N, dtype=int)
present_script[5:42] = 1     # t=5..41 有人（37 帧）
present_script[49:65] = 1   # t=49..64 有人（16 帧）
ACTION_FRAMES = {5, 30, 41, 49, 55, 64}   # 入座/离座/中途小动作（相位扰动）

def cal_posture(t):
    """calibration_status / posture / 剧本骨架（复刻示例）"""
    if present_script[t] == 0:
        return "uncalibrated", "not_applicable"
    if 5 <= t <= 15:
        return "calibrating", "unknown"
    if 16 <= t <= 20:
        return "valid", "reference"
    if 21 <= t <= 41:
        return "valid", "forward"
    # 49..64：回座，无有效标定
    return "uncalibrated", "unknown"

# ---------------- 1) 合成复数距离谱（1dfft 等效层级） ----------------
NB = 64
REGION = np.arange(14, 23)   # 座位区域 bins（安装已知）
MAIN = 17                     # 座位主 bin
SIG = 0.08                    # 每分量噪声标准差
BRF = 0.25                    # 呼吸频率 Hz（1 Hz 帧率下 4 s 窗整周期）

bg0 = 0.5 * np.exp(1j * rng.uniform(0, 2 * np.pi, NB))     # 静态背景杂波
br_p0 = float(rng.uniform(0, 2 * np.pi))
walk_ph = float(rng.uniform(0, 2 * np.pi))

spec = np.zeros((N, NB), dtype=np.complex128)
drift = 0.0
for t in range(N):
    frame = bg0 + (rng.normal(0, SIG, NB) + 1j * rng.normal(0, SIG, NB))
    if present_script[t]:
        breath = np.sin(2 * np.pi * BRF * t + br_p0)
        amp = 6.0 * (1.0 + 0.08 * breath)
        ph = 1.2 * breath + drift          # 胸腔相位调制 ~±1.2 rad（24 GHz 下 mm 级位移）
        if t in ACTION_FRAMES:
            amp *= 1.3
            ph += rng.uniform(-0.8, 0.8)
            drift += rng.normal(0, 0.10)
        else:
            drift += rng.normal(0, 0.02)
        A = amp * np.exp(1j * ph)
        frame[MAIN] += A
        frame[MAIN - 1] += 0.15 * A
        frame[MAIN + 1] += 0.15 * A
        frame[MAIN - 2] += 0.40 * A
        frame[MAIN + 2] += 0.40 * A
    if 70 <= t <= 74:                       # 区域外路过干扰（区域限定应忽略）
        walk_ph += rng.normal(0, 1.2)
        for k in (40, 41, 42, 43, 44):
            frame[k] += 5.0 * np.exp(1j * (walk_ph + 0.3 * k))
    spec[t] = frame
spec = spec.astype(np.complex64)

# ---------------- 2) 简化 M1 管线（严格因果：帧 t 只用 ≤t 数据） ----------------
WIN = 4                      # 特征窗（秒 = 帧 @1 Hz）
W_F1, W_F2, W_F3 = 0.6, 0.2, 0.2
T_ON, T_OFF = 0.5, 0.35      # 双阈值滞回
ALPHA = 0.2                  # 背景 EMA 系数（无人时更新，有人时冻结）
BG_WARMUP = 4                # 背景收敛帧数：此期间置信度封顶（冷启动伪影防护）
F1_LO, F1_HI = -9.0, 8.0     # F1 dB 映射
F2_NORM = 0.30               # 相位方差归一化（rad）

f1n = np.zeros(N); f2n = np.zeros(N); f3n = np.zeros(N)
gate = np.zeros(N); score = np.zeros(N)
presence = np.zeros(N, dtype=int); conf = np.zeros(N)

bg = spec[0].astype(np.complex128).copy()   # 冷启动：帧 0 自身
state = 0
main_unw = np.unwrap(np.angle(spec[:, MAIN].astype(np.complex128)))

for t in range(N):
    X = spec[t].astype(np.complex128)
    # F1：区域去背景能量（dB 归一化）
    resid = X[REGION] - bg[REGION]
    f1 = float(np.sum(np.abs(resid) ** 2))
    f1n[t] = np.clip((10 * np.log10(f1 + 1e-12) - F1_LO) / (F1_HI - F1_LO), 0, 1)
    # F2：主 bin 相位 4 s 窗标准差
    lo = max(0, t - WIN + 1)
    f2n[t] = np.clip(np.std(main_unw[lo:t + 1]) / F2_NORM, 0, 1)
    # F3：主 bin 复序列 4 s 窗 FFT，呼吸频段能量占比
    if t + 1 >= WIN:
        z = spec[lo:t + 1, MAIN].astype(np.complex128)
        Z = np.fft.fft(z) / len(z)
        band = (abs(Z[1]) ** 2 + abs(Z[-1]) ** 2) / (np.sum(np.abs(Z) ** 2) + 1e-12)
        f3n[t] = np.clip(band * 1.5, 0, 1)
    else:
        f3n[t] = 0.0
    # 窗内目标存在率门控（翻转帧自然低置信的来源）
    gate[t] = float(np.mean(f1n[lo:t + 1]))
    # 组合打分 + 双阈值滞回
    score[t] = W_F1 * f1n[t] + gate[t] * (W_F2 * f2n[t] + W_F3 * f3n[t])
    if score[t] >= T_ON:
        state = 1
    elif score[t] <= T_OFF:
        state = 0
    presence[t] = state
    # 置信度：对判决方向的信心（背景收敛期封顶——首帧"背景=自身"残差为 0 的伪影防护）
    conf[t] = np.clip(0.5 + 0.49 * abs(2 * score[t] - 1), 0.5, 0.99)
    if t < BG_WARMUP:
        conf[t] = min(conf[t], 0.80)
    # 背景更新（仅无人帧；因果：判决之后）
    if state == 0:
        bg = (1 - ALPHA) * bg + ALPHA * X

# posture 置信度（仅 valid 段 16..41）：4 s 置信均值 × 新标定 warmup 折扣
conf4 = np.array([np.mean(conf[max(0, t - WIN + 1):t + 1]) for t in range(N)])
pconf = np.zeros(N)
for t in range(16, 42):
    warm = 0.88 + 0.12 * min(t - 15, 5) / 5.0
    pconf[t] = np.clip(conf4[t] * warm, 0, 0.99)

# ---------------- 3) 输出 result.csv ----------------
target_count = np.where(presence == 1, 1, 0)
lines = ["frame_id,timestamp,presence,presence_confidence,target_count,"
         "posture,posture_confidence,calibration_status,quality"]
for t in range(N):
    cal, pos = cal_posture(t)
    pc = f"{pconf[t]:.2f}" if (16 <= t <= 41 and present_script[t]) else ""
    lines.append(f"{int(fids[t])},{int(ts[t])},{presence[t]},{conf[t]:.2f},"
                 f"{target_count[t]},{pos},{pc},{cal},ok")
with open(os.path.join(OUT, f"{RID}_result.csv"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines) + "\n")

# ---------------- 4) 输出 algorithm.json ----------------
config = {
    "pipeline": "region_limit -> background_freeze -> F1(residual_energy) "
                "+ F2(phase_std) + F3(breath_band_ratio) -> gate -> hysteresis",
    "region_bins": [14, 22],
    "main_bin": MAIN,
    "weights": {"f1": W_F1, "f2": W_F2, "f3": W_F3},
    "gate": "mean(F1n, 4s window)",
    "thresholds": {"on": T_ON, "off": T_OFF},
    "f1_map_db": [F1_LO, F1_HI],
    "f2_norm_rad": F2_NORM,
    "f3_band_hz": [0.2, 0.3],
    "background": {"update": "EMA(alpha=0.2) 仅 presence=0 帧", "freeze": "presence=1 冻结",
                   "warmup": {"frames": BG_WARMUP, "conf_cap": 0.80}},
    "confidence_map": "0.5 + 0.49*|2*score-1|",
    "posture_note": "lifecycle 演示中 posture 列承载标定生命周期标注"
                    "（reference/forward），非姿态分类输出",
    "random_seed": SEED,
}
param_hash = hashlib.md5(
    json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")
).hexdigest()[:8]
algo = {
    "schema_version": "1.0",
    "recording_id": RID,
    "algorithm_version": "M1-demo-0.1",
    "parameter_version": param_hash,
    "capabilities": ["presence", "posture"],
    "config": config,
    "window_s": 4.0,
    "smoothing": "none",
    "model_version": None,
    "calibrations": [
        {
            "calibration_id": "cal_01",
            "seat_id": "seat_01",
            "start_timestamp": 6.0,
            "end_timestamp": 16.0,
            "valid_from_timestamp": 16.0,
            "invalid_from_timestamp": 42.0,
            "invalid_reason": "离座",
        }
    ],
}
with open(os.path.join(OUT, f"{RID}_algorithm.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(algo, f, ensure_ascii=False, indent=2)
    f.write("\n")

# ---------------- 5) 输出 raw.npz + meta.json ----------------
np.savez(os.path.join(OUT, f"{RID}_raw.npz"),
         timestamps=ts, frame_ids=fids, raw_data=spec)
meta = {
    "schema_version": "1.0",
    "recording_id": RID,
    "seat_id": "seat_01",
    "subject_id": "P01",
    "session_id": "S01",
    "source_kind": "synthetic",
    "capture_start": "2026-11-03T10:00:00+08:00",
    "timestamp_unit": "s",
    "timestamp_origin": "recording_start",
    "data_level": "1dfft",
    "raw_shape": [N, NB],
    "raw_dtype": "complex64",
    "axis_order": ["frame", "range_bin"],
    "value_unit": "complex (I+jQ), arbitrary",
    "nominal_frame_rate_hz": 1,
    "radar_config": {"synthetic": True,
                     "note": "复数距离谱由 generate_demo.py 合成（seed 20261103）"},
    "installation": {"seat_region": "seat_01 主 bin 17（区域 bins 14-22，合成安装）"},
    "capture_notes": "M1-demo 合成数据：静态背景杂波 + 呼吸微动(0.25 Hz) + 动作帧"
                     "(5/30/41/49/55/64) + 区域外路过干扰(70-74 s)；"
                     "走同一接口（规范第 4/6 节），真实值由简化 M1 管线产出。",
}
with open(os.path.join(OUT, f"{RID}_meta.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
    f.write("\n")

# ---------------- 6) 自检 ----------------
print("== 自检 ==")
ok = True
# a) 判决序列 == 剧本序列（A 端依赖的硬约束）
mismatch = np.where(presence != present_script)[0]
print(f"[{'PASS' if len(mismatch)==0 else 'FAIL'}] presence 序列与剧本一致"
      f"（不一致帧：{list(mismatch)}）")
ok &= len(mismatch) == 0
# b) N 帧 N 行 / 时间递增 / frame_id 连续
assert len(lines) - 1 == N and np.all(np.diff(ts) > 0) and list(fids) == list(range(N))
print(f"[PASS] N 帧 N 行（{N}）、时间严格递增、frame_id 连续")
# c) 规范 5 条输出约束
for t in range(N):
    cal, pos = cal_posture(t)
    if presence[t] == 0:
        assert target_count[t] == 0 and cal_posture(t)[1] == "not_applicable"
    if presence[t] == 1:
        assert target_count[t] in (1, -1)
    if 16 <= t <= 41 and presence[t] == 1:
        assert cal_posture(t)[0] == "valid" and cal_posture(t)[1] in ("reference", "forward")
    if not (16 <= t <= 41):
        assert cal_posture(t)[1] not in ("reference", "forward")
print("[PASS] 规范 5 条输出一致性约束")
# d) 关键帧数值（真实动态展示）
print("\n== 关键帧（frame, presence, conf, F1n, F2n, F3n, score）==")
for t in (0, 4, 5, 6, 7, 8, 10, 15, 16, 20, 29, 30, 31, 41, 42, 43, 44,
          48, 49, 50, 55, 64, 65, 70, 74, 100, 150):
    print(f"  {t:3d}, {presence[t]}, {conf[t]:.2f}, "
          f"{f1n[t]:.2f}, {f2n[t]:.2f}, {f3n[t]:.2f}, {score[t]:.2f}")
# e) 与示例的结构一致性（presence/posture/cal/quality 列逐行比对）
demo_csv = os.path.join("uploads", "ses_e60fcc48fffeiSwJhBHlfbDuI2",
                        f"{RID}_result.csv")
if os.path.exists(demo_csv):
    with open(demo_csv, encoding="utf-8") as f:
        ref = [r.rstrip("\n").split(",") for r in f]
    same_struct = all(
        ref[i][2] == lines[i].split(",")[2]        # presence
        and ref[i][5] == lines[i].split(",")[5]    # posture
        and ref[i][7] == lines[i].split(",")[7]    # calibration_status
        and ref[i][8] == lines[i].split(",")[8]    # quality
        for i in range(1, N + 1)
    )
    print(f"\n[{'PASS' if same_struct else 'FAIL'}] 与示例结构列"
          f"（presence/posture/calibration_status/quality）逐行一致")
    ok &= same_struct
print("\n== 输出文件 ==")
for fn in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, fn)
    print(f"  {fn}  ({os.path.getsize(p)} B)")
sys.exit(0 if ok else 1)

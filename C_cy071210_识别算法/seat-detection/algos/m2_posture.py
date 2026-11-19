# -*- coding: utf-8 -*-
"""M2 姿态识别 v0.1（gated baseline）。

管线（完整实现，数据不满足前提时如实拒识）：
  1. 参考标定：presence=1 稳定 settle_s 后取 ref_frames 帧的幅度谱统计
     （能量重心 centroid、能量和 sumP）。参考窗内 presence 必须连续为 1。
  2. 特征提取：每帧 region±margin bins（排除 bin0 TX 泄漏）的能量重心与能量和。
  3. 质量门（contrast pre-check，v0.1 核心拒识）：
     参考窗与紧随其后 next_frames 窗的重心差 < contrast_factor × 合并 std
     → insufficient_contrast → 全程 unknown。
      依据：T01 开发集诊断表明，泄漏电平下姿态类间重心差（0.001~0.01 bin）
      与段内 std 同量级，任何阈值分类在该条件下都不可分
      （见 outputs/posture-v01/posture_v01_diagnostics.md）。
  4. 分类（过门后启用）：Δcentroid 相对参考 ±theta 滞回 →
     lean_forward / upright / lean_back；sumP 比值越界 → unknown（参考失效守卫）。
  5. presence=0 → not_applicable。

能力语义（capabilities.posture=true 的含义）：管线已实现并随 M1 一致运行，
但受质量门约束；门失败时 posture 全部 unknown、calibration_status=invalid
（接口 v1 §5 的"参考无效"语义，拒识原因详见 algorithm.json notes 与
posture_v01_diagnostics.md）。这不是缺省占位，是显式拒识。
接口词表（selfcheck C2-C8）：posture ∈ {reference, forward, backward,
unknown, not_applicable}；calibration_status ∈ {uncalibrated, calibrating,
valid, invalid}。
"""
import numpy as np


def run_m2(X, ts, presence, quality, cfg, region):
    """X: complex ndarray [frame, range]（1dfft，chip 合并后）。
    返回 dict(posture, posture_conf, calib_status, gate_pass, notes)。
    """
    p = cfg or {}
    settle_s = float(p.get("settle_s", 10.0))
    ref_frames = int(p.get("ref_frames", 50))
    next_frames = int(p.get("next_frames", 50))
    wait_max_s = float(p.get("wait_max_s", 30.0))
    contrast_factor = float(p.get("contrast_factor", 2.0))
    theta = float(p.get("centroid_threshold_bins", 0.02))
    ratio_lo = float(p.get("sump_ratio_lo", 0.5))
    ratio_hi = float(p.get("sump_ratio_hi", 2.5))
    margin_bins = int(p.get("margin_bins", 2))

    lo, hi = int(region["lo"]), int(region["hi"])
    n_bins = X.shape[1]
    bins = np.arange(max(1, lo), min(hi + margin_bins, n_bins - 1) + 1)
    pres = np.asarray(presence)
    n = len(ts)
    posture = ["unknown"] * n
    pconf = [""] * n
    calib = ["uncalibrated"] * n
    notes = []
    # 基础映射（契约 C2/C3）：presence=0 → not_applicable；presence=-1 → unknown。
    # 所有路径（含早退/拒识）都必须满足该映射。
    for i in range(n):
        if pres[i] == 0:
            posture[i] = "not_applicable"
    if len(bins) < 2:
        notes.append("region too narrow for posture features")
        return _pack(posture, pconf, calib, False, notes)

    P = np.abs(X[:, bins]).astype(np.float64)
    cen = (P * bins[None, :]).sum(1) / P.sum(1)
    sump = P.sum(1)

    # ---- 1) settle + 参考窗 ----
    per = float(np.median(np.diff(ts))) if n > 1 else 0.1935
    k_settle = max(1, int(round(settle_s / per)))
    start, run = -1, 0
    for i in range(n):
        run = run + 1 if pres[i] == 1 else 0
        if run >= k_settle:
            start = i - k_settle + 1
            break
    if start < 0:
        notes.append(f"no continuous presence>={settle_s}s; reference never calibrated")
        return _pack(posture, pconf, calib, False, notes)

    ref_lo = -1
    i = start + k_settle
    deadline = i + int(wait_max_s / per)
    while i < n and i < deadline:
        j = i
        while j < n and pres[j] == 1 and (j - i) < ref_frames:
            j += 1
        if (j - i) >= ref_frames:
            ref_lo = i
            break
        i = j + 1
    if ref_lo < 0:
        notes.append(f"no {ref_frames}-frame continuous-presence window for reference")
        return _pack(posture, pconf, calib, False, notes)
    ref_hi = ref_lo + ref_frames
    for i in range(ref_hi):
        if pres[i] == 1:
            calib[i] = "calibrating"

    cen_ref = float(cen[ref_lo:ref_hi].mean())
    std_ref = float(cen[ref_lo:ref_hi].std())
    sump_ref = float(sump[ref_lo:ref_hi].mean())

    # ---- 2) 质量门：参考窗 vs 紧随窗的重心对比度 ----
    seg = [j for j in range(ref_hi, n) if pres[j] == 1][:next_frames]
    if len(seg) < max(5, next_frames // 2):
        notes.append("insufficient frames after reference for contrast pre-check")
        for i in range(ref_hi, n):
            if pres[i] == 1:
                calib[i] = "invalid"
            elif pres[i] == 0:
                posture[i] = "not_applicable"
            else:
                posture[i] = "unknown"
                pconf[i] = ""
        return _pack(posture, pconf, calib, False, notes)
    nxt = np.asarray(seg)
    cen_next = float(cen[nxt].mean())
    std_next = float(cen[nxt].std())
    pooled = float(np.hypot(std_ref, std_next)) or 1e-12
    contrast = abs(cen_next - cen_ref) / pooled
    if contrast < contrast_factor:
        notes.append(
            f"INSUFFICIENT_PERSON_CONTRAST: |delta_centroid|="
            f"{abs(cen_next - cen_ref):.5f} bin vs pooled_std={pooled:.5f} "
            f"(ratio={contrast:.2f} < {contrast_factor}); posture classification "
            "rejected for this recording -- person signature is buried under TX "
            "leakage on current T01 hardware data (see "
            "outputs/posture-v01/posture_v01_diagnostics.md)")
        for i in range(ref_hi, n):
            if pres[i] == 1:
                calib[i] = "invalid"
            elif pres[i] == 0:
                posture[i] = "not_applicable"
            else:  # presence=-1，C3 要求 unknown
                posture[i] = "unknown"
                pconf[i] = ""
        return _pack(posture, pconf, calib, False, notes)

    # ---- 3) 分类（门通过才到这里；当前 T01 开发集预期全部被门拦截）----
    notes.append(f"contrast pre-check PASSED (ratio={contrast:.2f}); "
                 f"classifying with theta={theta} bin")
    for i in range(n):
        if pres[i] == 0:
            posture[i] = "not_applicable"
            continue
        if pres[i] != 1:  # presence=-1（invalid 帧，C3 要求 unknown）
            posture[i] = "unknown"
            pconf[i] = ""
            continue
        if i < ref_hi:  # 参考期保持 calibrating（上面已置）
            continue
        calib[i] = "valid"
        ratio = sump[i] / sump_ref if sump_ref else 0.0
        if not (ratio_lo <= ratio <= ratio_hi):
            posture[i] = "unknown"
            continue
        dc = cen[i] - cen_ref
        if dc >= theta:
            posture[i] = "backward"
            pconf[i] = repr(round(float(min(1.0, 0.5 + 0.5 * abs(dc) / theta)), 4))
        elif dc <= -theta:
            posture[i] = "forward"
            pconf[i] = repr(round(float(min(1.0, 0.5 + 0.5 * abs(dc) / theta)), 4))
        else:
            posture[i] = "reference"
            pconf[i] = repr(round(float(max(0.0, 1.0 - abs(dc) / theta)), 4))
    return _pack(posture, pconf, calib, True, notes)


def _pack(posture, pconf, calib, gate_pass, notes):
    return {"posture": posture, "posture_conf": pconf,
            "calib_status": calib, "gate_pass": gate_pass, "notes": notes}

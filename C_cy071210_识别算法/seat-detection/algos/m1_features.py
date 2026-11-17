# -*- coding: utf-8 -*-
"""M1 v1.1 静态占用特征管线（demo 验证结构的正式化版本）。

管线（严格因果：帧 t 只用 ≤t 数据）：
  区域限定(三级降级) → 背景EMA(α, 无人更新/有人冻结)
  → F1 残差能量dB / F2 主bin相位std / F3 呼吸频段占比 / F4 帧间跳变(预留)
  → gate(4s窗F1n均值) 乘性门控 F2/F3/F4
  → score = w1·F1n + gate·(w2·F2n + w3·F3n + w4·F4n)
  → 双阈值滞回(confirm 帧防抖 + 静态门) → presence；conf = clip(0.5+0.49·|2·score−1|)
  → quality: NaN→invalid_frame；区域平均功率<−60dB→low_signal

相对 demo（M1-demo-0.1）的差异：
  * 权重 0.45/0.30/0.25（demo 0.6/0.2/0.2 会让静态书包单独过阈 0.5，违反 G1 条件 b）
  * 窗口按 timestamp 计算（支持非 1 Hz 帧率；1 Hz 下与 demo 等价）
  * 实数输入退化：F2/F3 置 0、权重在 (F1,F4) 内重归一、无门控、无静态门
  * 质量列真实输出（invalid_frame / low_signal → presence=-1）
  * 滞回确认帧 confirm（默认 5 = 4 s 特征窗帧数 + 1）：突发静态物（书包放置）在
    特征窗滑出前有 3-4 帧全特征假阳性瞬态（gate 爬升 + F2 相位跳变 + F3 阶跃频谱），
    无法支撑 5 帧连续过阈 → 不翻转，背景 EMA 随后吸收（G1-b 的机制保证）
  * 静态门 static_gate.f23_floor（默认 0.10）：有人态下 4 s 窗 (F2n+F3n) 均值低于
    地板 → 强制释放 presence（依赖呼吸微动作为持续占用的证据；覆盖"离座留物"
    锁死路径——人走包留时冻结背景会把 F1n 钉在 1.0）
  * 背景 background.mode（v1.2 新增，默认 "ema" 保持 v1.1 行为不变）：
    - "ema"：v1.1 原逻辑（无人帧 EMA 更新）
    - "snapshot_freeze"：入座前累积运行均值为空座快照，检测到占用（presence=1）
      立即冻结（累积不满 snapshot_frames 时用 K_eff 部分窗口）；释放后累计
      re_snapshot_confirm 个「证据级空帧」（score ≤ t_off）→ 自动重建快照。
      累积期只吸收证据级空帧（score ≤ t_off）——回座未确认的人帧不入快照；
      hysteresis 波峰帧既不计数也不重置，入座未确认段不会误触发重建。
      动机（T01/P01 实测 §4）：EMA 吸收入座者（α=0.2 → 静坐 72 s 才触发）
      或滞后漂移（α=0.02 → 空座全程 FP）；冻结快照使空座残差与有人残差在
      bin1/bin3 组合上间隔 ≥3.4 dB。前提：录制开始时座位为空（或含空座引导段）
"""
import numpy as np

from .region import resolve_region

# 默认参数（与 config/M1_default.json 一致；缺失键时回退）
_DEFAULTS = {
    "weights": {"f1_residual": 0.45, "f2_phase_std": 0.30,
                "f3_breath_band": 0.25, "f4_motion": 0.0},
    "thresholds": {"t_on": 0.5, "t_off": 0.35},
    "windows": {"gate_s": 4.0, "f2_s": 4.0, "f3_s": 4.0, "bg_warmup_s": 4.0},
    "f1_db_map": [-9.0, 8.0],
    "f2_norm_rad": 0.30,
    "f3_band_hz": [0.1, 0.5],
    "f3_gain": 1.5,
    "f3_denominator": "total",   # 'non_static': 排除 DC bin（真实设备动静比 60+ dB 时用）
    "f4_jump_db": 12.0,
    "background": {"alpha": 0.2, "mode": "ema",
                   "snapshot_frames": 50, "re_snapshot_confirm": 90},
    "confidence": {"warmup_cap": 0.80},
    "hysteresis_confirm_frames": 5,
    "static_gate": {"f23_floor": 0.10},
    "quality": {"low_signal_db": -60.0},
}


def _cfg_get(cfg, path, default):
    """按点路径取配置，缺失回退 default。"""
    node = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _window_lo(ts, t, win_s, lo):
    """推进窗口左端：保持 ts[t] - ts[lo] < win_s（1 Hz、4 s 窗 → 恰好 4 样本，与 demo 等价）。"""
    while lo < t and ts[t] - ts[lo] >= win_s:
        lo += 1
    return lo


def presence_from_scores(scores, t_on, t_off, confirm_frames=1, static_flag=None):
    """对分数序列重放双阈值滞回（G1 阈值扫描复用）。

    static_flag: 与 scores 等长的 bool 序列，True 帧强制 want=0（静态门重放，
    判决顺序与 run_m1 内联实现一致）。
    """
    scores = np.asarray(scores, dtype=np.float64)
    n = len(scores)
    out = np.zeros(n, dtype=np.int64)
    state, cand, cand_state = 0, 0, 0
    for t in range(n):
        s = scores[t]
        if static_flag is not None and static_flag[t]:
            want = 0
        elif s >= t_on:
            want = 1
        elif s <= t_off:
            want = 0
        else:
            want = state
        if want != state:
            if want == cand_state:
                cand += 1
            else:
                cand_state, cand = want, 1
            if cand >= confirm_frames:
                state, cand = want, 0
        else:
            cand = 0
        out[t] = state
    return out


def run_m1(raw_data, timestamps, meta, cfg=None, region_params=None):
    """M1 主入口。

    参数
    ----
    raw_data : array [N, range]
        已轴规整的距离剖面（复数=完整模式；实数=退化模式）。2dfft 的 doppler 合并
        由 detect.py 完成后再传入。
    timestamps : float64 [N]
    meta : dict（<rid>_meta.json 内容）
    cfg : dict 或 None（config/M1_default.json 内容，None 用内置默认）
    region_params : dict 或 None

    返回
    ----
    dict: presence(int[N]), conf(list[float|None]), quality(list[str]),
          score/f1n/f2n/f3n/f4n/gate(float[N]), region(dict), degraded(bool),
          warnings(list[str]), state_trace(int[N])
    """
    cfg = cfg or {}
    X = np.asarray(raw_data)
    if X.ndim == 1:
        X = X[:, None]
    n, nb = X.shape
    ts = np.asarray(timestamps, dtype=np.float64)

    # ---- 参数（含回退） ----
    w1 = float(_cfg_get(cfg, ["weights", "f1_residual"], _DEFAULTS["weights"]["f1_residual"]))
    w2 = float(_cfg_get(cfg, ["weights", "f2_phase_std"], _DEFAULTS["weights"]["f2_phase_std"]))
    w3 = float(_cfg_get(cfg, ["weights", "f3_breath_band"], _DEFAULTS["weights"]["f3_breath_band"]))
    w4 = float(_cfg_get(cfg, ["weights", "f4_motion"], _DEFAULTS["weights"]["f4_motion"]))
    t_on = float(_cfg_get(cfg, ["thresholds", "t_on"], _DEFAULTS["thresholds"]["t_on"]))
    t_off = float(_cfg_get(cfg, ["thresholds", "t_off"], _DEFAULTS["thresholds"]["t_off"]))
    win_gate = float(_cfg_get(cfg, ["windows", "gate_s"], _DEFAULTS["windows"]["gate_s"]))
    win_f2 = float(_cfg_get(cfg, ["windows", "f2_s"], _DEFAULTS["windows"]["f2_s"]))
    win_f3 = float(_cfg_get(cfg, ["windows", "f3_s"], _DEFAULTS["windows"]["f3_s"]))
    warmup_s = float(_cfg_get(cfg, ["windows", "bg_warmup_s"], _DEFAULTS["windows"]["bg_warmup_s"]))
    f1_lo, f1_hi = (float(v) for v in _cfg_get(cfg, ["f1_db_map"], _DEFAULTS["f1_db_map"]))
    f2_norm = float(_cfg_get(cfg, ["f2_norm_rad"], _DEFAULTS["f2_norm_rad"]))
    band_lo, band_hi = (float(v) for v in _cfg_get(cfg, ["f3_band_hz"], _DEFAULTS["f3_band_hz"]))
    f3_gain = float(_cfg_get(cfg, ["f3_gain"], _DEFAULTS["f3_gain"]))
    f3_denom = str(_cfg_get(cfg, ["f3_denominator"], "total"))
    if f3_denom not in ("total", "non_static"):
        raise ValueError(f"f3_denominator must be 'total' or 'non_static', got {f3_denom!r}")
    f4_jump = float(_cfg_get(cfg, ["f4_jump_db"], _DEFAULTS["f4_jump_db"]))
    f1_bins = str(_cfg_get(cfg, ["f1_bins"], "region"))
    if f1_bins not in ("region", "non_main"):
        raise ValueError(f"f1_bins must be 'region' or 'non_main', got {f1_bins!r}")
    alpha = float(_cfg_get(cfg, ["background", "alpha"], _DEFAULTS["background"]["alpha"]))
    bg_mode = str(_cfg_get(cfg, ["background", "mode"], _DEFAULTS["background"]["mode"]))
    if bg_mode not in ("ema", "snapshot_freeze"):
        raise ValueError(f"background.mode must be 'ema' or 'snapshot_freeze', got {bg_mode!r}")
    snap_frames = max(1, int(_cfg_get(cfg, ["background", "snapshot_frames"],
                                      _DEFAULTS["background"]["snapshot_frames"])))
    re_snap_confirm = max(1, int(_cfg_get(cfg, ["background", "re_snapshot_confirm"],
                                          _DEFAULTS["background"]["re_snapshot_confirm"])))
    warmup_cap = float(_cfg_get(cfg, ["confidence", "warmup_cap"], _DEFAULTS["confidence"]["warmup_cap"]))
    confirm = max(1, int(_cfg_get(cfg, ["hysteresis_confirm_frames"],
                                 _DEFAULTS["hysteresis_confirm_frames"])))
    f23_floor = float(_cfg_get(cfg, ["static_gate", "f23_floor"],
                               _DEFAULTS["static_gate"]["f23_floor"]))
    low_db = float(_cfg_get(cfg, ["quality", "low_signal_db"], _DEFAULTS["quality"]["low_signal_db"]))
    low_floor = 10.0 ** (low_db / 10.0)

    region = resolve_region(meta, region_params, nb)
    lo_r, hi_r = region["lo"], region["hi"]
    warnings = list(region["warnings"])

    complex_input = bool(np.iscomplexobj(X))
    degraded = not complex_input
    if degraded:
        warnings.append(
            "real-valued input: phase features unavailable -> "
            "F2/F3 disabled, weights renormalized over (F1,F4), no gate")
        w2 = w3 = 0.0
        active = w1 + w4
        if active <= 0:
            warnings.append("all active weights are zero -> falling back to F1-only")
            w1, active = 1.0, 1.0
        f23_floor = None  # 静态门依赖相位特征，退化输入禁用

    # ---- 主 bin 解析（退化全距离时：warmup 期结束前用累计能量锁定，保持因果） ----
    main_bin = region["main_bin"]
    main_locked = main_bin is not None
    if main_bin is None:
        warnings.append("main_bin is None (full-range degraded): locking per accumulated "
                        "region power over the warmup window; F2/F3=0 before the lock")

    # ---- 状态与轨迹 ----
    presence = np.zeros(n, dtype=np.int64)
    quality = ["ok"] * n
    conf: list = [None] * n
    f1n = np.zeros(n); f2n = np.zeros(n); f3n = np.zeros(n)
    f4n = np.zeros(n); gate = np.zeros(n); score = np.zeros(n)
    f23_win = np.zeros(n)

    bg = X[0].astype(np.complex128 if complex_input else np.float64).copy()
    # snapshot_freeze 状态（v1.2）：累积器/计数/冻结标志/连续无人帧
    snap_acc = np.zeros(nb, dtype=np.complex128 if complex_input else np.float64)
    snap_cnt = 0
    snap_frozen = False
    snap_events = 0
    empty_run = 0
    state, cand, cand_state = 0, 0, 0
    lo_gate = lo_f2 = lo_f3 = 0
    prev_f1_db = None

    # 主 bin 相位序列（锁定后才能算 unwrap；unwrap 本身是顺序因果变换）
    main_unw = None

    dt_med = float(np.median(np.diff(ts))) if n >= 2 else 1.0
    if not dt_med > 0:
        dt_med = 1.0

    warmup_power = np.zeros(nb)  # main_bin 未锁定时的 warmup 累计能量

    for t in range(n):
        Xt = X[t]
        reg = Xt[lo_r:hi_r + 1]

        # ---- 质量判定 ----
        finite_reg = np.all(np.isfinite(reg)) and np.all(np.isfinite(reg.real))
        mean_power = float(np.mean(np.abs(reg.astype(np.complex128)) ** 2)) if finite_reg else 0.0
        if not finite_reg:
            quality[t] = "invalid_frame"
            presence[t] = -1
            conf[t] = None
            score[t] = 0.0
            continue
        if mean_power < low_floor:
            quality[t] = "low_signal"
            presence[t] = -1
            conf[t] = None
            score[t] = 0.0
            continue

        # ---- main_bin 未锁定的退化路径：warmup 期累计，锁死后固定 ----
        if main_bin is None:
            warmup_power += np.abs(Xt.astype(np.complex128)) ** 2
            if ts[t] >= warmup_s and not main_locked:
                main_bin = int(np.argmax(warmup_power[lo_r:hi_r + 1])) + lo_r
                main_locked = True

        # ---- F1：区域残差能量（背景去除后） ----
        resid_full = reg.astype(np.complex128) - bg[lo_r:hi_r + 1].astype(np.complex128)
        if f1_bins == "non_main" and main_locked:
            # v1.2 真机模式：排除主 bin——空座环境低频相位扰动集中于主 bin，
            # 人体静态反射分布在侧 bin（T01/P01 probe2/3 实测：主 bin 空座/有人
            # 分布重叠，侧 bin max(b1,b3) 间隔 ≥3.4 dB）。主 bin 仍由 F2/F3 监测。
            resid = np.delete(resid_full, main_bin - lo_r)
        else:
            resid = resid_full
        f1 = float(np.sum(np.abs(resid) ** 2))
        f1_db = 10.0 * np.log10(f1 + 1e-12)
        f1n[t] = np.clip((f1_db - f1_lo) / (f1_hi - f1_lo), 0.0, 1.0)
        f4n[t] = 0.0 if prev_f1_db is None else float(
            np.clip(abs(f1_db - prev_f1_db) / f4_jump, 0.0, 1.0))
        prev_f1_db = f1_db

        # ---- F2/F3：主 bin 相位与频段（复数且已锁定主 bin 才有） ----
        if complex_input and main_locked:
            if main_unw is None:
                series = X[:, main_bin].astype(np.complex128)
                main_unw = np.unwrap(np.angle(series))
            lo_f2 = _window_lo(ts, t, win_f2, lo_f2)
            seg = main_unw[lo_f2:t + 1]
            if len(seg) >= 2:
                f2n[t] = float(np.clip(np.std(seg) / f2_norm, 0.0, 1.0))
            lo_f3 = _window_lo(ts, t, win_f3, lo_f3)
            z = X[lo_f3:t + 1, main_bin].astype(np.complex128)
            if len(z) >= 2:
                Z = np.fft.fft(z) / len(z)
                freqs = np.fft.fftfreq(len(z), d=dt_med)
                band_mask = (np.abs(freqs) >= band_lo) & (np.abs(freqs) < band_hi)
                if np.any(band_mask):
                    denom = np.sum(np.abs(Z) ** 2)
                    if f3_denom == "non_static":
                        # 真实设备模式：静态杂波 DC 主导总分母（60+ dB 动静比），
                        # 占比结构性趋零；排除 DC bin 后衡量"非静态能量中的呼吸占比"
                        denom -= float(np.abs(Z[0]) ** 2)
                    ratio = float(np.sum(np.abs(Z[band_mask]) ** 2) /
                                  (denom + 1e-12))
                    f3n[t] = float(np.clip(ratio * f3_gain, 0.0, 1.0))

        # ---- gate 与得分 ----
        lo_gate = _window_lo(ts, t, win_gate, lo_gate)
        gate[t] = float(np.mean(f1n[lo_gate:t + 1]))
        if complex_input and main_locked:
            # 静态门证据：4 s 窗 (F2n+F3n) 均值（呼吸微动是否存在）
            f23_win[t] = float(np.mean((f2n + f3n)[lo_gate:t + 1]))
        if degraded:
            score[t] = (w1 * f1n[t] + w4 * f4n[t]) / active
        else:
            score[t] = w1 * f1n[t] + gate[t] * (w2 * f2n[t] + w3 * f3n[t] + w4 * f4n[t])

        # ---- 双阈值滞回（confirm 帧防抖 + 静态门；confirm=1 等价 demo 即时翻转） ----
        s = score[t]
        static = f23_floor is not None and main_locked and f23_win[t] < f23_floor
        if static:
            want = 0
        elif s >= t_on:
            want = 1
        elif s <= t_off:
            want = 0
        else:
            want = state
        if want != state:
            if want == cand_state:
                cand += 1
            else:
                cand_state, cand = want, 1
            if cand >= confirm:
                state, cand = want, 0
        else:
            cand = 0
        presence[t] = state

        # ---- 置信度（demo 公式；warmup 封顶） ----
        c = float(np.clip(0.5 + 0.49 * abs(2.0 * s - 1.0), 0.5, 0.99))
        if ts[t] < warmup_s:
            c = min(c, warmup_cap)
        conf[t] = c

        # ---- 背景更新：判决之后（因果） ----
        if bg_mode == "ema":
            # v1.1 原逻辑：仅无人帧 EMA 更换（保持行为完全不变）
            if state == 0:
                bg = (1.0 - alpha) * bg + alpha * (Xt.astype(np.complex128)
                                                   if complex_input else Xt.astype(np.float64))
        else:
            # v1.2 snapshot_freeze：入座前累积空座快照，检测到占用即冻结；
            # 释放后累计 re_snapshot_confirm 个「证据级空帧」（score ≤ t_off）→ 重建
            # 快照（漂移/环境变化自适应）。hysteresis 波峰帧（t_off < score < t_on）
            # 既不计数也不重置——入座未确认段/空座瞬态波峰不会误触发重建。
            # 累积期（初始或重建）只吸收证据级空帧：回座未确认的人帧（score > t_off）
            # 不入快照；冻结可发生在任意 K_eff（K_eff=0 表示保留旧快照）。
            # bg 在累积期为运行均值（随帧收敛），冻结后完全静止。
            if state == 1:
                if not snap_frozen:
                    snap_frozen = True  # 占用确认 → 冻结（部分窗口亦可）
                    snap_events += 1
                    if snap_cnt > 0:
                        warnings.append(f"background: snapshot #{snap_events} frozen "
                                        f"(K_eff={snap_cnt} frames)")
                    else:
                        warnings.append(f"background: snapshot #{snap_events} frozen "
                                        f"(K_eff=0, previous snapshot kept)")
                empty_run = 0
            else:
                if not snap_frozen:
                    if s <= t_off:
                        snap_acc = snap_acc + Xt
                        snap_cnt += 1
                        bg = snap_acc / snap_cnt
                    if snap_cnt >= snap_frames:
                        snap_frozen = True
                        snap_events += 1
                        warnings.append(f"background: snapshot #{snap_events} frozen "
                                        f"(K_eff={snap_cnt} frames)")
                else:
                    if s <= t_off:
                        empty_run += 1
                    if empty_run >= re_snap_confirm:
                        # 释放后证据级空帧足够久 → 重建快照
                        snap_frozen = False
                        snap_acc = np.zeros(nb, dtype=np.complex128
                                            if complex_input else np.float64)
                        snap_cnt = 0
                        empty_run = 0
                        warnings.append(f"background: snapshot #{snap_events} rebuild "
                                        f"triggered ({re_snap_confirm} empty-evidence frames)")

    return {
        "presence": presence,
        "conf": conf,
        "quality": quality,
        "score": score,
        "f1n": f1n, "f2n": f2n, "f3n": f3n, "f4n": f4n,
        "gate": gate,
        "f23_win": f23_win,
        "region": region,
        "degraded": degraded,
        "warnings": warnings,
        "bg_mode": bg_mode,
    }

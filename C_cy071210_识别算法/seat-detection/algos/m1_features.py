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

架构（在线化重构，行为逐字节不变）：
  * 全部跨帧状态封装为 M1State；逐帧推进为 _m1_step(state, Xt, ts_t)。
  * 离线 run_m1 = 构建 state + 循环调用 _m1_step；在线 SeatDetector
    （algos/online_detector.py）同样调用 _m1_step——同一代码路径保证离线/在线一致。
  * 原实现中 main_unw = np.unwrap(全序列) 在锁定主 bin 时一次性构建；np.unwrap
    是顺序递推（任意前缀与全序列前缀逐位一致），改为逐帧对原始相位全链重算，
    浮点行为与原实现完全一致（std 对窗口局部 unwrap 的近似方案会引入累计偏移
    的舍入差异，已弃用）。相位链含全部帧（含 invalid/low_signal 早退帧），
    与原实现对全序列 unwrap 的行为一致。
  * F3 的 dt_med 由调用方传入（离线 = median(diff(ts))；在线 = 会话帧周期），
    均匀时戳下二者相等。
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


def _window_lo_hist(ts_hist, t, win_s, lo):
    """与 _window_lo 相同的推进逻辑，但作用于 list 时间戳历史（在线路径共用）。"""
    while lo < t and ts_hist[t] - ts_hist[lo] >= win_s:
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


class M1State:
    """M1 逐帧状态（离线 run_m1 与在线 SeatDetector 共用；严格因果）。

    构造参数 region/nb/complex_input/dt_med 由调用方判定后传入：
      * 离线：nb/complex_input 取自全量数组，dt_med = median(diff(ts))。
      * 在线：nb 取自会话 meta（raw_shape[-1]），complex_input 取自首帧，
        dt_med 取自会话帧周期（B 侧提供；均匀时戳下与离线 median 一致）。
    """

    __slots__ = (
        # 参数（_resolve_params 解析后传入）
        "w1", "w2", "w3", "w4", "t_on", "t_off",
        "win_gate", "win_f2", "win_f3", "warmup_s",
        "f1_lo", "f1_hi", "f2_norm", "band_lo", "band_hi", "f3_gain", "f3_denom",
        "f4_jump", "f1_bins", "alpha", "bg_mode", "snap_frames", "re_snap_confirm",
        "warmup_cap", "confirm", "f23_floor", "low_floor",
        # 环境
        "region", "lo_r", "hi_r", "nb", "complex_input", "dt_med", "degraded",
        # 输出轨迹（list；离线最后转 ndarray 打包）
        "presence", "conf", "quality", "f1n", "f2n", "f3n", "f4n",
        "gate", "score", "f23_win",
        # 滑窗与历史
        "ts_hist", "x_hist", "f23s_hist",
        "lo_gate", "lo_f2", "lo_f3", "active_sum",
        # 背景/快照
        "bg", "snap_acc", "snap_cnt", "snap_frozen", "snap_events", "empty_run",
        # 滞回/主bin/相位链
        "state", "cand", "cand_state", "prev_f1_db",
        "main_bin", "main_locked", "warmup_power", "raw_ang_hist",
        # 其他
        "warnings",
    )


def _resolve_params(cfg):
    """解析 config（缺失回退默认）；返回 dict。与旧 run_m1 逐项一致。"""
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
    return {
        "w1": w1, "w2": w2, "w3": w3, "w4": w4, "t_on": t_on, "t_off": t_off,
        "win_gate": win_gate, "win_f2": win_f2, "win_f3": win_f3,
        "warmup_s": warmup_s, "f1_lo": f1_lo, "f1_hi": f1_hi, "f2_norm": f2_norm,
        "band_lo": band_lo, "band_hi": band_hi, "f3_gain": f3_gain,
        "f3_denom": f3_denom, "f4_jump": f4_jump, "f1_bins": f1_bins,
        "alpha": alpha, "bg_mode": bg_mode, "snap_frames": snap_frames,
        "re_snap_confirm": re_snap_confirm, "warmup_cap": warmup_cap,
        "confirm": confirm, "f23_floor": f23_floor,
        "low_floor": 10.0 ** (low_db / 10.0),
    }


def make_m1_state(cfg, meta, region_params, nb, complex_input, dt_med):
    """构建 M1State（离线/在线共用）。区域解析与退化处理在此一次完成。"""
    prm = _resolve_params(cfg or {})
    st = M1State()
    for k, v in prm.items():
        setattr(st, k, v)
    region = resolve_region(meta, region_params, nb)
    st.region = region
    st.lo_r, st.hi_r = int(region["lo"]), int(region["hi"])
    st.nb = int(nb)
    st.complex_input = bool(complex_input)
    dt_med = float(dt_med) if dt_med else 1.0
    st.dt_med = dt_med if dt_med > 0 else 1.0
    st.degraded = not st.complex_input

    if st.degraded:
        st.warnings = list(region["warnings"]) + [
            "real-valued input: phase features unavailable -> "
            "F2/F3 disabled, weights renormalized over (F1,F4), no gate"]
        st.w2 = 0.0
        st.w3 = 0.0
        active = st.w1 + st.w4
        if active <= 0:
            st.warnings.append("all active weights are zero -> falling back to F1-only")
            st.w1, active = 1.0, 1.0
        st.active_sum = active  # 构造期归一化分母（与原实现 active 局部变量一致）
        st.f23_floor = None  # 静态门依赖相位特征，退化输入禁用
    else:
        st.warnings = list(region["warnings"])
        st.active_sum = 0.0  # 非退化模式不使用

    st.main_bin = region["main_bin"]
    st.main_locked = st.main_bin is not None
    if st.main_bin is None:
        st.warnings.append("main_bin is None (full-range degraded): locking per accumulated "
                           "region power over the warmup window; F2/F3=0 before the lock")

    # 输出轨迹
    st.presence, st.conf, st.quality = [], [], []
    st.f1n, st.f2n, st.f3n, st.f4n = [], [], [], []
    st.gate, st.score, st.f23_win = [], [], []
    # 滑窗与历史
    st.ts_hist, st.x_hist = [], []
    st.f23s_hist = []
    st.lo_gate = st.lo_f2 = st.lo_f3 = 0
    # 背景/快照
    st.bg = None  # 首帧初始化为该帧 copy（与离线 bg = X[0].copy() 等价）
    st.snap_acc = np.zeros(st.nb, dtype=np.complex128 if st.complex_input else np.float64)
    st.snap_cnt, st.snap_frozen, st.snap_events, st.empty_run = 0, False, 0, 0
    # 滞回/主bin/相位链
    st.state, st.cand, st.cand_state = 0, 0, 0
    st.prev_f1_db = None
    st.warmup_power = np.zeros(st.nb)
    st.raw_ang_hist = None  # 主 bin 锁定帧起维护（含回补历史）
    return st


def _m1_step(st, Xt, ts_t):
    """单帧推进（严格因果；与重构前 run_m1 循环体逐行等价）。

    Xt  : 单帧 range profile [nb]（complex 或 real，须与构造时 complex_input 一致）
    ts_t: 本帧 timestamp（秒，相对录制/会话起点）
    """
    t = len(st.ts_hist)
    st.ts_hist.append(ts_t)
    if st.complex_input:
        Xt = np.asarray(Xt).astype(np.complex128)
    else:
        Xt = np.asarray(Xt).astype(np.float64)
    st.x_hist.append(Xt)
    if st.bg is None:  # 首帧：与离线 bg = X[0].copy() 等价
        st.bg = Xt.copy()

    # 相位链维护：主 bin 锁定后每帧记录原始相位（含本帧），锁定帧回补全部历史。
    # 质量早退帧的相位同样进链（与原实现对全序列 unwrap 的行为一致）。
    if st.complex_input and st.main_locked:
        if st.raw_ang_hist is None:
            st.raw_ang_hist = [float(np.angle(row[st.main_bin])) for row in st.x_hist]
        else:
            st.raw_ang_hist.append(float(np.angle(Xt[st.main_bin])))

    # 输出占位（与原实现零初始化一致）
    st.f1n.append(0.0); st.f2n.append(0.0); st.f3n.append(0.0)
    st.f4n.append(0.0); st.gate.append(0.0); st.score.append(0.0)
    st.f23_win.append(0.0)
    st.presence.append(0)
    st.conf.append(None)
    st.quality.append("ok")

    reg = Xt[st.lo_r:st.hi_r + 1]

    # ---- 质量判定 ----
    finite_reg = np.all(np.isfinite(reg)) and np.all(np.isfinite(reg.real))
    mean_power = float(np.mean(np.abs(reg.astype(np.complex128)) ** 2)) if finite_reg else 0.0
    if not finite_reg or mean_power < st.low_floor:
        if not finite_reg:
            st.quality[t] = "invalid_frame"
        else:
            st.quality[t] = "low_signal"
        st.presence[t] = -1
        st.conf[t] = None
        st.score[t] = 0.0
        st.f23s_hist.append(st.f2n[t] + st.f3n[t])
        return

    # ---- main_bin 未锁定的退化路径：warmup 期累计，锁死后固定 ----
    if st.main_bin is None:
        st.warmup_power += np.abs(Xt.astype(np.complex128)) ** 2
        if ts_t >= st.warmup_s and not st.main_locked:
            st.main_bin = int(np.argmax(st.warmup_power[st.lo_r:st.hi_r + 1])) + st.lo_r
            st.main_locked = True
            # 锁定帧：回补历史相位链（原实现对全序列 unwrap 的等价前缀）
            st.raw_ang_hist = [float(np.angle(row[st.main_bin])) for row in st.x_hist]

    # ---- F1：区域残差能量（背景去除后） ----
    resid_full = reg.astype(np.complex128) - st.bg[st.lo_r:st.hi_r + 1].astype(np.complex128)
    if st.f1_bins == "non_main" and st.main_locked:
        # v1.2 真机模式：排除主 bin——空座环境低频相位扰动集中于主 bin，
        # 人体静态反射分布在侧 bin（T01/P01 probe2/3 实测：主 bin 空座/有人
        # 分布重叠，侧 bin max(b1,b3) 间隔 ≥3.4 dB）。主 bin 仍由 F2/F3 监测。
        resid = np.delete(resid_full, st.main_bin - st.lo_r)
    else:
        resid = resid_full
    f1 = float(np.sum(np.abs(resid) ** 2))
    f1_db = 10.0 * np.log10(f1 + 1e-12)
    st.f1n[t] = np.clip((f1_db - st.f1_lo) / (st.f1_hi - st.f1_lo), 0.0, 1.0)
    st.f4n[t] = 0.0 if st.prev_f1_db is None else float(
        np.clip(abs(f1_db - st.prev_f1_db) / st.f4_jump, 0.0, 1.0))
    st.prev_f1_db = f1_db

    # ---- F2/F3：主 bin 相位与频段（复数且已锁定主 bin 才有） ----
    if st.complex_input and st.main_locked:
        # 全链重算 unwrap（顺序递推前缀一致；窗口局部 unwrap 会引入累计偏移舍入差，不可用）
        main_unw = np.unwrap(np.asarray(st.raw_ang_hist))
        lo_f2 = _window_lo_hist(st.ts_hist, t, st.win_f2, st.lo_f2)
        st.lo_f2 = lo_f2
        seg = main_unw[lo_f2:t + 1]
        if len(seg) >= 2:
            st.f2n[t] = float(np.clip(np.std(seg) / st.f2_norm, 0.0, 1.0))
        lo_f3 = _window_lo_hist(st.ts_hist, t, st.win_f3, st.lo_f3)
        st.lo_f3 = lo_f3
        z = np.asarray([row[st.main_bin] for row in st.x_hist[lo_f3:t + 1]])
        if len(z) >= 2:
            Z = np.fft.fft(z) / len(z)
            freqs = np.fft.fftfreq(len(z), d=st.dt_med)
            band_mask = (np.abs(freqs) >= st.band_lo) & (np.abs(freqs) < st.band_hi)
            if np.any(band_mask):
                denom = np.sum(np.abs(Z) ** 2)
                if st.f3_denom == "non_static":
                    # 真实设备模式：静态杂波 DC 主导总分母（60+ dB 动静比），
                    # 占比结构性趋零；排除 DC bin 后衡量"非静态能量中的呼吸占比"
                    denom -= float(np.abs(Z[0]) ** 2)
                ratio = float(np.sum(np.abs(Z[band_mask]) ** 2) /
                             (denom + 1e-12))
                st.f3n[t] = float(np.clip(ratio * st.f3_gain, 0.0, 1.0))

    # ---- 滑窗历史（先于 gate：f23_win 窗口含本帧，与原实现 (f2n+f3n)[lo_gate:t+1] 一致；
    #      gate 窗口直接读 f1n 轨迹，t+1 元素含本帧） ----
    st.f23s_hist.append(st.f2n[t] + st.f3n[t])

    # ---- gate 与得分 ----
    lo_gate = _window_lo_hist(st.ts_hist, t, st.win_gate, st.lo_gate)
    st.lo_gate = lo_gate
    st.gate[t] = float(np.mean(np.asarray(st.f1n[lo_gate:t + 1])))
    if st.complex_input and st.main_locked:
        # 静态门证据：4 s 窗 (F2n+F3n) 均值（呼吸微动是否存在）
        st.f23_win[t] = float(np.mean(np.asarray(st.f23s_hist[lo_gate:t + 1])))
    if st.degraded:
        st.score[t] = (st.w1 * st.f1n[t] + st.w4 * st.f4n[t]) / st.active_sum
    else:
        st.score[t] = st.w1 * st.f1n[t] + st.gate[t] * (
            st.w2 * st.f2n[t] + st.w3 * st.f3n[t] + st.w4 * st.f4n[t])

    # ---- 双阈值滞回（confirm 帧防抖 + 静态门；confirm=1 等价 demo 即时翻转） ----
    s = st.score[t]
    static = st.f23_floor is not None and st.main_locked and st.f23_win[t] < st.f23_floor
    if static:
        want = 0
    elif s >= st.t_on:
        want = 1
    elif s <= st.t_off:
        want = 0
    else:
        want = st.state
    if want != st.state:
        if want == st.cand_state:
            st.cand += 1
        else:
            st.cand_state, st.cand = want, 1
        if st.cand >= st.confirm:
            st.state, st.cand = want, 0
    else:
        st.cand = 0
    st.presence[t] = st.state

    # ---- 置信度（demo 公式；warmup 封顶） ----
    c = float(np.clip(0.5 + 0.49 * abs(2.0 * s - 1.0), 0.5, 0.99))
    if ts_t < st.warmup_s:
        c = min(c, st.warmup_cap)
    st.conf[t] = c

    # ---- 背景更新：判决之后（因果） ----
    if st.bg_mode == "ema":
        # v1.1 原逻辑：仅无人帧 EMA 更换（保持行为完全不变）
        if st.state == 0:
            st.bg = (1.0 - st.alpha) * st.bg + st.alpha * Xt
    else:
        # v1.2 snapshot_freeze：入座前累积空座快照，检测到占用即冻结；
        # 释放后累计 re_snapshot_confirm 个「证据级空帧」（score ≤ t_off）→ 重建
        # 快照（漂移/环境变化自适应）。hysteresis 波峰帧（t_off < score < t_on）
        # 既不计数也不重置——入座未确认段/空座瞬态波峰不会误触发重建。
        # 累积期（初始或重建）只吸收证据级空帧：回座未确认的人帧（score > t_off）
        # 不入快照；冻结可发生在任意 K_eff（K_eff=0 表示保留旧快照）。
        # bg 在累积期为运行均值（随帧收敛），冻结后完全静止。
        if st.state == 1:
            if not st.snap_frozen:
                st.snap_frozen = True  # 占用确认 → 冻结（部分窗口亦可）
                st.snap_events += 1
                if st.snap_cnt > 0:
                    st.warnings.append(f"background: snapshot #{st.snap_events} frozen "
                                       f"(K_eff={st.snap_cnt} frames)")
                else:
                    st.warnings.append(f"background: snapshot #{st.snap_events} frozen "
                                       f"(K_eff=0, previous snapshot kept)")
            st.empty_run = 0
        else:
            if not st.snap_frozen:
                if s <= st.t_off:
                    st.snap_acc = st.snap_acc + Xt
                    st.snap_cnt += 1
                    st.bg = st.snap_acc / st.snap_cnt
                if st.snap_cnt >= st.snap_frames:
                    st.snap_frozen = True
                    st.snap_events += 1
                    st.warnings.append(f"background: snapshot #{st.snap_events} frozen "
                                       f"(K_eff={st.snap_cnt} frames)")
            else:
                if s <= st.t_off:
                    st.empty_run += 1
                if st.empty_run >= st.re_snap_confirm:
                    # 释放后证据级空帧足够久 → 重建快照
                    st.snap_frozen = False
                    st.snap_acc = np.zeros(st.nb,
                                           dtype=np.complex128 if st.complex_input else np.float64)
                    st.snap_cnt = 0
                    st.empty_run = 0
                    st.warnings.append(f"background: snapshot #{st.snap_events} rebuild "
                                       f"triggered ({st.re_snap_confirm} empty-evidence frames)")


def run_m1(raw_data, timestamps, meta, cfg=None, region_params=None):
    """M1 主入口（离线；与在线 SeatDetector 共享 _m1_step 代码路径）。

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
          score/f1n/f2n/f3n/f4n/gate/f23_win(float[N]), region(dict), degraded(bool),
          warnings(list[str]), bg_mode(str)
    """
    X = np.asarray(raw_data)
    if X.ndim == 1:
        X = X[:, None]
    n, nb = X.shape
    ts = np.asarray(timestamps, dtype=np.float64)

    dt_med = float(np.median(np.diff(ts))) if n >= 2 else 1.0
    if not dt_med > 0:
        dt_med = 1.0

    st = make_m1_state(cfg, meta, region_params, nb,
                       complex_input=np.iscomplexobj(X), dt_med=dt_med)
    for t in range(n):
        _m1_step(st, X[t], float(ts[t]))

    return {
        "presence": np.asarray(st.presence, dtype=np.int64),
        "conf": st.conf,
        "quality": st.quality,
        "score": np.asarray(st.score),
        "f1n": np.asarray(st.f1n), "f2n": np.asarray(st.f2n),
        "f3n": np.asarray(st.f3n), "f4n": np.asarray(st.f4n),
        "gate": np.asarray(st.gate),
        "f23_win": np.asarray(st.f23_win),
        "region": st.region,
        "degraded": st.degraded,
        "warnings": st.warnings,
        "bg_mode": st.bg_mode,
    }

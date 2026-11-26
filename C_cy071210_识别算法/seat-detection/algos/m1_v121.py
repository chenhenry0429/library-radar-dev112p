# -*- coding: utf-8 -*-
"""M1 v1.2.1 —— 开场空座标定 + 奇偶 chirp 分离 + Bin1 微动判空（C 修复任务单）。

背景（v1.2 在 T01 真实数据上的两个结构性缺陷，见
`outputs/t01-round2/v1.2_round2_defect_analysis.md`）：
  1. **开场锁死**：背景 ``bg = X[0]`` 只由帧 0 决定；frame-0 的奇/偶 chirp 配比
     与完整帧不同（tx_polling=1 时奇 ≈16200 / 偶 ≈1450 counts，比例 ~11×），
     简单均值给种子帧 +139 counts 偏置，配合快初始衰减，1–2 s 内 f1 饱和 →
     presence 钉死为 1，空座全程误报（11/12 组 93.8–98.7%）。
  2. **离座不判空**：占用态冻结背景后，设备慢漂移（−1.9~−6.2 %/min）让
     |X−bg| 只增不减，score 永不回落到 t_off → 9/9 离座事件零释放。

v1.2.1 的三点修复（与组员任务单逐条对应）：
  R1 **奇偶 chirp 分开处理**：输入按 chirp 序号奇/偶分成两条剖面
     ``[frame, parity, range]``，各自标定与归一化；部分帧的奇偶配比不再
     污染背景（adapter 的 ``chirp_mean`` 也不再把 11× 的两种幅度平均掉）。
  R2 **开场标定**：启动后先积累 ``calibration.empty_accumulate_s``（默认 10 s）
     的有效帧，只在这段时间内估计空座背景/噪声底，标定完成前不做占用判断
     （presence 恒 0，不误报）；标定完成时间 = max(10 s, 有效帧数下限)。
  R3 **F1 归一化 + 新的判空条件**：F1 残差改为「泄漏归一比值」——
     每个 bin 先除以 bin0（设备泄漏，与场景无关）再做背景残差，消除热状态
     /绝对基数（3538–10245 counts，2.9×）带来的尺度差异；判空改由
     **Bin1 瞬时变化（微动）证据**驱动：静坐者的 Bin1 帧间变化是走远后空座的
     1.4–8.7 倍（组员复算 5.65× 同向），以开场空座 10 s 的微动噪声底做归一，
     连续 ``release_frames`` 帧低于 ``release_ratio`` 即判空并重建空座快照。
     单用微动有误报/漏报，故判空同时要求 F1 归一残差不超阈值（``f1_gate``）。

严格因果：帧 t 只使用 ≤ t 的数据；标定窗口、噪声底、快照重建均不回看未来。
离线 ``run_m1`` 与在线 ``SeatDetector`` 共用 ``M1V121State.step``。

与 v1.2 的关系：
  * v1.2（``feature_set`` 缺省/``v1.2``）行为逐字节不变（本模块不参与）。
  * v1.2.1 由 config 的 ``feature_set: "v1.2.1"`` 显式启用，见
    ``config/M1_dev112p_v1.2.1.json``。
"""
import numpy as np

from .region import resolve_region

# 缺省参数见 config/M1_dev112p_v1.2.1.json（本模块的 _get 调用内联同一组默认值，
# 便于不提供 config 时也能运行）。


def _get(cfg, path, default):
    node = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def is_v121_config(cfg):
    """config 是否显式启用 v1.2.1（缺省 = v1.2，保持冻结行为）。"""
    if not isinstance(cfg, dict):
        return False
    fs = cfg.get("feature_set")
    if isinstance(fs, str):
        return fs.strip().lower() == "v1.2.1"
    return bool(_get(cfg, ["m1_v121", "enabled"], False))


def _detrend_std(a):
    """去线性趋势后的标准差（漂移不进入微动量）。"""
    n = len(a)
    if n < 6:
        return 0.0
    t = np.arange(n, dtype=np.float64)
    A = np.vstack([t, np.ones(n)]).T
    coef, *_ = np.linalg.lstsq(A, a, rcond=None)
    return float(np.std(a - A @ coef))


class M1V121State:
    """v1.2.1 逐帧状态机（离线/在线共用；严格因果）。

    输出轨迹字段与 v1.2 的 M1State 同名（detect.py / live_seat.py 直接复用）：
    presence/conf/quality、score/f1n/f2n/f3n/f4n/gate/f23_win；
    另加诊断字段 m（Bin1 微动比）、m4、f1_db、calibrating。
    """

    __slots__ = (
        # 参数
        "entry_ratio", "entry_confirm", "release_ratio", "release_frames",
        "win_slow", "win_fast", "motion_bin", "floor_alpha", "floor_min_ratio",
        "fast_rel_m_max", "fast_rel_inst_max", "fast_rel_f1_max",
        "calib_s", "calib_min_frames", "warmup_guard", "floor_pct",
        "f1_db_floor", "f1_db_span", "f1_gate", "f1_entry_support", "f1_ref_alpha",
        "f1_entry_enabled", "f1_change_max_db",
        "win_f2", "win_f3", "f2_norm", "band_lo", "band_hi", "low_floor",
        "t_on", "t_off", "confirm", "win_gate",
        # 环境
        "region", "lo_r", "hi_r", "nb", "complex_input", "dt_med", "degraded",
        "n_parity", "parity_enabled",
        # 配置回显
        "bg_mode", "warnings", "algorithm_version",
        # 输出轨迹
        "presence", "conf", "quality", "score", "f1n", "f2n", "f3n", "f4n",
        "gate", "f23_win", "m_trace", "m4_trace", "f1db_trace", "calibrating",
        "m_inst_trace", "calib_inst",
        # 历史
        "ts_hist", "amp_hist", "phase_hist", "main_hist", "q_amp_hist", "q_ts",
        "act_slow_hist", "act_fast_hist", "inst_hist", "inst_floor",
        # 标定
        "calib_amp_sum", "calib_cnt", "calib_done", "calib_act", "calib_f1", "bg_amp",
        "calib_end_ts", "floor_calib",
        "valid_frames", "floor", "f1_ref_db",
        # 判决
        "state", "cand", "cand_state", "empty_run", "snap_events",
    )

    def __init__(self, cfg, meta, region_params, nb, complex_input, dt_med,
                 n_parity=2):
        cfg = cfg or {}
        self.entry_ratio = float(_get(cfg, ["micro_motion", "entry_ratio"], 4.0))
        self.entry_confirm = max(1, int(_get(cfg, ["micro_motion", "entry_confirm_frames"], 8)))
        self.release_ratio = float(_get(cfg, ["micro_motion", "release_ratio"], 1.45))
        self.release_frames = max(1, int(_get(cfg, ["micro_motion", "release_frames"], 26)))
        self.win_slow = float(_get(cfg, ["micro_motion", "window_s"], 10.0))
        self.win_fast = float(_get(cfg, ["micro_motion", "fast_window_s"], 4.0))
        self.motion_bin = int(_get(cfg, ["micro_motion", "bin"], 1))
        self.floor_alpha = float(_get(cfg, ["micro_motion", "floor_alpha"], 0.02))
        self.floor_min_ratio = float(_get(cfg, ["micro_motion", "floor_min_ratio"], 0.8))
        # 判空快通道：慢窗被“慢漂移/慢台阶”抬起来、但没有真实微动时，
        # 允许按瞬时变化 + 背景未变判空（849 帧现场：走远后 73–82 s 的慢台阶
        # 把 m 抬到 5，但瞬时变化只有空座水平，导致释放被推迟约 13 s）。
        self.fast_rel_m_max = float(_get(cfg, ["micro_motion", "fast_release_m_max"], 6.0))
        self.fast_rel_inst_max = float(_get(cfg, ["micro_motion", "fast_release_inst_max"], 3.5))
        self.fast_rel_f1_max = float(_get(cfg, ["micro_motion", "fast_release_f1_max"], 0.2))
        self.calib_s = float(_get(cfg, ["calibration", "empty_accumulate_s"], 10.0))
        self.calib_min_frames = max(1, int(_get(cfg, ["calibration", "min_valid_frames"], 40)))
        self.warmup_guard = float(_get(cfg, ["calibration", "warmup_guard_s"], 6.0))
        self.floor_pct = float(_get(cfg, ["calibration", "floor_percentile"], 20.0))
        self.f1_db_floor = float(_get(cfg, ["f1", "db_floor"], 3.0))
        self.f1_db_span = float(_get(cfg, ["f1", "db_span"], 10.0))
        self.f1_gate = float(_get(cfg, ["f1", "gate"], 0.6))
        self.f1_entry_support = float(_get(cfg, ["f1", "entry_motion_support"], 1.8))
        self.f1_entry_enabled = bool(_get(cfg, ["f1", "entry_support_enabled"], False))
        self.f1_change_max_db = float(_get(cfg, ["f1", "stable_change_db"], 6.0))
        self.f1_ref_alpha = float(_get(cfg, ["f1", "ref_alpha"], 0.10))
        self.win_f2 = float(_get(cfg, ["windows", "f2_s"], 4.0))
        self.win_f3 = float(_get(cfg, ["windows", "f3_s"], 4.0))
        self.f2_norm = float(_get(cfg, ["f2_norm_rad"], 0.03))
        self.band_lo, self.band_hi = (float(v) for v in _get(cfg, ["f3_band_hz"], [0.1, 0.5]))
        low_db = float(_get(cfg, ["quality", "low_signal_db"], -60.0))
        self.low_floor = 10.0 ** (low_db / 10.0)
        # 接口兼容别名（live_seat 写 algorithm_online.json 用）
        self.t_on = self.entry_ratio
        self.t_off = self.release_ratio
        self.confirm = self.entry_confirm
        self.win_gate = self.win_slow
        self.algorithm_version = str(cfg.get("algorithm_version") or "M1 v1.2.1")

        region = resolve_region(meta, region_params, nb)
        self.region = region
        self.lo_r, self.hi_r = int(region["lo"]), int(region["hi"])
        self.nb = int(nb)
        self.complex_input = bool(complex_input)
        self.degraded = not self.complex_input
        self.dt_med = float(dt_med) if dt_med and float(dt_med) > 0 else 1.0
        self.n_parity = int(n_parity) if complex_input else 1
        self.parity_enabled = bool(_get(cfg, ["chirp_parity", "enabled"], True)) \
            and self.n_parity == 2
        self.warnings = list(region["warnings"])
        if self.degraded:
            self.warnings.append("real-valued input: v1.2.1 parity split and phase "
                                 "diagnostics unavailable; presence is motion-only")
        self.bg_mode = "calibrated_adaptive" if not self.degraded else "motion_only"

        # 输出轨迹
        self.presence, self.conf, self.quality = [], [], []
        self.score, self.f1n, self.f2n, self.f3n, self.f4n = [], [], [], [], []
        self.gate, self.f23_win = [], []
        self.m_trace, self.m4_trace, self.f1db_trace = [], [], []
        self.m_inst_trace = []
        self.calibrating = []
        # 历史
        self.ts_hist = []
        self.amp_hist = [[] for _ in range(self.n_parity)]
        self.phase_hist = [[] for _ in range(self.n_parity)]
        self.main_hist = [[] for _ in range(self.n_parity)]
        self.q_amp_hist = [[] for _ in range(self.n_parity)]
        self.act_slow_hist = [[] for _ in range(self.n_parity)]
        self.act_fast_hist = [[] for _ in range(self.n_parity)]
        self.inst_hist = []          # 每帧 Bin1 瞬时相对变化（parity 平均）
        # 标定
        self.calib_amp_sum = np.zeros((self.n_parity, self.nb), dtype=np.float64)
        self.calib_cnt = 0
        self.calib_done = False
        self.calib_end_ts = None
        self.calib_act = []
        self.calib_f1 = []
        self.calib_inst = []
        self.bg_amp = np.zeros((self.n_parity, self.nb), dtype=np.float64)
        self.valid_frames = 0
        self.floor = None
        self.floor_calib = None
        self.inst_floor = None
        self.f1_ref_db = None
        # 判决
        self.state = 0
        self.cand = 0
        self.cand_state = 0
        self.empty_run = 0
        self.snap_events = 0

    # ------------------------------------------------------------------ #
    def _calib_ready(self, ts_t):
        return (ts_t >= self.calib_s) and (self.valid_frames >= self.calib_min_frames)

    def _finish_calibration(self):
        k = max(1, self.valid_frames)
        # 幅度域背景：|X|/|X0| 的均值（复均值会因 10 s 内相位旋转而抵消，
        # 不能作背景；幅度比值对相位不敏感且消除泄漏基数）
        self.bg_amp = self.calib_amp_sum / k
        acts = [v for v in self.calib_act if v > 0]
        self.floor = max(float(np.percentile(acts, self.floor_pct)), 1e-6) if acts else 1e-3
        self.floor_calib = self.floor
        insts = [v for v in self.calib_inst if v > 0]
        self.inst_floor = max(float(np.percentile(insts, self.floor_pct)),
                              1e-6) if insts else self.floor
        # F1 参考：用刚建立的背景回算标定帧的泄漏归一残差（标定期 bg 尚未建立，
        # 逐帧累计的 calib_f1 全为 0，不能用作参考）
        qs = np.stack([np.asarray(self.q_amp_hist[p]) for p in range(self.n_parity)], axis=1)
        f1s = [self._f1_db(qs[i]) for i in range(len(self.ts_hist))]
        self.f1_ref_db = float(np.median(f1s)) if f1s else 0.0
        self.calib_done = True
        self.calib_end_ts = float(self.ts_hist[-1])
        self.warnings.append(
            f"v1.2.1 calibration: {self.valid_frames} valid frames over "
            f"{self.ts_hist[-1]:.1f}s; empty motion floor={self.floor:.5f} "
            f"(P{self.floor_pct:.0f}); F1 ref={self.f1_ref_db:.1f} dB")

    def _f1_db(self, q_amp):
        """泄漏归一幅度残差（dB，按区域非主 bin）。

        q_amp = |X|/|X[0]|（bin0 为设备泄漏，场景无关）；与标定空座幅度比
        比较，得到与热状态/绝对基数无关的相对残差。
        """
        sel = [b for b in range(self.lo_r, self.hi_r + 1) if b != self.region["main_bin"]]
        if not sel or not np.any(self.bg_amp):
            return 0.0
        r = np.asarray(q_amp)[:, sel] - self.bg_amp[:, sel]
        p_res = float(np.mean(np.sum(r ** 2, axis=1)))
        p_ref = float(np.mean(np.sum(self.bg_amp[:, sel] ** 2, axis=1)))
        return 10.0 * np.log10(p_res / (p_ref + 1e-18) + 1e-18)

    def _windows(self, ts_t):
        """当前帧两个微动窗的帧索引 [lo, t]（warmup 前样本不参与）。

        窗口尚未填满（标定早期）时回退到全部历史：否则 6–10 s 内只有 1–4 个
        样本，标定噪声底会被估到真实空座水平以下（T01_chair_shift 实测
        0.0004 vs 0.0013），标定后 m 虚高、开场误入场。
        """
        t = len(self.ts_hist) - 1
        guard = int(np.searchsorted(self.ts_hist, self.warmup_guard, side="left"))
        lo_s = int(np.searchsorted(self.ts_hist, ts_t - self.win_slow, side="left"))
        lo_f = int(np.searchsorted(self.ts_hist, ts_t - self.win_fast, side="left"))
        lo_s = max(lo_s, guard)
        lo_f = max(lo_f, guard)
        if t - lo_s + 1 < 6:
            lo_s = 0
        if t - lo_f + 1 < 6:
            lo_f = 0
        return lo_s, lo_f

    # ------------------------------------------------------------------ #
    def step(self, Xt, ts_t):
        """单帧推进（严格因果）。Xt: [nb] 或 [parity, nb] 复数剖面。"""
        t = len(self.ts_hist)
        X = np.asarray(Xt)
        if X.ndim == 1:
            X = X[None, :]
        if X.shape[0] != self.n_parity:
            if self.n_parity == 2 and X.shape[0] == 1:
                X = np.repeat(X, 2, axis=0)
            else:
                raise ValueError(f"frame parity axis {X.shape[0]} != session {self.n_parity}")
        X = X.astype(np.complex128)
        self.ts_hist.append(float(ts_t))

        # 输出占位
        self.presence.append(0)
        self.conf.append(None)
        self.quality.append("ok")
        self.score.append(0.0)
        self.f1n.append(0.0)
        self.f2n.append(0.0)
        self.f3n.append(0.0)
        self.f4n.append(0.0)
        self.gate.append(0.0)
        self.f23_win.append(0.0)
        self.m_trace.append(0.0)
        self.m4_trace.append(0.0)
        self.m_inst_trace.append(0.0)
        self.f1db_trace.append(0.0)
        self.calibrating.append(0)

        # ---- 质量判定（区域平均功率；与 v1.2 同一口径）----
        reg = X[:, self.lo_r:self.hi_r + 1]
        finite = np.all(np.isfinite(reg.real)) and np.all(np.isfinite(reg.imag))
        mean_power = float(np.mean(np.abs(reg) ** 2)) if finite else 0.0
        if not finite or mean_power < self.low_floor:
            self.quality[t] = "invalid_frame" if not finite else "low_signal"
            self.presence[t] = -1
            self.conf[t] = None
            return
        self.valid_frames += 1

        # ---- 逐帧特征历史 ----
        for p in range(self.n_parity):
            xp = X[p]
            amp = float(abs(xp[self.motion_bin]))
            self.amp_hist[p].append(amp)
            main = self.region["main_bin"]
            self.main_hist[p].append(xp[main])
            self.phase_hist[p].append(float(np.angle(xp[main])))
            a0 = abs(xp[0]) if abs(xp[0]) > 1e-12 else 1.0
            self.q_amp_hist[p].append(np.abs(xp) / a0)

        # Bin1 瞬时相对变化（parity 平均）：对“慢台阶/慢漂移”不敏感，
        # 用于判空快通道与诊断（组员原始要求里的“瞬间变化量”）。
        inst_now = 0.0
        for p in range(self.n_parity):
            h = self.amp_hist[p]
            if len(h) >= 2:
                inst_now += abs(h[-1] - h[-2]) / (h[-2] + 1e-9)
        inst_now /= float(self.n_parity)
        self.inst_hist.append(inst_now)

        # ---- 微动（去趋势幅度标准差 / 均值）----
        lo_s, lo_f = self._windows(float(ts_t))
        m_slow = m_fast = 0.0
        for p in range(self.n_parity):
            a_slow = a_fast = 0.0
            if t - lo_s + 1 >= 6:
                seg = np.asarray(self.amp_hist[p][lo_s:t + 1])
                a_slow = _detrend_std(seg) / (float(np.mean(seg)) + 1e-9)
            if t - lo_f + 1 >= 6:
                seg = np.asarray(self.amp_hist[p][lo_f:t + 1])
                a_fast = _detrend_std(seg) / (float(np.mean(seg)) + 1e-9)
            self.act_slow_hist[p].append(a_slow)
            self.act_fast_hist[p].append(a_fast)
            m_slow += a_slow
            m_fast += a_fast
        m_slow /= float(self.n_parity)
        m_fast /= float(self.n_parity)
        inst_win = (float(np.mean(np.asarray(self.inst_hist[lo_s:t + 1])))
                    if t - lo_s + 1 >= 6 else 0.0)

        # ---- F1（泄漏归一幅度残差，dB）----
        f1_db = self._f1_db(np.stack([self.q_amp_hist[p][-1] for p in range(self.n_parity)]))

        # ---- 相位/呼吸诊断（不参与判决；保持 scores.csv 列语义）----
        f2n = f3n = 0.0
        if not self.degraded:
            ph = np.asarray(self.phase_hist[0][lo_f:t + 1])
            if len(ph) >= 3:
                f2n = float(np.clip(np.std(np.unwrap(self.phase_hist[0][lo_f:t + 1]))
                                    / max(self.f2_norm, 1e-9), 0.0, 1.0))
            z = np.asarray(self.main_hist[0][lo_f:t + 1])
            if len(z) >= 8:
                Z = np.fft.fft(z) / len(z)
                freqs = np.fft.fftfreq(len(z), d=self.dt_med)
                band = (np.abs(freqs) >= self.band_lo) & (np.abs(freqs) < self.band_hi)
                den = float(np.sum(np.abs(Z) ** 2) - np.abs(Z[0]) ** 2)
                if np.any(band):
                    f3n = float(np.clip(np.sum(np.abs(Z[band]) ** 2)
                                        / (den + 1e-12) * 1.5, 0.0, 1.0))
        self.f2n[t], self.f3n[t], self.f23_win[t] = f2n, f3n, f2n + f3n
        self.f1db_trace[t] = float(f1_db)

        # ---- 标定阶段：只累积空座背景，不做占用判断 ----
        if not self.calib_done:
            for p in range(self.n_parity):
                self.calib_amp_sum[p] += self.q_amp_hist[p][-1]
            self.calib_cnt += 1
            if t >= 1:
                self.calib_act.append(m_slow)
            if t >= 2 and inst_win > 0:
                self.calib_inst.append(inst_win)
            self.calib_f1.append(float(f1_db))
            self.calibrating[t] = 1
            if self._calib_ready(float(ts_t)):
                self._finish_calibration()
            self.conf[t] = 0.5
            return

        # ---- 标定后的“整窗”等待：慢窗必须完全落在标定结束之后才允许判决 ----
        # 标定结束时慢窗里仍混着上电/摆位过渡段（T01 484 帧实测：标定结束 10.1 s，
        # 10.2–14.5 s 的 10 s 窗被 10–13 s 的幅度台阶/回摆污染，m 4.6–6.5 →
        # 11.61 s 误入场）。等一个完整慢窗（win_slow）过去再启用判决，
        # 过渡段只影响诊断量、不影响 presence。
        if self.calib_end_ts is not None and float(ts_t) < self.calib_end_ts + self.win_slow:
            self.m_trace[t] = m_slow / self.floor if self.floor else 0.0
            self.m4_trace[t] = m_fast / self.floor if self.floor else 0.0
            self.conf[t] = 0.5
            return

        # ---- 标定后 ----
        m = m_slow / self.floor if self.floor else 0.0
        m4 = m_fast / self.floor if self.floor else 0.0
        m_inst = inst_win / self.inst_floor if self.inst_floor else 0.0
        self.m_trace[t] = m
        self.m4_trace[t] = m4
        self.m_inst_trace[t] = m_inst

        # 空座期噪声底 / F1 空座基线缓跟踪：**只在无人态**更新。占用态冻结，
        # 否则静坐者的低微动会被当成新空座吸收（T01/P02 114–126 s 实测：
        # 自适应会把 m 从 1.5 拉到 0.7 并在 120 s 误判空座）。
        if self.state == 0 and m <= self.release_ratio:
            self.floor = (1.0 - self.floor_alpha) * self.floor \
                + self.floor_alpha * max(m_slow, 1e-6)
            if self.floor_calib:
                # 噪声底不得低于标定空座水平太多：底越低 m 越敏感，安静段
                # 漂低后任何小扰动都会被放大成“入场”（1,031 帧现场复盘项）。
                self.floor = max(self.floor, self.floor_calib * self.floor_min_ratio)
            if self.f1_ref_db is not None:
                self.f1_ref_db = (1.0 - self.f1_ref_alpha) * self.f1_ref_db \
                    + self.f1_ref_alpha * float(f1_db)

        f1n = float(np.clip((f1_db - self.f1_ref_db - self.f1_db_floor)
                            / max(self.f1_db_span, 1e-9), 0.0, 1.0))
        self.f1n[t] = f1n
        self.gate[t] = m

        # F1 稳定性：当前残差与 2–10 s 前的中位数之差（dB）。判空时要求背景
        # 没有正在发生的大幅变化（新空座证据必须“稳定”，而不是过渡态）。
        f1_change = 0.0
        if t >= 12:
            lo_f1 = int(np.searchsorted(self.ts_hist, float(ts_t) - 10.0, side="left"))
            hi_f1 = int(np.searchsorted(self.ts_hist, float(ts_t) - 2.0, side="left"))
            if hi_f1 > lo_f1:
                f1_change = abs(float(f1_db) - float(np.median(self.f1db_trace[lo_f1:hi_f1])))

        # 入场：微动显著；可选 F1 辅助分支（默认关闭：人走近时背景已变化，
        # 会把入场提前到“人还在走向座位”的 2–6 s，增加开场误报）
        occ = (m >= self.entry_ratio) or (
            self.f1_entry_enabled and m >= self.f1_entry_support and f1n >= self.f1_gate)
        quiet_motion = (m <= self.release_ratio) and (m4 <= self.release_ratio * 1.5)
        if not quiet_motion:
            # 判空快通道：慢窗被慢台阶/慢漂移抬起（m ≤ 6），但 Bin1 瞬时变化仍是
            # 空座水平、且背景 F1 没有变化 → 视为空座证据。849 帧现场 73–82 s 的
            # 慢台阶（m≈5、m_inst≈0.7、f1n=0）因此不再把释放推迟 13 s；
            # 静止等静坐者（P02 f1n=1.0、P04 f1n≈0.7）不满足 f1 条件，仍保持有人。
            quiet_motion = ((m <= self.fast_rel_m_max)
                            and (m_inst <= self.fast_rel_inst_max)
                            and (f1n <= self.fast_rel_f1_max))
        if self.state == 1:
            self.empty_run = self.empty_run + 1 if quiet_motion else 0
        else:
            self.empty_run = 0

        if self.state == 0:
            want = 1 if occ else 0
        else:
            # 判空计数由微动决定；释放时刻再用 F1 稳定性否决（背景正在大幅
            # 变化 = 新证据还不稳定，先不下结论），避免把过渡态当空座。
            released = (self.empty_run >= self.release_frames) \
                and (f1_change <= self.f1_change_max_db)
            want = 0 if released else 1

        if want != self.state:
            if want == self.cand_state:
                self.cand += 1
            else:
                self.cand_state, self.cand = want, 1
            need = self.entry_confirm if want == 1 else 1
            if self.cand >= need:
                self.state = want
                self.cand = 0
                if want == 0:
                    self._resnapshot(float(ts_t))
        else:
            self.cand = 0
        self.presence[t] = self.state

        # 兼容旧口径的 0–1 证据分数（A 端 conf 显示；非阈值依据）
        span = max(self.entry_ratio - self.release_ratio, 1e-6)
        self.score[t] = float(np.clip((m - self.release_ratio) / span, 0.0, 1.0))
        c = float(np.clip(0.5 + 0.49 * abs(2.0 * self.score[t] - 1.0), 0.5, 0.99))
        if self.calibrating[t]:
            c = min(c, 0.8)
        self.conf[t] = c

    def _resnapshot(self, ts_t):
        """判空后重建空座快照：背景与噪声底取自最近 10 s 的空座帧。"""
        lo = int(np.searchsorted(self.ts_hist, ts_t - self.win_slow, side="left"))
        for p in range(self.n_parity):
            qs = np.asarray(self.q_amp_hist[p][lo:])
            if len(qs):
                self.bg_amp[p] = np.mean(qs, axis=0)
        acts = []
        for p in range(self.n_parity):
            acts.extend(a for a in self.act_slow_hist[p][lo:] if a > 0)
        if acts:
            floor_new = max(float(np.percentile(acts, 50.0)), 1e-6)
            if self.floor_calib:
                floor_new = max(floor_new, self.floor_calib * self.floor_min_ratio)
            self.floor = floor_new
        if self.f1db_trace:
            self.f1_ref_db = float(self.f1db_trace[-1])
        self.snap_events += 1
        self.warnings.append(
            f"v1.2.1 release at t={ts_t:.1f}s: empty snapshot rebuilt "
            f"({self.snap_events}), floor={self.floor:.5f}")


def run_m1_v121(raw_data, timestamps, meta, cfg=None, region_params=None):
    """v1.2.1 主入口（离线；与在线共用 M1V121State.step）。"""
    X = np.asarray(raw_data)
    if X.ndim == 2:
        X = X[:, None, :]
    n, p, nb = X.shape
    ts = np.asarray(timestamps, dtype=np.float64)
    dt_med = float(np.median(np.diff(ts))) if n >= 2 else 1.0
    if not dt_med > 0:
        dt_med = 1.0

    st = M1V121State(cfg, meta, region_params, nb,
                     complex_input=np.iscomplexobj(X), dt_med=dt_med, n_parity=p)
    for t in range(n):
        st.step(X[t], float(ts[t]))
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
        "warnings": list(st.warnings),
        "bg_mode": st.bg_mode,
        "m": np.asarray(st.m_trace),
        "m4": np.asarray(st.m4_trace),
        "f1_db": np.asarray(st.f1db_trace),
        "calibrating": np.asarray(st.calibrating),
        "floor": st.floor,
        "f1_ref_db": st.f1_ref_db,
        "snap_events": st.snap_events,
    }

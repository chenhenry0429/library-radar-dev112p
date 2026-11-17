# -*- coding: utf-8 -*-
"""B0 候选③：点云重建（每帧 2D CA-CFAR + 局部极大 + MTI + 区域过滤 + 点数滞回）。

设计要点（见 docs/seat-detection-design-v0.1.md §5）：
  * 2D CA-CFAR 于 |2dfft|² 图，guard=(doppler 1, range 2)、train=(2, 4)、p_fa=1e-3；
    积分图实现，边界用可用单元数重归一；α = N_train·(p_fa^(−1/N_train)−1)。
  * 3×3 局部极大抑制，压多点粘连。
  * MTI 静杂波排除：丢弃 |wrapped(doppler − DC)| ≤ exclude_bins(1) 的检测点。
    静坐者能量几乎全在 doppler DC → 被滤除：这是官方基线"静坐弱检出"的复现，
    是 RQ2 的直接证据；B0 v1 因此是"区域内运动目标检测器"，静态占用由 M1 负责。
  * 区域过滤 + 点数滞回（t_on=2 帧 ≥1 点置位；连续 3 帧 0 点清零；翻转时清零对侧
    游程——否则残留计数会让单帧点立即重触发/立即释放）。
  * 诚实统计：p_fa=1e-3 → 60 s 录制约 7 个 FA 点帧，偶发 2-3 帧聚簇会触发短
    presence 闪现（≤ t_on+t_off 帧）；这是基线特性而非缺陷，静态占用由 M1 负责。

输出语义：
  presence=1 → target_count = 当帧区域内点数（c≥1）；保持期 c=0 → -1（无法可靠估计）。
  presence=0 → target_count=0（规范 C2）。quality≠ok → presence=-1、count=-1。
"""
import numpy as np

from .region import resolve_region

_DEFAULTS = {
    "cfar": {"guard_doppler": 1, "guard_range": 2,
             "train_doppler": 2, "train_range": 4, "p_fa": 0.001},
    "local_max_window": 3,
    "mti": {"exclude_bins": 1},
    "region_filter": True,
    "hysteresis": {"t_on_frames": 2, "t_off_frames": 3,
                   "count_on": 1, "count_off": 0},
    "quality": {"low_signal_db": -60.0},
}


def _cfg_get(cfg, path, default):
    node = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _int_image_sums(mag2, half_rows, half_cols):
    """对每个单元计算 (窗口和, 窗口单元数)，窗口为以单元为中心的
    [±half_rows]×[±half_cols] 方窗，边界截断（S 为零填充积分图）。"""
    nd, nb = mag2.shape
    S = np.zeros((nd + 1, nb + 1), dtype=np.float64)
    S[1:, 1:] = np.cumsum(np.cumsum(mag2, axis=0), axis=1)

    ii = np.arange(nd)
    jj = np.arange(nb)
    rs = np.clip(ii - half_rows, 0, nd)          # 含
    re = np.clip(ii + half_rows + 1, 0, nd)      # 不含
    cs = np.clip(jj - half_cols, 0, nb)
    ce = np.clip(jj + half_cols + 1, 0, nb)

    big = (S[re][:, ce] - S[rs][:, ce] - S[re][:, cs] + S[rs][:, cs])
    n_big = ((re - rs)[:, None] * (ce - cs)[None, :])
    return big, n_big


def cfar2d(mag2, gd, gr, td, tr, p_fa):
    """二维 CA-CFAR 检测掩码（向量化积分图实现）。

    参数：gd/gr=doppler/range 维 guard；td/tr=训练单元数（每侧）。
    返回：bool[nd, nb]。
    """
    big, n_big = _int_image_sums(mag2, gd + td, gr + tr)
    gsum, n_g = _int_image_sums(mag2, gd, gr)
    n_train = n_big - n_g
    n_train = np.maximum(n_train, 1)
    noise = np.maximum((big - gsum) / n_train, 1e-30)
    alpha = n_train * (p_fa ** (-1.0 / n_train) - 1.0)
    return mag2 > alpha * noise


def local_max_mask(mag2, det, k=3):
    """检测掩码内保留 k×k 邻域局部极大（并列峰全保留）。"""
    if not np.any(det):
        return det
    pad = k // 2
    big = np.full((mag2.shape[0] + 2 * pad, mag2.shape[1] + 2 * pad), -np.inf)
    big[pad:pad + mag2.shape[0], pad:pad + mag2.shape[1]] = mag2
    mx = np.full(mag2.shape, -np.inf)
    for di in range(k):
        for dj in range(k):
            mx = np.maximum(mx, big[di:di + mag2.shape[0], dj:dj + mag2.shape[1]])
    return det & (mag2 >= mx)


def _wrapped_dist(idx, dc, n):
    d = (idx - dc) % n
    return np.minimum(d, n - d)


def run_b0(raw_data, timestamps, meta, cfg=None, region_params=None):
    """B0 主入口。

    参数
    ----
    raw_data : array [N, doppler, range]
        已轴规整的 2dfft（channel 轴由 detect.py 非相干合并后再传入）。
    timestamps : float64 [N]
    meta : dict；meta.radar_config.doppler_dc_index 可覆盖 DC 位置
    cfg : dict 或 None（config/B0_default.json）

    返回
    ----
    dict: presence(int[N]), target_count(int[N]), conf(list[float|None]),
          quality(list[str]), points_per_frame(int[N] 区域内 MTI 后点数),
          region(dict), warnings(list[str])
    """
    cfg = cfg or {}
    X = np.asarray(raw_data)
    if X.ndim == 2:  # 单帧容错
        X = X[None, ...]
    n, nd, nb = X.shape
    ts = np.asarray(timestamps, dtype=np.float64)

    cfar = _cfg_get(cfg, ["cfar"], _DEFAULTS["cfar"])
    gd = int(cfar.get("guard_doppler", _DEFAULTS["cfar"]["guard_doppler"]))
    gr = int(cfar.get("guard_range", _DEFAULTS["cfar"]["guard_range"]))
    td = int(cfar.get("train_doppler", _DEFAULTS["cfar"]["train_doppler"]))
    tr = int(cfar.get("train_range", _DEFAULTS["cfar"]["train_range"]))
    p_fa = float(cfar.get("p_fa", _DEFAULTS["cfar"]["p_fa"]))
    k = int(_cfg_get(cfg, ["local_max_window"], _DEFAULTS["local_max_window"]))
    mti_bins = int(_cfg_get(cfg, ["mti", "exclude_bins"], _DEFAULTS["mti"]["exclude_bins"]))
    use_region = bool(_cfg_get(cfg, ["region_filter"], _DEFAULTS["region_filter"]))
    hys = _cfg_get(cfg, ["hysteresis"], _DEFAULTS["hysteresis"])
    t_on_frames = int(hys.get("t_on_frames", _DEFAULTS["hysteresis"]["t_on_frames"]))
    t_off_frames = int(hys.get("t_off_frames", _DEFAULTS["hysteresis"]["t_off_frames"]))
    count_on = int(hys.get("count_on", _DEFAULTS["hysteresis"]["count_on"]))
    count_off = int(hys.get("count_off", _DEFAULTS["hysteresis"]["count_off"]))
    low_db = float(_cfg_get(cfg, ["quality", "low_signal_db"], _DEFAULTS["quality"]["low_signal_db"]))
    low_floor = 10.0 ** (low_db / 10.0)

    region = resolve_region(meta, region_params, nb)
    lo_r, hi_r = region["lo"], region["hi"]
    warnings = list(region["warnings"])

    radar_cfg = (meta or {}).get("radar_config") or {}
    dc_index = radar_cfg.get("doppler_dc_index", cfg.get("doppler_dc_index", 0))
    dc_index = int(dc_index) % nd if nd else 0
    dop_dist = _wrapped_dist(np.arange(nd), dc_index, nd)
    mti_keep = dop_dist > mti_bins

    presence = np.zeros(n, dtype=np.int64)
    target_count = np.full(n, -1, dtype=np.int64)
    points_per_frame = np.zeros(n, dtype=np.int64)
    conf: list = [None] * n
    quality = ["ok"] * n

    state, on_run, off_run = 0, 0, 0

    for t in range(n):
        Xt = X[t]
        if not np.all(np.isfinite(Xt)):
            quality[t] = "invalid_frame"
            presence[t] = -1
            target_count[t] = -1
            conf[t] = None
            continue
        reg_slice = Xt[:, lo_r:hi_r + 1]
        if float(np.mean(np.abs(reg_slice.astype(np.complex128)) ** 2)) < low_floor:
            quality[t] = "low_signal"
            presence[t] = -1
            target_count[t] = -1
            conf[t] = None
            continue

        mag2 = np.abs(Xt.astype(np.complex128)) ** 2
        det = cfar2d(mag2, gd, gr, td, tr, p_fa)
        det = local_max_mask(mag2, det, k)
        det &= mti_keep[:, None]
        if use_region:
            det = det[:, lo_r:hi_r + 1]
        c = int(np.count_nonzero(det))
        points_per_frame[t] = c

        # 点数滞回（翻转时清零对侧游程：否则残留计数会让单帧点立即重触发/立即释放）
        if state == 0:
            on_run = on_run + 1 if c >= count_on else 0
            if on_run >= t_on_frames:
                state = 1
                on_run = off_run = 0
        else:
            off_run = off_run + 1 if c <= count_off else 0
            if off_run >= t_off_frames:
                state = 0
                off_run = on_run = 0

        presence[t] = state
        if state == 1:
            target_count[t] = c if c >= 1 else -1
            # 置信度：由点数给出的保守映射（2 点满置信；保持期 0 点降回 0.5）
            conf[t] = float(np.clip(0.5 + 0.45 * min(c, 3) / 3.0, 0.5, 0.95))
        else:
            target_count[t] = 0
            conf[t] = float(np.clip(0.5 + 0.45 * (0 if c else 1), 0.5, 0.95))

    return {
        "presence": presence,
        "target_count": target_count,
        "conf": conf,
        "quality": quality,
        "points_per_frame": points_per_frame,
        "region": region,
        "warnings": warnings,
    }

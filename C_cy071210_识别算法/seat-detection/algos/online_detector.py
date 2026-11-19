# -*- coding: utf-8 -*-
"""在线座位识别入口 v0.1 —— 连续可调用的单帧识别（B↔C 帧级链路的目标端）。

职责与边界（v0.1）：
  * 输入：B 侧「解码后一帧」（dev112p 单帧立方 + valid_chirps 掩码 + 名义时间）
    或已投影的规范单帧距离剖面（[nb] complex，1dfft 层级）。
  * 投影责任在 C（chip/rx 选路与 chirp 合并是算法决策，不外移给 B）：
    project_dev112p_frame = tools/dev112p_adapter.py chirp_mean 的单帧版
    （valid chirp 复均值；与离线转换逐字节一致，见 tools/live_seat.py 回归）。
  * 识别：与离线 run_m1 同一代码路径（make_m1_state + _m1_step），
    逐帧因果推进，不回改历史——离线/在线逐字节一致（12 组回归验证）。
  * 输出：九字段行 dict（contract v1 RESULT_COLUMNS），逐帧返回；
    A 侧按其 /api/frame 契约 POST（本模块不耦合 HTTP）。
  * 姿态：M2 v0.1 门控基线是回填语义（参考窗在未来帧），在线不可撤回历史帧
    → v0.1 在线姿态为接口占位（presence=0 → not_applicable；否则 unknown；
    cal=uncalibrated），与离线不带 --posture-config 的行为一致。全在线化推迟。

会话生命周期：
  * frame_id 从 0 起连续（A 契约：不连续即会话错乱，须重建 SeatDetector）。
  * timestamp 严格递增（秒，相对会话起点）。
  * 零有效 chirp 帧（无法投影）：保留 frame_id/timestamp 输出 invalid_frame 行
    （presence=-1, conf=null, target=-1），不推进识别状态机——离线适配器对这种
    帧的立场是整卷拒收；在线降级为帧级拒收。喂 NaN 剖面会毒化相位链
    （unwrap 对 NaN 前缀敏感），故此类帧不进 _m1_step。

dt_med（F3 频率映射）：
  离线 = median(diff(ts))；在线由会话帧周期显式传入（B 的 nominal_frame_period_s，
  与归档转换所用常数一致）。回放回归须传 float(np.median(np.diff(ts))) 以逐字节对齐。
"""
import numpy as np

from .m1_features import make_m1_state, _m1_step

RESULT_COLUMNS = ["frame_id", "timestamp", "presence", "presence_confidence",
                  "target_count", "posture", "posture_confidence",
                  "calibration_status", "quality"]


class DevFrameError(ValueError):
    """B 侧单帧数据不可投影（如零有效 chirp、形状不符）。"""


def project_dev112p_frame(raw_frame, valid_chirps, chip=1, rx=0):
    """dev112p 单帧立方 → 规范 1dfft 单帧距离剖面 [nb] complex64。

    与 tools/dev112p_adapter.py 的 chirp_mean 逐字节一致（valid chirp 复均值）：
      x = raw[chip, rx, :, :, 0] + 1j·raw[..., 1]        (float32 → complex64)
      prof = Σ_chirp x·v / Σ_chirp v                     (int64 分母 → complex128 → complex64)
    """
    rd = np.asarray(raw_frame)
    vc = np.asarray(valid_chirps)
    if rd.ndim != 5 or rd.shape[-1] != 2:
        raise DevFrameError(
            f"raw_frame must be (chip, rx, chirp, range_fft_bin, iq), got {rd.shape}")
    if vc.shape != rd.shape[:3]:
        raise DevFrameError(
            f"valid_chirps shape {vc.shape} != raw_frame prefix {rd.shape[:3]}")
    if not (0 <= chip < rd.shape[0] and 0 <= rx < rd.shape[1]):
        raise DevFrameError(f"chip={chip}/rx={rx} out of range {rd.shape[:2]}")
    x = rd[chip, rx, :, :, 0].astype(np.float32) \
        + 1j * rd[chip, rx, :, :, 1].astype(np.float32)      # (K, nb) complex64
    v = vc[chip, rx, :]                                       # (K,)
    nvalid = int(v.sum())
    if nvalid == 0:
        raise DevFrameError(f"no valid chirp on chip{chip}/rx{rx}")
    w = v.astype(np.float32)[:, None]                         # (K, 1)
    acc = (x * w).sum(axis=0)                                 # (nb,)
    prof = (acc / np.asarray(nvalid, dtype=np.int64)).astype(np.complex64)
    return prof


class SeatDetector:
    """连续识别会话（一卷录制/一次采集对应一个实例）。

    参数
    ----
    meta : dict
        规范 v1 meta（会话握手时由 B 的归档 meta 派生，或回放时用 <rid>_meta.json）。
        必需：data_level=="1dfft"、raw_shape（末位=bin 数）、raw_dtype；
        区域解析用 installation.* 与 detect.py 相同。
    m1_config : dict | None
        M1 配置（同 config/M1_dev112p_v0.2.json 内容；None 用内置默认）。
    region_params : dict | None
        与 detect.py --region-params 相同。
    frame_period_s : float | None
        F3 频率映射用的帧周期。缺省取 meta.radar_config.nominal_frame_period_s。
        回放回归须显式传 float(np.median(np.diff(ts)))。
    dev112p_chip / dev112p_rx : int
        feed_dev112p 投影选路（默认 1/0：呼吸带证据最强、无 int16 饱和，见适配器）。
    """

    def __init__(self, meta, m1_config=None, region_params=None,
                 frame_period_s=None, dev112p_chip=1, dev112p_rx=0):
        dl = meta.get("data_level")
        if dl != "1dfft":
            raise ValueError(
                f"online v0.1 supports 1dfft profiles only (M1), got data_level={dl!r}; "
                "2dfft/B0 and dsraw stay offline")
        shape = meta.get("raw_shape")
        if not (isinstance(shape, (list, tuple)) and len(shape) >= 2):
            raise ValueError(f"meta.raw_shape invalid for 1dfft: {shape!r}")
        nb = int(shape[-1])
        dtype_name = str(meta.get("raw_dtype") or "").lower()
        if not dtype_name:
            raise ValueError("meta.raw_dtype missing -- cannot decide complex/real mode")
        complex_input = dtype_name.startswith("complex")

        if frame_period_s is None:
            rc = meta.get("radar_config") or {}
            frame_period_s = rc.get("nominal_frame_period_s")
        if not frame_period_s or float(frame_period_s) <= 0:
            raise ValueError(
                "frame period unknown: pass frame_period_s explicitly or provide "
                "meta.radar_config.nominal_frame_period_s (F3 breath-band mapping "
                "depends on it)")

        self._st = make_m1_state(m1_config, meta, region_params, nb,
                                 complex_input=complex_input,
                                 dt_med=float(frame_period_s))
        self._dev_chip = int(dev112p_chip)
        self._dev_rx = int(dev112p_rx)
        self._next_frame_id = 0
        self._last_ts = None

    # ------------------------------------------------------------------ #

    def _check_frame_id(self, frame_id):
        if frame_id is None:
            frame_id = self._next_frame_id
        frame_id = int(frame_id)
        if frame_id != self._next_frame_id:
            raise ValueError(
                f"frame_id {frame_id} != expected {self._next_frame_id} "
                "(0-based consecutive per contract; a new recording/session "
                "must construct a new SeatDetector)")
        return frame_id

    def _check_ts(self, ts_t):
        ts_t = float(ts_t)
        if self._last_ts is not None and not ts_t > self._last_ts:
            raise ValueError(
                f"timestamp {ts_t!r} not strictly increasing "
                f"(last={self._last_ts!r})")
        return ts_t

    def _row(self, frame_id, ts_t, p, c, q):
        """九字段行（posture 为 v0.1 在线占位，= 离线不带 --posture-config）。"""
        return {
            "frame_id": int(frame_id),
            "timestamp": ts_t,
            "presence": int(p),
            "presence_confidence": None if c is None else float(c),
            "target_count": 1 if p == 1 else (0 if p == 0 else -1),
            "posture": "not_applicable" if p == 0 else "unknown",
            "posture_confidence": None,
            "calibration_status": "uncalibrated",
            "quality": q,
        }

    # ------------------------------------------------------------------ #

    def feed_frame(self, Xt, ts_t, frame_id=None):
        """喂入一帧已投影的规范距离剖面（[nb] complex/real）。

        返回九字段行 dict；不修改历史帧（严格因果）。
        """
        frame_id = self._check_frame_id(frame_id)
        ts_t = self._check_ts(ts_t)
        st = self._st
        _m1_step(st, Xt, ts_t)
        self._next_frame_id = frame_id + 1
        self._last_ts = ts_t
        t = len(st.ts_hist) - 1
        return self._row(frame_id, ts_t, st.presence[t], st.conf[t], st.quality[t])

    def feed_dev112p(self, raw_frame, valid_chirps, ts_t, frame_id=None):
        """喂入 B 侧「解码后一帧」：投影（C 侧责任）后进入识别。

        零有效 chirp / 形状不符 → DevFrameError 语义：输出 invalid_frame 行
        （保留 frame_id/timestamp，不填无人），识别状态机不推进。
        """
        frame_id = self._check_frame_id(frame_id)
        ts_t = self._check_ts(ts_t)
        try:
            prof = project_dev112p_frame(raw_frame, valid_chirps,
                                         chip=self._dev_chip, rx=self._dev_rx)
            if prof.shape[-1] != self._st.nb:
                raise DevFrameError(
                    f"projected profile has {prof.shape[-1]} bins, "
                    f"session expects {self._st.nb}")
        except DevFrameError as e:
            self._st.warnings.append(f"dev112p frame {frame_id} rejected: {e}")
            self._next_frame_id = frame_id + 1
            self._last_ts = ts_t
            return self._row(frame_id, ts_t, -1, None, "invalid_frame")
        return self.feed_frame(prof, ts_t, frame_id=frame_id)

    # ------------------------------------------------------------------ #

    @property
    def state(self):
        """M1State（诊断用：score/f1n/... 轨迹、滑窗、背景）。"""
        return self._st

    @property
    def frame_count(self):
        """已接收帧数（含拒收帧）。"""
        return self._next_frame_id

    @property
    def processed_count(self):
        """已进入识别状态机的帧数。"""
        return len(self._st.ts_hist)

    @property
    def warnings(self):
        return list(self._st.warnings)

    def last_scores(self):
        """最近一个已识别帧的辅助分数（离线 *_scores.csv 同款列）。"""
        st = self._st
        if not st.ts_hist:
            raise RuntimeError("no frame processed yet")
        t = len(st.ts_hist) - 1
        return {
            "score": float(st.score[t]),
            "f1n": float(st.f1n[t]),
            "f2n": float(st.f2n[t]),
            "f3n": float(st.f3n[t]),
            "gate": float(st.gate[t]),
        }

    def capabilities(self):
        """会话能力声明（contract / A 端 algorithm.json capabilities）。

        规范（A↔C 收口，2026-11-19）：capabilities 只放 presence/posture 两个
        纯 bool 能力键。A 端 snapshot 把 truthy 键名渲染为“已启用能力”，
        任何非 bool 扩展键都会污染展示 —— 扩展语义放 capabilities_semantics()
        / notes，禁止并入本 dict（contract 只校验两个 bool，不拒扩展键，
        但扩展键对 A 是展示缺陷）。
        """
        return {"presence": True, "posture": False}

    def capabilities_semantics(self):
        """能力语义说明（诊断/文档用；不得进 algorithm.json capabilities）。"""
        return (
            "online v0.1: M1 real-time presence only; posture column is the "
            "interface placeholder (not_applicable when empty, unknown when "
            "occupied, calibration_status=uncalibrated). Offline M2 v0.1 "
            "gated baseline is not online yet (backfill semantics); "
            "see CAPABILITIES_AND_KNOWN_ISSUES.md")

    def session_summary(self):
        """会话算法说明（live_seat.py 写 <rid>_session.json，诊断用）。"""
        st = self._st
        return {
            "algorithm_version": "M1 v1.2 + online v0.1 (placeholder posture)",
            "model_version": "M1-v1.2+online-v0.1",
            "capabilities": self.capabilities(),
            "capabilities_semantics": self.capabilities_semantics(),
            "region": {k: st.region[k] for k in ("lo", "hi", "main_bin", "source")},
            "degraded_input": st.degraded,
            "bg_mode": st.bg_mode,
            "notes": list(st.warnings),
        }

# -*- coding: utf-8 -*-
"""dev112p 采集归档 → 规范 v1 录制适配器（真实数据接入工具）。

输入：B 侧 dev112p_decoded_1dfft_archive v1 归档目录（raw.npz + meta.json）
输出：seat-detection 可直接消费的规范录制（两个层级）：
  <rid>_1dfft_raw.npz / <rid>_1dfft_meta.json   → M1（[frame, range_bin] complex）
  <rid>_2dfft_raw.npz / <rid>_2dfft_meta.json   → B0（[frame, doppler_bin, range_bin] complex）

降维策略（诚实记录在 meta 内，不冒充原始数据）：
  1. chip/rx 选路：默认 chip1/rx0（呼吸带证据最强、无 int16 饱和；
     chip0 bin0 存在 3.1% 负向饱和，见归档校验报告）。
  2. chirp 合并：
     - 1dfft：valid chirp 的复数均值（缺包 chirp 剔除后平均；无有效 chirp 的帧报错）。
       chirp 间隔 ~3 ms，呼吸/微动在 chirp 间近似不变，复均值近似无损且降噪；
       大动作的 chirp 间相位变化会部分相干抵消（类 MTI 效应），如实记录。
     - 2dfft：对 chirp 轴做 64 点 FFT，DC 位于 doppler bin 0（与合成生成器约定一致，
       不做 fftshift）。缺包 chirp 以复数 0 填充——仅帧 0（200/256）与帧 839（92/256）
       受影响，频谱存在 sinc 型伪影，在 capture_notes 中记录。
  3. timestamps = 归档 nominal_time_s（frame_ids × 0.1935 s，5.168 Hz）；frame_ids 原样。

用法：
    python tools/dev112p_adapter.py <archive_dir> [--chip 1] [--rx 0] [--out examples/real]
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # seat-detection/


def load_archive(archive_dir):
    npz_path = os.path.join(archive_dir, "raw.npz")
    meta_path = os.path.join(archive_dir, "meta.json")
    if not (os.path.isfile(npz_path) and os.path.isfile(meta_path)):
        raise SystemExit(f"archive must contain raw.npz and meta.json: {archive_dir}")
    with open(meta_path, "r", encoding="utf-8") as f:
        am = json.load(f)
    if am.get("format") != "dev112p_decoded_1dfft_archive":
        raise SystemExit(f"unexpected archive format: {am.get('format')!r}")
    z = np.load(npz_path, allow_pickle=False)
    return z, am


def select_chain(z, am, chip, rx):
    """抽取指定 chip/rx 的复数慢时间立方 [frame, chirp, bin] + 有效 chirp 掩码。"""
    if am["axis_order"] != ["frame", "chip", "rx", "chirp", "range_fft_bin", "iq_component"]:
        raise SystemExit(f"unexpected axis_order: {am['axis_order']}")
    rd = z["raw_data"]                      # (F, 2, 2, 64, 8, 2) int16
    vc = z["valid_chirps"]                  # (F, 2, 2, 64) bool
    if rd.shape != tuple(am["raw_shape"]):
        raise SystemExit(f"raw_shape mismatch: {rd.shape} vs {am['raw_shape']}")
    fids = z["frame_ids"].astype(np.int64)
    if not np.array_equal(fids, np.arange(len(fids))):
        raise SystemExit("frame_ids not 0..n-1 sequential; adapter cannot emit spec recording")
    x = rd[:, chip, rx, :, :, 0].astype(np.float32) \
        + 1j * rd[:, chip, rx, :, :, 1].astype(np.float32)   # (F, 64, 8)
    v = vc[:, chip, rx, :]                                  # (F, 64)
    nvalid = v.sum(axis=1)
    if (nvalid == 0).any():
        bad = int(np.flatnonzero(nvalid == 0)[0])
        raise SystemExit(f"frame {bad} has no valid chirp on chip{chip}/rx{rx}")
    return x, v, fids, z["nominal_time_s"].astype(np.float64)


def chirp_mean(x, v):
    """1dfft：valid chirp 复均值 → [F, 8] complex64（v1.2 旧口径）。"""
    f, n_ch, n_b = x.shape
    w = v.astype(np.float32)[:, :, None]                    # (F, 64, 1)
    acc = (x * w).sum(axis=1)                               # (F, 8)
    return (acc / v.sum(axis=1)[:, None]).astype(np.complex64)


def chirp_split(x, v):
    """1dfft：奇/偶 chirp 各自复均值 → [F, 2, 8] complex64（v1.2.1 口径）。

    tx_polling=1 时奇/偶 chirp 走不同发射通道，幅度相差 ~11×
    （T01 实测奇 ≈16200 / 偶 ≈1450 counts）。把两者平均会：
      * 让背景幅度取决于该帧的奇偶配比（frame 0 只有 27/26 → +139 counts 种子偏置）；
      * 把相差 11× 的两种传播路径混在一起。
    分开后每条链各自标定，部分帧的缺 chirp 不再改变另一条链的均值。
    返回的 parity 轴顺序：0 = 偶 chirp（chirp 序号 0,2,4…），1 = 奇 chirp。
    """
    f, n_ch, n_b = x.shape
    parity = np.arange(n_ch) % 2                            # 0=even index, 1=odd index
    out = np.zeros((f, 2, n_b), dtype=np.complex64)
    for p in (0, 1):
        mask = (parity == p)[None, :] & v
        w = mask.astype(np.float32)[:, :, None]
        cnt = mask.sum(axis=1)[:, None]
        acc = (x * w).sum(axis=1)
        out[:, p, :] = np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.0)
    return out


def chirp_fft(x, v):
    """2dfft：缺包填 0 后 chirp 轴 FFT（DC@bin0，无 fftshift）→ [F, 64, 8] complex64。"""
    xz = x * v[:, :, None]                                  # 缺包置复 0
    x2 = np.fft.fft(xz, axis=1)                             # (F, 64, 8)
    return x2.astype(np.complex64)


def build_meta(rid, am, chip, rx, data_level, axis_order, shape, post_note,
               region_bins, main_bin, iq_note=None):
    rc = dict(am.get("radar_config") or {})
    rc = {
        "nominal_frame_period_s": rc.get("nominal_frame_period_s"),
        "nominal_frame_rate_hz": rc.get("nominal_frame_rate_hz"),
        "range_bin_distance_m": rc.get("range_bin_distance_m"),   # null：未标定
        "tx_polling": rc.get("tx_polling"),
        "n_range_bins": shape[-1],
        "n_doppler_bins": shape[1] if data_level == "2dfft" else None,
        "doppler_dc_index": 0 if data_level == "2dfft" else None,
        "source_archive": {
            "format": am.get("format"), "archive_version": am.get("archive_version"),
            "recording_id": am.get("recording_id"),
            "device_model": am.get("device_model"),
            "raw_shape": am.get("raw_shape"), "raw_dtype": am.get("raw_dtype"),
        },
        "postprocess": post_note,
        "note": "derived from B-side dev112p archive; chip/rx selection and chirp "
                "merge are lossy projections, see postprocess",
    }
    return {
        "schema_version": "1.0",
        "recording_id": rid,
        "seat_id": am.get("seat_id"),
        "subject_id": am.get("subject_id"),
        "session_id": "R01",
        "source_kind": "measured",
        "capture_start": "2026-11-12T19:41:47+08:00",
        "timestamp_unit": "seconds",
        "timestamp_origin": "recording_start",
        "timestamp_source": ("derived: frame_id x nominal_frame_period (0.1935 s) from first "
                             "captured frame group (frame 0 may be partial); not wall-clock "
                             "aligned; raw .dat write window 19:41:47-19:44:29 per field log; "
                             "pending confirmation with B-side acquisition (Chen Zhixuan)"),
        "data_level": data_level,
        "raw_shape": list(shape),
        "raw_dtype": "complex64",
        "axis_order": axis_order,
        "value_unit": "device_fft_counts",
        "iq_representation": (
            iq_note or
            ("complex64 I/Q (device 1dfft output, valid-chirp merged mean); no DC removal applied"
             if data_level == "1dfft" else
             "complex64 I/Q (device 2dfft output, zero-filled missing chirps in partial frames 0/839); "
             "no DC removal applied")),
        "nominal_frame_rate_hz": am["radar_config"]["nominal_frame_rate_hz"],
        "radar_config": rc,
        "annotation": am.get("annotation"),
        "installation": {
            "position": ("field trial seat T01; no geometric calibration "
                         "(range bins uncalibrated); radar-to-chest ~60 cm per "
                         "field log note, unverified"),
            "height_m": None,
            "angle_deg": None,
            "region_m": None,
            "region_bins": region_bins,
            "main_bin": main_bin,
            "plan": None
        },
        "capture_notes": (
            "operator deviated from scripted plan; plan times are verbal commands, "
            "NOT ground truth (actual_action_timestamps_available=false). "
            "chip0 bin0 has 3.1% int16 negative saturation; chip1/rx0 selected "
            "(cleanest breathing evidence, no saturation). Frames 0 (200/256 chirps) "
            "and 839 (92/256) are partial; 1dfft uses valid-chirp mean, 2dfft "
            "zero-fills missing chirps (sinc artifacts in those two frames). "
            "Range bins not calibrated to meters; region_bins/main_bin were "
            "signal-calibrated during pilot bring-up (breathing evidence, not "
            "geometry) -- treat as per-seat calibration pending geometric survey."
        ),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("archive_dir")
    ap.add_argument("--chip", type=int, default=1)
    ap.add_argument("--rx", type=int, default=0)
    ap.add_argument("--region-bins", default="1,3",
                    help="installation.region_bins for meta, e.g. '1,3' (pilot "
                         "signal-calibrated chest neighborhood)")
    ap.add_argument("--main-bin", type=int, default=2,
                    help="installation.main_bin for meta (pilot signal-calibrated)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "examples", "real"))
    ap.add_argument("--parity", choices=["mean", "split"], default="mean",
                    help="1dfft chirp merge: 'mean' = v1.2 single profile (default, "
                         "backward compatible); 'split' = v1.2.1 odd/even separate "
                         "profiles [frame, parity, range]")
    ap.add_argument("--skip-2dfft", action="store_true",
                    help="do not emit the (large) 2dfft recording (v1.2.1 1dfft only)")
    a = ap.parse_args()

    region_bins = [int(v) for v in a.region_bins.split(",")]
    if not (len(region_bins) == 2 and region_bins[0] <= a.main_bin <= region_bins[1]):
        raise SystemExit(f"region {region_bins} must bracket main_bin {a.main_bin}")

    z, am = load_archive(a.archive_dir)
    x, v, fids, ts = select_chain(z, am, a.chip, a.rx)

    os.makedirs(a.out, exist_ok=True)
    base_rid = f"{am['recording_id']}_c{a.chip}r{a.rx}"

    # 1dfft（M1 输入）
    if a.parity == "split":
        x1 = chirp_split(x, v)
        rid1 = f"{base_rid}_1dfft_parity"
        np.savez(os.path.join(a.out, f"{rid1}_raw.npz"),
                 timestamps=ts, frame_ids=fids, raw_data=x1)
        m1 = build_meta(rid1, am, a.chip, a.rx, "1dfft",
                        ["frame", "chirp_parity", "range_bin"], x1.shape,
                        {"chip_rx_selected": [a.chip, a.rx],
                         "chirp_merge": "odd/even chirp split: complex mean per parity "
                                        "(parity 0 = chirp index 0,2,4..., parity 1 = 1,3,5...)"},
                        region_bins, a.main_bin,
                        iq_note=("complex64 I/Q (device 1dfft output, odd/even chirp "
                                 "means kept separate for v1.2.1); no DC removal applied"))
    else:
        x1 = chirp_mean(x, v)
        rid1 = f"{base_rid}_1dfft"
        np.savez(os.path.join(a.out, f"{rid1}_raw.npz"),
                 timestamps=ts, frame_ids=fids, raw_data=x1)
        m1 = build_meta(rid1, am, a.chip, a.rx, "1dfft",
                        ["frame", "range_bin"], x1.shape,
                        {"chip_rx_selected": [a.chip, a.rx],
                         "chirp_merge": "complex mean over valid chirps"},
                        region_bins, a.main_bin)
    with open(os.path.join(a.out, f"{rid1}_meta.json"), "w", encoding="utf-8") as f:
        json.dump(m1, f, ensure_ascii=False, indent=2)

    # 2dfft（B0 输入）
    x2 = None
    if not a.skip_2dfft:
        x2 = chirp_fft(x, v)
        rid2 = f"{base_rid}_2dfft"
        np.savez(os.path.join(a.out, f"{rid2}_raw.npz"),
                 timestamps=ts, frame_ids=fids, raw_data=x2)
        m2 = build_meta(rid2, am, a.chip, a.rx, "2dfft",
                        ["frame", "doppler_bin", "range_bin"], x2.shape,
                        {"chip_rx_selected": [a.chip, a.rx],
                         "chirp_merge": "64-point FFT over chirp axis, DC at doppler "
                                        "bin 0 (no fftshift); missing chirps zero-filled"},
                        region_bins, a.main_bin)
        with open(os.path.join(a.out, f"{rid2}_meta.json"), "w", encoding="utf-8") as f:
            json.dump(m2, f, ensure_ascii=False, indent=2)

    # 输出自检摘要
    pairs = [(rid1, x1)] + ([] if x2 is None else [(rid2, x2)])
    for rid, X in pairs:
        finite = np.isfinite(X.view(np.float32)).all()
        print(f"{rid}: shape={X.shape} dtype={X.dtype} "
              f"abs_max={np.abs(X).max():.1f} finite={finite}")
    n_partial = int(((v.sum(axis=1) < 64)).sum())
    print(f"partial frames (chirp loss) on chain: {n_partial}")
    print(f"written to {a.out}")


if __name__ == "__main__":
    main()

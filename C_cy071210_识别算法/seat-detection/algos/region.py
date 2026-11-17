# -*- coding: utf-8 -*-
"""区域解析：三级降级。

优先级（高 → 低）：
  1. meta.installation.region_bins = [lo, hi]（可附 main_bin） —— meta_explicit
  2. meta.installation.plan ∈ {A,B} + region_params 参数表几何换算 —— geometry
  3. 全距离降级（main_bin=None，逐帧动态 argmax，低可靠） —— full_range_degraded

换算公式（与 config/region_params_v0.1.json 的 bin_conversion 一致）：
  bin_lo = floor(region_m[0] / range_resolution_m)
  bin_hi = ceil (region_m[1] / range_resolution_m)
  结果 clip 到 [0, n_range_bins-1]
"""
import json
import math


def resolve_region(meta, region_params=None, n_range_bins=64):
    """解析座位关注区域。

    参数
    ----
    meta : dict
        <rid>_meta.json 内容（至少可能含 installation）。
    region_params : dict 或 None
        config/region_params_v0.1.json 内容；几何换算需要。
    n_range_bins : int
        raw_data 的 range 维长度，用于 clip 与降级。

    返回
    ----
    dict: lo, hi, main_bin(int 或 None), source, warnings(list[str])
    """
    warnings = []
    inst = {}
    if isinstance(meta, dict):
        inst = meta.get("installation") or {}

    # ---- 1) meta 显式 region_bins ----
    rb = inst.get("region_bins")
    if isinstance(rb, (list, tuple)) and len(rb) == 2:
        try:
            lo, hi = int(rb[0]), int(rb[1])
        except (TypeError, ValueError):
            warnings.append(f"region_bins {rb!r} not numeric, falling back")
        else:
            if 0 <= lo <= hi <= n_range_bins - 1:
                main = inst.get("main_bin")
                if main is None:
                    main = int(round((lo + hi) / 2.0))
                    warnings.append(
                        f"main_bin not given, using region center bin {main}")
                else:
                    main = int(main)
                    if not (lo <= main <= hi):
                        clamped = min(max(main, lo), hi)
                        warnings.append(
                            f"main_bin {main} outside region [{lo},{hi}], clamped to {clamped}")
                        main = clamped
                return {"lo": lo, "hi": hi, "main_bin": main,
                        "source": "meta_explicit", "warnings": warnings}
            warnings.append(
                f"region_bins {rb!r} out of range for n_range_bins={n_range_bins}, falling back")

    # ---- 2) 参数表几何换算 ----
    plan = inst.get("plan")
    if plan and isinstance(region_params, dict):
        plans = region_params.get("plans") or {}
        if plan in plans:
            res = float(region_params.get("range_resolution_m", 0.6) or 0.6)
            region_m = plans[plan].get("region_m")
            if isinstance(region_m, (list, tuple)) and len(region_m) == 2:
                lo = max(0, int(math.floor(float(region_m[0]) / res)))
                hi = min(n_range_bins - 1, int(math.ceil(float(region_m[1]) / res)))
                if lo <= hi:
                    main = inst.get("main_bin")
                    if main is None:
                        main = int(round(((float(region_m[0]) + float(region_m[1])) / 2.0) / res))
                    main = min(max(int(main), lo), hi)
                    warnings.append(
                        f"region from geometry plan {plan!r} @ {res} m resolution")
                    return {"lo": lo, "hi": hi, "main_bin": main,
                            "source": "geometry", "warnings": warnings}
                warnings.append("geometry conversion degenerate (lo>hi), falling back")
        else:
            warnings.append(f"plan {plan!r} not found in region_params, falling back")

    # ---- 3) 全距离降级 ----
    warnings.append(
        "no usable region info in meta (no region_bins / plan); "
        "full-range degraded mode -- low reliability, main_bin is per-frame argmax")
    return {"lo": 0, "hi": n_range_bins - 1, "main_bin": None,
            "source": "full_range_degraded", "warnings": warnings}


def load_region_params(path):
    """读取 region_params JSON；不存在时返回 None（允许无参数表运行）。"""
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="resolve seat region from meta.json")
    ap.add_argument("meta", help="path to <rid>_meta.json")
    ap.add_argument("--region-params", default=None,
                    help="path to config/region_params_v0.1.json")
    ap.add_argument("--n-range-bins", type=int, default=64)
    a = ap.parse_args()
    with open(a.meta, encoding="utf-8") as f:
        m = json.load(f)
    r = resolve_region(m, load_region_params(a.region_params), a.n_range_bins)
    print(f"region bins [{r['lo']},{r['hi']}]  main_bin={r['main_bin']}  "
          f"source={r['source']}")
    for w in r["warnings"]:
        print(f"  note: {w}")

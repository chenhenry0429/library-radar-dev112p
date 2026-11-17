# -*- coding: utf-8 -*-
"""seat-detection 算法包（M1 静态占用 / B0 点云重建）。

模块按需显式导入，包初始化保持轻量：
    from algos.region import resolve_region
    from algos.selfcheck import check_result_csv
    from algos.m1_features import run_m1, presence_from_scores
    from algos.b0_pointcloud import run_b0
"""

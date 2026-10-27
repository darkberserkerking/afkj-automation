"""掩码归一化互相关（masked TM_CCOEFF_NORMED）——支持 RGBA 透明模板。

cv2.matchTemplate 的 TM_CCOEFF_NORMED 不接受 mask；TM_CCORR_NORMED 虽支持
mask 但对亮度敏感易误报。本实现用滑动窗口积分（cv2.filter2D，等价 correlation），
把模板 alpha 作为权重，只统计圆内像素，是"透明背景模板"的正确匹配算法。

公式（对每个候选窗口左上角 (x,y)，跨 3 通道求和）：
    Tss = Σ M·T² - (Σ M·T)² / n          （模板侧常数）
    num = Σ M·T·I - (Σ M·T)·(Σ M·I) / n
    Iss = Σ M·I² - (Σ M·I)² / n          （每窗口，随位置变化）
    score = num / sqrt(Tss · Iss)
"""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np


def masked_ccoeff_map(
    src_bgr: np.ndarray,
    tpl_bgr: np.ndarray,
    mask01: np.ndarray,
) -> Optional[np.ndarray]:
    """返回 (H-th+1, W-tw+1) 的分数图，或 None（模板无效）。

    src_bgr: 源图 BGR (H, W, 3) uint8
    tpl_bgr: 模板 BGR (th, tw, 3) uint8（透明区 RGB 可任意，由 mask 排除）
    mask01:  模板权重 (th, tw) float32，取值 0.0~1.0（如 alpha/255）
    """
    H, W = src_bgr.shape[:2]
    th, tw = tpl_bgr.shape[:2]
    if th > H or tw > W:
        return None
    M = mask01.astype(np.float32)
    n = float(M.sum())
    if n < 1.0:
        return None

    # 模板侧常数（跨通道累计）
    st_by_c = []
    k_mt_by_c = []       # 每通道卷积核 = M·T
    tss_total = 0.0
    for c in range(3):
        T = tpl_bgr[:, :, c].astype(np.float32)
        MT = M * T
        st_c = float(MT.sum())
        tss_c = float((MT * T).sum()) - st_c * st_c / n
        st_by_c.append(st_c)
        k_mt_by_c.append(MT)
        tss_total += tss_c
    if tss_total <= 1e-9:
        return None

    valid_h = H - th + 1
    valid_w = W - tw + 1
    # cv2.filter2D 输出与 src 同尺寸；无 padding 污染的"有效区"位于
    # [th//2 : th//2+valid_h, tw//2 : tw//2+valid_w]
    ay0, ax0 = th // 2, tw // 2
    num = np.zeros((valid_h, valid_w), dtype=np.float64)
    iss_map = np.zeros((valid_h, valid_w), dtype=np.float64)
    kM = M  # Σ M·I 的核（即 M 本身）
    for c in range(3):
        I = src_bgr[:, :, c].astype(np.float32)
        I2 = I * I
        r_mti = cv2.filter2D(I, cv2.CV_64F, k_mt_by_c[c])
        r_mi = cv2.filter2D(I, cv2.CV_64F, kM)
        r_mi2 = cv2.filter2D(I2, cv2.CV_64F, kM)
        # 取有效区
        mti = r_mti[ay0:ay0 + valid_h, ax0:ax0 + valid_w]
        mi = r_mi[ay0:ay0 + valid_h, ax0:ax0 + valid_w]
        mi2 = r_mi2[ay0:ay0 + valid_h, ax0:ax0 + valid_w]
        num += mti - st_by_c[c] * mi / n
        iss_map += mi2 - mi * mi / n

    # 数值误差兜底：Iss 理论恒 >= 0，clip 防舍入负值
    iss_map = np.maximum(iss_map, 0.0)
    den = np.sqrt(tss_total * iss_map)
    score = np.zeros_like(num)
    # 分母相对模板能量可忽略（mask 内窗口纯色/无纹理，Iss≈浮点残差）
    # → 匹配无统计意义，得分归 0，防止残差被放大成虚假高分
    floor = 1e-7 * np.sqrt(tss_total)
    np.divide(num, den, out=score, where=den > floor)
    np.clip(score, -1.0, 1.0, out=score)
    return score


def match_with_alpha(
    src_bgr: np.ndarray,
    tpl_rgba: np.ndarray,
    scale_range: Tuple[float, float, float] = (0.5, 2.0, 0.1),
    threshold: float = 0.75,
) -> Optional[Tuple[int, int, float, float]]:
    """多尺度 masked NCC，返回 (cx, cy, score, scale) 或 None。

    tpl_rgba: RGBA 模板 (th, tw, 4)，alpha 即权重。
    """
    if tpl_rgba.shape[2] != 4:
        raise ValueError('模板需为 RGBA 4 通道')
    H, W = src_bgr.shape[:2]
    tpl_bgr = tpl_rgba[:, :, :3]
    alpha = tpl_rgba[:, :, 3]
    mask01 = (alpha > 0).astype(np.float32)

    min_s, max_s, step = scale_range
    best: Tuple[int, int, float, float] | None = None  # (cx, cy, score, scale)
    n_scales = int((max_s - min_s) / step) + 1
    for i in range(n_scales):
        s = min_s + i * step
        tpl_s = cv2.resize(tpl_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        mask_s = cv2.resize(mask01, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        mask_s = (mask_s > 0.5).astype(np.float32)
        th_s, tw_s = tpl_s.shape[:2]
        if th_s > H or tw_s > W:
            continue
        score_map = masked_ccoeff_map(src_bgr, tpl_s, mask_s)
        if score_map is None:
            continue
        _, mv, _, ml = cv2.minMaxLoc(score_map)
        if best is None or mv > best[2]:
            cx = ml[0] + tw_s // 2
            cy = ml[1] + th_s // 2
            best = (cx, cy, float(mv), s)
    if best is None or best[2] < threshold:
        return None
    return best

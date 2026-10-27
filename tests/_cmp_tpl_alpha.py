"""透明背景模板 vs 旧模板：在弹窗1.png 上的匹配对照实验。

方法 A: IMREAD_COLOR 读取（丢 alpha → 纯黑角，当前 runtime 行为）
方法 B: OpenCV TM_CCORR_NORMED + mask 参数（原生支持 mask，但亮度敏感）
方法 C: masked NCC（自研，tests/_masked_ncc.py，透明语义最正）
基准:   旧模板 btn_close.png / btn_confirm.png（RGBA 真实底色）
"""
import cv2
import numpy as np
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _masked_ncc import masked_ccoeff_map, match_with_alpha  # noqa: E402

ROOT = Path(r'C:\Users\x9502\WorkBuddy\20260320211537')
POPUP = Path(r'C:\Users\x9502\Pictures\Screenshots\弹窗1.png')
SCALES = np.arange(0.5, 2.0 + 1e-6, 0.1)
TARGETS = {
    '白X(688,679)': (688, 679),
    '绿√(811,679)': (811, 679),
}


def load(path: Path, flag: int):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), flag)


def target_best(score_map, th, tw, H, W, tgt, radius=40):
    """score_map 为 (H-th+1, W-tw+1)，找 tgt 附近最优分数。"""
    tx, ty = tgt
    cx0 = tx - tw // 2
    cy0 = ty - th // 2
    lo_x = max(0, cx0 - radius)
    hi_x = min(W - tw, cx0 + radius)
    lo_y = max(0, cy0 - radius)
    hi_y = min(H - th, cy0 + radius)
    if lo_x > hi_x or lo_y > hi_y:
        return -1.0
    sub = score_map[lo_y:hi_y + 1, lo_x:hi_x + 1]
    return float(sub.max())


def multi_scale_best(run_one):
    """run_one(scale) -> (score_map, th, tw) 或 None；返回全局最优。"""
    best = None  # (score, x, y, th, tw, scale)
    H, W = POPUP_H, POPUP_W
    for s in SCALES:
        out = run_one(s)
        if out is None:
            continue
        score_map, th, tw = out
        _, mv, _, ml = cv2.minMaxLoc(score_map)
        if best is None or mv > best[0]:
            best = (float(mv), int(ml[0]), int(ml[1]), th, tw, float(s))
    return best


def fmt_best(b):
    if b is None:
        return '未命中'
    score, x, y, th, tw, s = b
    return f'{score:.3f} @({x + tw // 2},{y + th // 2}) s={s:.1f} tpl={tw}x{th}'


def main():
    global POPUP_H, POPUP_W
    src = load(POPUP, cv2.IMREAD_COLOR)
    POPUP_H, POPUP_W = src.shape[:2]

    new_tpls = {
        'btn_confirm_cancel.png': (688, 679),
        'btn_confirm_check.png': (811, 679),
    }
    old_tpls = {
        'btn_close.png': (688, 679),
        'btn_confirm.png': (811, 679),
    }

    def analyze(name, tpl_bgr, tpl_rgba, mask01, want):
        rows = {}

        # A: COLOR（模拟当前 runtime：RGB + 黑角）
        tpl_a = tpl_bgr.copy()
        if mask01 is not None:
            tpl_a[mask01 < 0.5] = 0  # 复现"黑角"裁剪版

        def run_a(s):
            t = cv2.resize(tpl_a, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            th, tw = t.shape[:2]
            if th > POPUP_H or tw > POPUP_W:
                return None
            r = cv2.matchTemplate(src, t, cv2.TM_CCOEFF_NORMED)
            return r, th, tw
        rows['A 丢alpha黑角(CCOEFF)'] = fmt_best(multi_scale_best(run_a))

        # B: OpenCV TM_CCORR_NORMED + mask
        if tpl_rgba is not None and mask01 is not None:
            def run_b(s):
                t = cv2.resize(tpl_rgba[:, :, :3], None, fx=s, fy=s,
                               interpolation=cv2.INTER_AREA)
                m = cv2.resize(mask01, None, fx=s, fy=s,
                               interpolation=cv2.INTER_NEAREST)
                m = (m > 0.5).astype(np.uint8) * 255
                th, tw = t.shape[:2]
                if th > POPUP_H or tw > POPUP_W:
                    return None
                r = cv2.matchTemplate(src, t, cv2.TM_CCORR_NORMED, mask=m)
                return r, th, tw
            rows['B CCORR_NORMED+mask'] = fmt_best(multi_scale_best(run_b))

        # C: masked NCC
        if tpl_rgba is not None and mask01 is not None:
            def run_c(s):
                t = cv2.resize(tpl_rgba[:, :, :3], None, fx=s, fy=s,
                               interpolation=cv2.INTER_AREA)
                m = cv2.resize(mask01, None, fx=s, fy=s,
                               interpolation=cv2.INTER_NEAREST)
                m = (m > 0.5).astype(np.float32)
                th, tw = t.shape[:2]
                if th > POPUP_H or tw > POPUP_W:
                    return None
                sm = masked_ccoeff_map(src, t, m)
                if sm is None:
                    return None
                return sm, th, tw
            rows['C maskedNCC(透明)'] = fmt_best(multi_scale_best(run_c))

        return rows

    print(f'弹窗图: 1278x1022\n')

    # ── 新模板（透明版）──
    for name, want in new_tpls.items():
        rgba = load(ROOT / 'templates' / name, cv2.IMREAD_UNCHANGED)
        bgr = rgba[:, :, :3]
        mask01 = (rgba[:, :, 3] > 0).astype(np.float32)
        rows = analyze(name, bgr, rgba, mask01, want)
        print(f'▶ {name}  期望命中: {want}')
        for k, v in rows.items():
            print(f'    {k:<28} {v}')
        print()

    # ── 旧模板（真实底色矩形基准）──
    for name, want in old_tpls.items():
        rgba = load(ROOT / 'templates' / name, cv2.IMREAD_UNCHANGED)
        bgr = rgba[:, :, :3]
        rows = analyze(name, bgr, None, None, want)
        print(f'▶ {name}（旧基准） 期望命中: {want}')
        for k, v in rows.items():
            print(f'    {k:<28} {v}')
        print()


if __name__ == '__main__':
    main()

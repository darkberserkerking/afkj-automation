"""模板对比测试：4 个 X/✓ 模板在弹窗1.png 上的多尺度匹配。

匹配口径与 afk_bot/infra/input.py::template_match 完全一致（TM_CCOEFF_NORMED，0.5-2.0 步长 0.1）。
除"全局最优"外，还分别评估"白 X 按钮目标区(688,679)"和"绿 √ 按钮目标区(811,679)"的最高得分，
用以判断旧模板是否仍能正确命中。
"""
import cv2
import numpy as np
from pathlib import Path

ROOT = Path(r'C:\Users\x9502\WorkBuddy\20260320211537')
POPUP = Path(r'C:\Users\x9502\Pictures\Screenshots\弹窗1.png')
TPLS = [
    'btn_close.png',          # 旧：白色 X
    'btn_confirm.png',        # 旧：绿色 √
    'btn_confirm_cancel.png', # 新：白 X（圆形 mask）
    'btn_confirm_check.png',  # 新：绿 √（圆形 mask）
]
TARGETS = {
    'white_X_688_679': (688, 679),     # 弹窗中的白色 ✕ 按钮中心
    'green_C_811_679': (811, 679),     # 弹窗中的绿色 √ 按钮中心
}
SCALES = np.arange(0.5, 2.0 + 1e-6, 0.1)


def load(path: Path) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f'读取失败: {path}')
    return img


def match_at_target(result_map, ts_h, ts_w, img_h, img_w, target_xy, radius=40):
    """在 target_xy 周围 radius 范围内找最大匹配值（返回 val + 全图 loc）。"""
    tx, ty = target_xy
    # result_map[iy, ix] = template 在 (ix, iy) 处的得分
    # 模板尺寸 ts_h x ts_w，因此模板左上角位于 (tx - ts_w//2, ty - ts_h//2)
    cx = tx - ts_w // 2
    cy = ty - ts_h // 2
    lo_x = max(0, cx - radius)
    hi_x = min(img_w - ts_w, cx + radius)
    lo_y = max(0, cy - radius)
    hi_y = min(img_h - ts_h, cy + radius)
    if lo_x > hi_x or lo_y > hi_y:
        return -1.0, None
    sub = result_map[lo_y:hi_y + 1, lo_x:hi_x + 1]
    _, mv, _, ml = cv2.minMaxLoc(sub)
    return mv, (lo_x + ml[0], lo_y + ml[1])


def main():
    img = load(POPUP)
    H, W = img.shape[:2]
    print(f'弹窗图: {POPUP.name}  尺寸: {W}x{H}')

    overlay = img.copy()
    # BGR 调色板
    palette = [
        (0, 0, 255),    # 红 - btn_close
        (255, 128, 0),  # 蓝 - btn_confirm
        (255, 0, 255),  # 紫 - btn_confirm_cancel
        (0, 255, 255),  # 黄 - btn_confirm_check
    ]

    rows = []
    for idx, name in enumerate(TPLS):
        tpl_path = ROOT / 'templates' / name
        tpl = load(tpl_path)
        th0, tw0 = tpl.shape[:2]
        best_val, best_loc, best_scale = -1.0, None, 1.0
        per_scale_top = []
        # 缓存最后一轮的 result 给 target_score 用
        last_by_scale = {}
        for s in SCALES:
            tpl_s = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            ts_h, ts_w = tpl_s.shape[:2]
            if ts_h > H or ts_w > W:
                continue
            r = cv2.matchTemplate(img, tpl_s, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(r)
            last_by_scale[s] = (r, ts_h, ts_w, mv, ml)
            if mv > best_val:
                best_val, best_loc, best_scale = mv, ml, s
                best_shape = (ts_h, ts_w)
            per_scale_top.append((s, mv))

        cx, cy = best_loc[0] + best_shape[1] // 2, best_loc[1] + best_shape[0] // 2

        # 目标区域得分
        target_scores = {}
        for tgt_name, tgt_xy in TARGETS.items():
            best_t = -1.0
            best_t_loc = None
            best_t_scale = 1.0
            for s, (r, ts_h, ts_w, _, _) in last_by_scale.items():
                v, loc = match_at_target(r, ts_h, ts_w, H, W, tgt_xy, radius=40)
                if v > best_t:
                    best_t = v
                    best_t_loc = loc
                    best_t_scale = s
            target_scores[tgt_name] = (best_t, best_t_loc, best_t_scale)

        # 画框
        x0, y0 = best_loc
        x1, y1 = best_loc[0] + best_shape[1], best_loc[1] + best_shape[0]
        cv2.rectangle(overlay, (x0, y0), (x1, y1), palette[idx], 3)
        label = f'{name}  {best_val:.2f}  s={best_scale:.1f}'
        cv2.putText(overlay, label, (x0, max(0, y0 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, palette[idx], 2)

        rows.append({
            'name': name,
            'tpl_size': (tw0, th0),
            'best_val': best_val,
            'best_loc': best_loc,
            'best_scale': best_scale,
            'best_center': (cx, cy),
            'white_x': target_scores['white_X_688_679'],
            'green_c': target_scores['green_C_811_679'],
        })

    # 打印表
    print()
    header = (
        f'{"模板":<26}{"尺寸":<11}{"全局最优":<10}'
        f'{"全局loc":<18}{"scale":<6}'
        f'{"白X目标(688,679)":<22}{"绿√目标(811,679)":<22}'
    )
    print(header)
    print('-' * len(header))
    for r in rows:
        wx_v, wx_loc, wx_s = r['white_x']
        gc_v, gc_loc, gc_s = r['green_c']
        wx_s_str = f'({wx_v:.3f}@s{wx_s:.1f})'
        gc_s_str = f'({gc_v:.3f}@s{gc_s:.1f})'
        print(
            f'{r["name"]:<26}'
            f'{str(r["tpl_size"]):<11}'
            f'{r["best_val"]:<10.3f}'
            f'{str(r["best_loc"]):<18}'
            f'{r["best_scale"]:<6.1f}'
            f'{wx_s_str:<22}'
            f'{gc_s_str:<22}'
        )

    # 解读
    print('\n结论速览（0.75 为运行时常用阈值，0.85+ 为强命中）:')
    for r in rows:
        wx_v = r['white_x'][0]
        gc_v = r['green_c'][0]
        flag_w = '✓' if wx_v >= 0.75 else '✗'
        flag_g = '✓' if gc_v >= 0.75 else '✗'
        print(
            f'  {r["name"]:<26}  白X(688,679)={wx_v:.3f} {flag_w}   '
            f'绿√(811,679)={gc_v:.3f} {flag_g}'
        )

    # 保存 overlay
    out = ROOT / 'tests' / '弹窗1_tpl_overlay.png'
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode('.png', overlay)[1].tofile(str(out))
    print(f'\n可视化已保存: {out}')

    # 画两个目标圈
    overlay2 = img.copy()
    for r in rows:
        x0, y0 = r['best_loc']
        x1, y1 = x0 + 110, y0 + 110  # 大致 112
    for tgt_name, (tx, ty) in TARGETS.items():
        cv2.circle(overlay2, (tx, ty), 50, (0, 255, 0), 2)
        cv2.putText(overlay2, tgt_name, (tx - 40, ty - 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    cv2.imwrite(str(ROOT / 'tests' / '弹窗1_targets.png'), overlay2)


if __name__ == '__main__':
    main()

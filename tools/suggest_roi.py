# -*- coding: utf-8 -*-
"""ROI 建议工具（v2.4.22，dev 分支阶段 1.5）。

用途：拿一张**真实界面截图**，对所有模板跑一遍全屏匹配，输出每个模板的
实际命中位置与**建议 ROI**。ROI 铺开必须由数据决定，不能凭印象猜坐标。

用法：
    # 用已有截图
    python tools/suggest_roi.py shot.png

    # 从当前游戏窗口实时截图
    python tools/suggest_roi.py --live

输出：
    - 表格：模板 | 命中 | 分数 | 位置(px) | 相对位置(rx,ry) | 建议 ROI
    - 建议 ROI 的取法：命中位置 ± PAD 像素，再裁到画面内
      （PAD 默认 60px，足以覆盖窗口缩放带来的抖动，但远比全屏小）

注意：建议 ROI 只在**该张截图对应的界面**成立。换界面/换分辨率要重跑。
即便 ROI 设小了也不会丢目标——运行期有 fallback_full 兜底（见
tests/test_roi_safety.py），只是会慢一点并打警告。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PAD = 60          # 建议 ROI 在命中位置外扩的像素
THRESHOLD = 0.6


def _load_tpls(tpl_dir):
    out = []
    for name in sorted(os.listdir(tpl_dir)):
        if name.lower().endswith('.png'):
            out.append(os.path.join(tpl_dir, name))
    return out


def _match_one(cv2, np, screen_bgr, tpl_path, scale=1.0):
    """单尺度 masked/普通匹配，返回 (score, (cx, cy), tpl_shape)。"""
    tpl = cv2.imread(tpl_path, cv2.IMREAD_UNCHANGED)
    if tpl is None:
        return (0.0, None, None)
    if tpl.ndim == 3 and tpl.shape[2] == 4 and bool((tpl[:, :, 3] < 128).any()):
        from afk_bot.infra.input import _masked_ccoeff_map
        bgr = tpl[:, :, :3]
        mask = (tpl[:, :, 3] > 0).astype(np.float32)
        if scale != 1.0:
            bgr = cv2.resize(bgr, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_AREA)
            mask = cv2.resize(mask, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_AREA)
        m = _masked_ccoeff_map(screen_bgr, bgr, mask)
        if m is None:
            return (0.0, None, None)
        _, mx, _, ml = cv2.minMaxLoc(m)
        th, tw = bgr.shape[:2]
        return (float(mx), (ml[0] + tw // 2, ml[1] + th // 2), (tw, th))

    bgr = tpl[:, :, :3]
    if scale != 1.0:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_AREA)
    res = cv2.matchTemplate(screen_bgr, bgr, cv2.TM_CCOEFF_NORMED)
    _, mx, _, ml = cv2.minMaxLoc(res)
    th, tw = bgr.shape[:2]
    return (float(mx), (ml[0] + tw // 2, ml[1] + th // 2), (tw, th))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image', nargs='?', help='截图路径；省略时用 --live')
    ap.add_argument('--live', action='store_true', help='从游戏窗口实时截图')
    ap.add_argument('--pad', type=int, default=PAD)
    ap.add_argument('--scale', type=float, default=1.0)
    args = ap.parse_args()

    import cv2
    import numpy as np
    from PIL import Image

    if args.live:
        from afk_bot.infra.screen import ScreenCapture
        sc = ScreenCapture(log_callback=lambda *a, **k: None)
        img, _l, _t, _w, _h = sc.snap()
        if img is None or img.width <= 0:
            print('截图失败（游戏窗口未就绪）')
            return 2
    elif args.image:
        img = Image.open(args.image).convert('RGB')
    else:
        ap.error('需要 image 路径或 --live')

    screen = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    H, W = screen.shape[:2]
    print(f'截图 {W}x{H}，模板目录 {os.path.join(ROOT, "templates")}\n')
    print(f'{"模板":30s} {"分数":>6s} {"位置":>13s} {"相对位置":>15s}  '
          f'建议 ROI (x,y,w,h)')
    print('-' * 100)

    hits = 0
    for tpl_path in _load_tpls(os.path.join(ROOT, 'templates')):
        score, loc, shape = _match_one(cv2, np, screen, tpl_path, args.scale)
        name = os.path.basename(tpl_path)
        if loc is None or score < THRESHOLD:
            print(f'{name:30s} {score:6.3f} {"未命中":>13s}')
            continue
        hits += 1
        cx, cy = loc
        rx, ry = cx / W, cy / H
        tw, th = shape
        x0 = max(0, cx - tw // 2 - args.pad)
        y0 = max(0, cy - th // 2 - args.pad)
        x1 = min(W, cx + tw // 2 + args.pad)
        y1 = min(H, cy + th // 2 + args.pad)
        roi = (x0, y0, x1 - x0, y1 - y0)
        print(f'{name:30s} {score:6.3f} ({cx:4d},{cy:4d})  '
              f'({rx:.3f},{ry:.3f})  {roi}  面积占比 '
              f'{roi[2]*roi[3]/(W*H)*100:.1f}%')

    print('-' * 100)
    print(f'命中 {hits} 个模板。建议 ROI = 命中框 ± {args.pad}px。')
    print('提示：ROI 只在当前截图对应的界面成立；换界面需重跑。'
          '即使 ROI 设小，运行期 fallback_full 也会兜底，不会丢目标。')
    return 0


if __name__ == '__main__':
    sys.exit(main())

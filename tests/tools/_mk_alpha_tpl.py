"""生成 RGBA 透明背景模板：圆形按钮内部 alpha=255（保留源图 RGB），
圆外 alpha=0（完全透明）。覆盖 templates/btn_confirm_*.png（已备份）。

源图: Pictures/Screenshots/弹窗1.png
中心: 白 X (688,679) / 绿 √ (811,679)，裁剪 112x112，圆形 mask 半径 48。
"""
import cv2
import numpy as np
from pathlib import Path

ROOT = Path(r'C:\Users\x9502\WorkBuddy\20260320211537')
SRC = Path(r'C:\Users\x9502\Pictures\Screenshots\弹窗1.png')
RADIUS = 48
SIZE = 112
CENTERS = {
    'btn_confirm_cancel.png': (688, 679),  # 白 X
    'btn_confirm_check.png': (811, 679),   # 绿 √
}


def load_bgr(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def main():
    src = load_bgr(SRC)
    h, w = src.shape[:2]
    # 圆形 mask 单通道 (0/255)
    yy, xx = np.ogrid[:SIZE, :SIZE]
    center = (SIZE - 1) / 2.0
    dist = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)
    circle = (dist <= RADIUS).astype(np.uint8) * 255  # 0/255

    for name, (cx, cy) in CENTERS.items():
        x0, y0 = cx - SIZE // 2, cy - SIZE // 2
        x1, y1 = x0 + SIZE, y0 + SIZE
        assert 0 <= x0 and y1 <= w and 0 <= y0 and y1 <= h, (name, x0, y0)
        crop_bgr = src[y0:y1, x0:x1].copy()
        # RGBA: 圆内保留源图 RGB，圆外 RGB 置 0 + alpha=0（完全透明）
        alpha = circle.copy()
        crop_rgba = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2BGRA)
        crop_rgba[:, :, 3] = alpha
        # 圆外 RGB 置 0（透明区）——避免某些查看器灰底
        outside = alpha == 0
        crop_rgba[outside, 0:3] = 0

        out = ROOT / 'templates' / name
        ok = cv2.imencode('.png', crop_rgba)[1].tofile(str(out))
        n_alpha0 = int((alpha == 0).sum())
        n_alpha255 = int((alpha == 255).sum())
        print(f'{name:<24} {out}  alpha0={n_alpha0:>4} alpha255={n_alpha255:>5} '
              f'写回 {"OK" if ok else "FAIL"}')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""切换阵容「>」按钮的**真实算法**端到端测试（v2.4.20）。

与 test_switch_btn_region.py 的分工：
  - 那个测的是「几何覆盖」+ 源码静态约束（不碰图像）
  - 这个把模板按真实布局合成到画布上，用程序自身的
    `_masked_template_scan` + `FormationManager._switch_btn_region`
    真跑一遍，验证**分数与命中坐标**

之所以要这一层：v2.4.20 的 bug 恰是「几何上写在区域里、真跑出来只有
0.44」——只做几何断言会漏掉算法阈值这一环。

三组布局取自真机实测：
  横屏 1284x1026  rx=0.686 ry=0.537  分数 0.992
  竖屏  747x1334  rx=0.918 ry=0.537  分数 0.911
  宽屏 1280x617   rx=0.480 ry=0.522  分数 0.838  ← v2.4.20 报障
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILED = []
PASSED = [0]
SKIPPED = []

THRESHOLD = 0.6          # switch_formation 里 template_match 的阈值
TPL_PATH = os.path.join(ROOT, 'templates', 'btn_formation_next.png')


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def skip(name, why):
    SKIPPED.append(name)
    print(f'[SKIP] {name}  {why}', flush=True)


try:
    import cv2
    from afk_bot.infra.input import _masked_template_scan
    from afk_bot.auto_farm.formation import FormationManager
    _OK = True
except Exception as _e:      # 无 cv2 / 无显示器时的兜底
    _OK = False
    _ERR = repr(_e)


class _FakeScreen:
    def __init__(self, w, h):
        self.w, self.h = w, h

    def win_dims(self):
        return (0, 0, self.w, self.h)


class _FakeMgr:
    def __init__(self, w, h):
        self._screen = _FakeScreen(w, h)


def _region(w, h):
    return FormationManager._switch_btn_region(_FakeMgr(w, h))


def _load_tpl():
    tpl = cv2.imread(TPL_PATH, cv2.IMREAD_UNCHANGED)
    bgr = tpl[:, :, :3]
    mask = tpl[:, :, 3].astype(np.float32) / 255.0
    return bgr, mask, tpl


def _build_screen(W, H, btn_rx, btn_ry, tpl_rgba, seed=42):
    """深色噪声背景 + 渐变，再把模板按 alpha 贴到指定比例位置。"""
    rng = np.random.default_rng(seed)
    bg = rng.integers(18, 78, (H, W, 3), dtype=np.uint8)
    # 加一层纵向渐变，避免纯噪声导致归一化分母抖动
    ramp = np.linspace(0, 40, H, dtype=np.float32)[:, None, None]
    bg = np.clip(bg.astype(np.float32) + ramp, 0, 255).astype(np.uint8)

    th, tw = tpl_rgba.shape[:2]
    cx, cy = int(btn_rx * W), int(btn_ry * H)
    x0, y0 = cx - tw // 2, cy - th // 2
    x0 = max(0, min(x0, W - tw))
    y0 = max(0, min(y0, H - th))
    tpl_bgr = tpl_rgba[:, :, :3].astype(np.float32)
    alpha = (tpl_rgba[:, :, 3:4].astype(np.float32) / 255.0)
    roi = bg[y0:y0 + th, x0:x0 + tw].astype(np.float32)
    bg[y0:y0 + th, x0:x0 + tw] = np.clip(
        tpl_bgr * alpha + roi * (1 - alpha), 0, 255).astype(np.uint8)
    return bg, (x0 + tw // 2, y0 + th // 2)


def _scan(region, img, tpl_bgr, mask, scale_range=(0.5, 2.0, 0.1)):
    """在 region 内跑真实 masked NCC，返回 (分数, 全图中心 x, y, scale)。"""
    x, y, w, h = region
    sub = img[y:y + h, x:x + w]
    r = _masked_template_scan(sub, tpl_bgr, mask, scale_range)
    if r is None:
        return (0.0, None, None, None)
    val, loc, scale, (th, tw) = r
    return (float(val), x + loc[0] + tw // 2, y + loc[1] + th // 2, scale)


if not _OK:
    skip('全部', f'依赖不可用：{_ERR}')
else:
    tpl_bgr, mask, tpl_rgba = _load_tpl()
    check('0a 模板可加载（64x64 RGBA）', tpl_rgba.shape == (64, 64, 4),
          str(tpl_rgba.shape))

    # ── ① 三组真实布局：新区域必须命中且坐标准确 ──────────────
    CASES = [
        ('横屏 1284x1026', 1284, 1026, 0.686, 0.537),
        ('竖屏 747x1334', 747, 1334, 0.918, 0.537),
        ('宽屏 1280x617（报障）', 1280, 617, 0.480, 0.522),
    ]
    for label, W, H, rx, ry in CASES:
        img, expect = _build_screen(W, H, rx, ry, tpl_rgba)
        reg = _region(W, H)
        val, cx, cy, sc = _scan(reg, img, tpl_bgr, mask)
        hit = val >= THRESHOLD
        check(f'1 {label}：区域内分数 ≥ {THRESHOLD}', hit,
              f'{val:.3f}（scale={sc}）')
        if hit:
            dx, dy = abs(cx - expect[0]), abs(cy - expect[1])
            check(f'2 {label}：命中坐标误差 ≤3px', dx <= 3 and dy <= 3,
                  f'实测({cx},{cy}) 期望{expect} 偏差({dx},{dy})')

    # ── ③ 对照：旧区域 0.65~1.00 在宽屏布局下必须失败 ──────────
    W, H = 1280, 617
    img, expect = _build_screen(W, H, 0.480, 0.522, tpl_rgba)
    old_reg = (int(W * 0.65), int(H * 0.25), int(W * 0.35), int(H * 0.50))
    val_old, _cx, _cy, _sc = _scan(old_reg, img, tpl_bgr, mask)
    check('3 对照：旧区域 0.65~1.00 在宽屏下 < 阈值（复现报障）',
          val_old < THRESHOLD, f'{val_old:.3f}')
    val_new, _cx2, _cy2, _sc2 = _scan(_region(W, H), img, tpl_bgr, mask)
    check('3b 同图换新区域后 ≥ 阈值（修复有效）', val_new >= THRESHOLD,
          f'{val_new:.3f} vs 旧 {val_old:.3f}')

    # ── ④ 误匹配防护：区域里没有按钮时不得误报 ────────────────
    for label, W, H in (('1280x617', 1280, 617), ('747x1334', 747, 1334)):
        rng = np.random.default_rng(7)
        blank = rng.integers(18, 78, (H, W, 3), dtype=np.uint8)
        ramp = np.linspace(0, 40, H, dtype=np.float32)[:, None, None]
        blank = np.clip(blank.astype(np.float32) + ramp, 0, 255).astype(np.uint8)
        val, *_ = _scan(_region(W, H), blank, tpl_bgr, mask)
        check(f'4 {label}：无按钮画面区域内分数 < 阈值（不误报）',
              val < THRESHOLD, f'{val:.3f}')

    # ── ⑤ 区域外有同款按钮时，不得被拉进区域（防误点）──────────
    # 把按钮贴到 rx=0.10（远在 0.30 左边界之外），区域内应无高分
    W, H = 1280, 617
    img_out, _ = _build_screen(W, H, 0.10, 0.52, tpl_rgba, seed=11)
    val_out, *_ = _scan(_region(W, H), img_out, tpl_bgr, mask)
    check('5 区域外的同款按钮不会被误认（左边界 0.30 起效）',
          val_out < THRESHOLD, f'{val_out:.3f}')

    # ── ⑥ 区域外按钮 + 区域内真按钮共存 → 必须命中区域内那个 ────
    img_both, expect = _build_screen(W, H, 0.480, 0.522, tpl_rgba, seed=13)
    # 再在 rx=0.10 叠一个
    th, tw = tpl_rgba.shape[:2]
    tx, ty = int(0.10 * W) - tw // 2, int(0.52 * H) - th // 2
    tb = tpl_rgba[:, :, :3].astype(np.float32)
    ta = tpl_rgba[:, :, 3:4].astype(np.float32) / 255.0
    roi = img_both[ty:ty + th, tx:tx + tw].astype(np.float32)
    img_both[ty:ty + th, tx:tx + tw] = np.clip(
        tb * ta + roi * (1 - ta), 0, 255).astype(np.uint8)
    val_b, cx_b, cy_b, _ = _scan(_region(W, H), img_both, tpl_bgr, mask)
    check('6 共存时命中区域内按钮（不会被左侧干扰抢走）',
          val_b >= THRESHOLD and abs(cx_b - expect[0]) <= 3
          and abs(cy_b - expect[1]) <= 3,
          f'分数={val_b:.3f} 命中({cx_b},{cy_b}) 期望{expect}')

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）'
      + (f'，跳过 {len(SKIPPED)} 项' if SKIPPED else ''))

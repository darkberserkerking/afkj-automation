# -*- coding: utf-8 -*-
"""v2.4.5：赛季职业圆标「内缩模板」回归（真机 2026-11-07 12:31 卡死修复）。

真机现象：辅助职业出现「!」可装备角标（等级横幅同时压住圆标边缘），
主界面圆标模板未命中 → 更新装备任务反复重试 20 次后失败/停止。

差分实测（用户两张真机截图，maskedNCC 与 infra/input.py 同源算法）：
  原模板主界面：辅助 0.687 / 法师 0.533 / 射手 0.637 / 战士 0.559 —— 全部 < 0.70 ✗
  （游侠 0.856 / 坦克 0.811 险过——未遮挡的职业能过，正是"时好时坏"的根源）
  内缩模板主界面：6/6 ≥ 0.780 ✓   内缩模板底部 tab：6/6 ≥ 0.875 ✓

覆盖：
  A. 模板一致性：CLASS_TPL 全部指向 *_inner.png 且文件存在、alpha 确实内缩
  B. 行为分叉（真机截图）：内缩模板 6/6 命中；原模板主界面至少 3 个未命中
     （锁住"为什么必须 inner"，防止有人改回去）
  C. 底部 tab：内缩模板 6/6 ≥ 0.82

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_season_class_tpl_inner.py
"""
import os
import sys

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot.infra.input import _masked_template_scan  # noqa: E402

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


IMG_MAIN = os.path.join(ROOT, 'tests', 'assets',
                        'season_main_alert_20261107.jpg')
IMG_TAB = os.path.join(ROOT, 'tests', 'assets',
                       'season_support_detail_alert_20261107.jpg')
TPL_DIR = os.path.join(ROOT, 'templates')

screen_main = cv2.cvtColor(np.array(Image.open(IMG_MAIN)), cv2.COLOR_RGB2BGR)
screen_tab = cv2.cvtColor(np.array(Image.open(IMG_TAB)), cv2.COLOR_RGB2BGR)

K = np.ones((5, 5), np.uint8)
FILES = ['support', 'mage', 'warrior', 'ranger', 'marksman', 'tank']


def load(fn):
    t = cv2.imdecode(np.fromfile(os.path.join(TPL_DIR, fn), dtype=np.uint8),
                     cv2.IMREAD_UNCHANGED)
    return t[:, :, :3], (t[:, :, 3] > 0).astype(np.float32), t[:, :, 3]


def score(screen, bgr, mask, rng):
    v, loc, s, _ = _masked_template_scan(screen, bgr, mask, rng)
    return v, loc, s


# ── A. 模板一致性 ───────────────────────────────────────────
print('── A. CLASS_TPL 指向内缩模板且文件有效 ──')
try:
    import importlib
    import tasks.season_equipment as se
    importlib.reload(se)   # 防止同进程旧缓存
    check('A1 CLASS_TPL 六职业全部指向 *_inner.png',
          all(fn.endswith('_inner.png') for fn in se.CLASS_TPL.values()),
          str(list(se.CLASS_TPL.values())))
    check('A2 六个 inner 模板文件都存在',
          all(os.path.isfile(p) for p in se.CLASS_TPL.values()))
    for zh, p in se.CLASS_TPL.items():
        bgr_o, mask_o, alpha_o = load(os.path.basename(p).replace(
            '_inner.png', '.png'))
        bgr_i, mask_i, alpha_i = load(os.path.basename(p))
        shrunk = int((mask_o > 0).sum() - (mask_i > 0).sum())
        same_bgr = np.array_equal(bgr_o, bgr_i)
        check(f'A3 {zh} alpha 确实内缩（去掉 {shrunk}px 边缘）且 BGR 不变',
              300 <= shrunk <= 900 and same_bgr,
              f'shrunk={shrunk} bgr_same={same_bgr}')
except Exception as _e:
    check('A 模板一致性', False, repr(_e))

# ── B. 主界面行为分叉（阈值 0.70，任务真实区间）─────────────
print('── B. 主界面大圆标（img1，阈值 0.70）──')
try:
    rng = (1.3, 1.9, 0.1)
    n_inner_ok = 0
    n_orig_bad = 0
    for f in FILES:
        bgr_o, mask_o, _ = load(f'season_class_{f}.png')
        bgr_i, mask_i, _ = load(f'season_class_{f}_inner.png')
        v_o, _, _ = score(screen_main, bgr_o, mask_o, rng)
        v_i, loc_i, _ = score(screen_main, bgr_i, mask_i, rng)
        if v_i >= 0.70:
            n_inner_ok += 1
        if v_o < 0.70:
            n_orig_bad += 1
        print(f'    {f:9s} 原={v_o:.3f}  内缩={v_i:.3f}@{loc_i}')
    check('B1 内缩模板主界面 6/6 命中（阈值 0.70）',
          n_inner_ok == 6, f'{n_inner_ok}/6')
    check('B2 原模板主界面至少 3 个未命中（锁住改 inner 的理由）',
          n_orig_bad >= 3, f'原模板未命中 {n_orig_bad}/6')
except Exception as _e:
    check('B 主界面分叉', False, repr(_e))

# ── C. 底部 tab（阈值 0.82，tab 区间）───────────────────────
print('── C. 详情页底部 tab（img2，阈值 0.82）──')
try:
    rng = (0.8, 1.4, 0.1)
    n_ok = 0
    for f in FILES:
        bgr_i, mask_i, _ = load(f'season_class_{f}_inner.png')
        v, loc, _ = score(screen_tab, bgr_i, mask_i, rng)
        if v >= 0.82:
            n_ok += 1
        print(f'    {f:9s} 内缩={v:.3f}@{loc}')
    check('C1 内缩模板底部 tab 6/6 命中（阈值 0.82）',
          n_ok == 6, f'{n_ok}/6')
except Exception as _e:
    check('C 底部 tab', False, repr(_e))

print('')
print(f'通过 {PASSED[0]} / {PASSED[0] + len(FAILED)}')
if FAILED:
    print('失败用例：')
    for n in FAILED:
        print(' -', n)
    sys.exit(1)
print('ALL PASS')

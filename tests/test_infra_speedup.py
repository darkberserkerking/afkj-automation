# -*- coding: utf-8 -*-
"""基础设施提速回归（v2.4.22，dev 分支「对齐 MaaFramework」阶段 1）。

三项改动，各自守住一个不变量：

① `ocr_screen(roi=...)` 开放区域识别
   - roi 必须真的透传给引擎
   - **缓存键必须含 roi**：指纹按整图 10 点采样算，同一帧不同 roi 指纹相同，
     只按指纹做键会把 A 区结果当 B 区返回（静默错数据，比慢严重）

② 模板解码缓存 `_load_tpl_cached`
   - 二次调用命中缓存（同一 ndarray 对象）
   - 模板文件被替换后自动失效（键含 mtime/size）

③ 多尺度两段式
   - 高置信命中走快路径，**结果与全范围扫描一致**
   - 低分/未命中必须回落到全范围（不允许"更快但漏检"）
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

import afk_bot.infra.input as I
from afk_bot.infra.screen import ScreenCapture

FAILED = []
PASSED = [0]
SKIPPED = []


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


# ── ① ocr_screen 的 roi 与缓存键 ─────────────────────────────
class _FakeEngine:
    """记录每次 recognize 收到的 roi，并按 roi 返回可区分的结果。"""

    def __init__(self):
        self.calls = []

    def recognize(self, img, roi=None):
        self.calls.append(roi)
        tag = 'full' if roi is None else f'roi{roi}'
        # 真实引擎返回 [(text, box4pts, score)]
        return [(tag, [[10, 10], [40, 10], [40, 30], [10, 30]], 0.99)]


def _make_sc(engine):
    sc = ScreenCapture.__new__(ScreenCapture)
    sc._ocr_engine = engine
    sc._ocr_cache = {}
    sc._ocr_cache_ttl = 60.0
    sc._hwnd = None
    sc._last_snap_empty = False
    sc._log = lambda *a, **k: None
    return sc


img = Image.new('RGB', (200, 150), (30, 40, 50))
eng = _FakeEngine()
sc = _make_sc(eng)

r1 = sc.ocr_screen(img=img, roi=(10, 10, 50, 40))
check('1a roi 透传到引擎', eng.calls and eng.calls[-1] == (10, 10, 50, 40),
      str(eng.calls[-1] if eng.calls else None))
check('1b roi 结果按预期返回', r1 and r1[0][2].startswith('roi'),
      str(r1))

# 同一帧、不同 roi：必须各自识别，不能互相命中缓存
r2 = sc.ocr_screen(img=img, roi=(60, 20, 30, 30))
check('1c 不同 roi 不串缓存（第二次必须真的调引擎）', len(eng.calls) == 2,
      f'引擎被调 {len(eng.calls)} 次')
check('1d 第二次拿到的是自己区域的结果', r2 and r2[0][2] == 'roi(60, 20, 30, 30)',
      str(r2))

# 同一帧、同一 roi：应命中缓存
r3 = sc.ocr_screen(img=img, roi=(10, 10, 50, 40))
check('1e 相同 roi 命中缓存（引擎不再被调用）', len(eng.calls) == 2,
      f'引擎被调 {len(eng.calls)} 次')
check('1f 命中缓存返回同一结果', r3 and r3[0][2] == 'roi(10, 10, 50, 40)')

# 全屏（roi=None）与 roi 也不能串
r4 = sc.ocr_screen(img=img)
check('1g roi=None 与带 roi 互不干扰', r4 and r4[0][2] == 'full',
      str(r4))
check('1h 全屏也只调了一次引擎', len(eng.calls) == 3, f'{len(eng.calls)} 次')

# ── ② 模板解码缓存 ──────────────────────────────────────────
tpl_src = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'templates', 'btn_formation_next.png')
if not os.path.isfile(tpl_src):
    skip('2 模板解码缓存', '模板文件不存在')
else:
    td = tempfile.mkdtemp(prefix='afkj_tplcache_')
    tpl = os.path.join(td, 'btn.png')
    with open(tpl_src, 'rb') as f:
        data = f.read()
    with open(tpl, 'wb') as f:
        f.write(data)

    I._TPL_CACHE.clear()
    a = I._load_tpl_cached(tpl)
    b = I._load_tpl_cached(tpl)
    check('2a 首次解码成功', a is not None, str(None if a is None else a.shape))
    check('2b 二次调用命中缓存（同一对象）', a is b, f'{id(a)} vs {id(b)}')

    # 换内容 + 改 mtime → 自动失效
    time.sleep(0.01)
    with open(tpl, 'wb') as f:
        f.write(data)
    os.utime(tpl, None)
    c = I._load_tpl_cached(tpl)
    check('2c 文件变更后自动失效（返回新对象）', c is not a,
          f'{id(c)} vs {id(a)}')

    # 不存在的文件 → None，不抛
    check('2d 文件不存在时返回 None', I._load_tpl_cached(
        os.path.join(td, 'nope.png')) is None)
    I._TPL_CACHE.clear()

# ── ③ 两段式扫描：结果必须与全范围一致 ──────────────────────
try:
    import cv2
    from afk_bot.infra.input import _masked_template_scan
    _HAS_CV = True
except Exception as e:
    _HAS_CV = False
    _CV_ERR = repr(e)

if not _HAS_CV:
    skip('3 两段式扫描', _CV_ERR)
else:
    # 构造一张带按钮的图（复用 v2.4.20 的做法）
    tpl_img = cv2.imread(tpl_src, cv2.IMREAD_UNCHANGED)
    H, W = 600, 800
    rng = np.random.default_rng(5)
    canvas = rng.integers(20, 70, (H, W, 3), dtype=np.uint8)
    th, tw = tpl_img.shape[:2]
    bx, by = 400, 300
    x0, y0 = bx - tw // 2, by - th // 2
    a_ch = tpl_img[:, :, 3:4].astype(np.float32) / 255.0
    roi_px = canvas[y0:y0 + th, x0:x0 + tw].astype(np.float32)
    canvas[y0:y0 + th, x0:x0 + tw] = np.clip(
        tpl_img[:, :, :3].astype(np.float32) * a_ch + roi_px * (1 - a_ch),
        0, 255).astype(np.uint8)

    tpl_bgr = tpl_img[:, :, :3]
    mask01 = (tpl_img[:, :, 3] > 0).astype(np.float32)

    def scan(cr, src=canvas):
        r = _masked_template_scan(src, tpl_bgr, mask01, cr)
        if r is None:
            return (-1.0, None)
        v, loc, s, (h, w) = r
        return (v, (loc[0] + w // 2, loc[1] + h // 2))

    THRESH, FAST_OK = 0.6, 0.75
    old = scan((0.5, 2.0, 0.1))
    fast = scan((0.9, 1.1, 0.1))
    new = fast if fast[0] >= FAST_OK else old
    check('3a 命中场景：快路径结果与全范围完全一致',
          abs(old[0] - new[0]) < 1e-9 and old[1] == new[1],
          f'全范围 {old[0]:.3f}@{old[1]} / 两段式 {new[0]:.3f}@{new[1]}')
    check('3b 命中场景：确实走了快路径（快扫得分已达门槛）',
          fast[0] >= FAST_OK, f'快扫 {fast[0]:.3f}')

    # 无按钮画面 → 快扫不达标 → 必须回落全范围（结果仍是"未命中"）
    blank = rng.integers(20, 70, (H, W, 3), dtype=np.uint8)
    fast_b = scan((0.9, 1.1, 0.1), blank)
    old_b = scan((0.5, 2.0, 0.1), blank)
    new_b = fast_b if fast_b[0] >= FAST_OK else old_b
    check('3c 未命中场景：快扫低于门槛 → 回落全范围', fast_b[0] < FAST_OK,
          f'快扫 {fast_b[0]:.3f}')
    check('3d 未命中场景：两段式与全范围结论一致（都不命中）',
          (old_b[0] < THRESH) and (new_b[0] < THRESH),
          f'全范围 {old_b[0]:.3f} / 两段式 {new_b[0]:.3f}')

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）'
      + (f'，跳过 {len(SKIPPED)} 项' if SKIPPED else ''))

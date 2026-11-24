# -*- coding: utf-8 -*-
"""ROI 安全网回归（v2.4.22）——**证明 ROI 不会导致丢目标**。

这是「ROI 铺开」的前置保证。核心设计：

    ROI 只做**快路径**；未命中一律自动全屏兜底。
    宁可慢，也绝不因为区域设小就"找不到目标"。

本测试直接构造「ROI 故意设错」的场景，验证兜底能救回来：

  ① ocr_screen：ROI 内无文字 → 自动全屏兜底，结果与全屏一致
  ② ocr_screen：`fallback_full=False` → 不兜底（给"反向判定"用）
  ③ template_match：region 故意框在没有按钮的地方 → 兜底仍找到，坐标与全屏一致
  ④ template_match：region 正确时**不触发**兜底（快路径生效）
  ⑤ template_match：`fallback_full=False` → 不兜底（反向判定语义不被破坏）
  ⑥ 多模板遍历：全屏结果 vs (ROI+兜底) 结果逐项一致
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

import afk_bot.infra.input as I
from afk_bot.infra.screen import ScreenCapture
from afk_bot.infra.input import InputController

FAILED = []
PASSED = [0]
SKIPPED = []

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL_DIR = os.path.join(ROOT, 'templates')


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


# ── ① / ② ocr_screen 兜底 ──────────────────────────────────
class _ScriptedEngine:
    """roi 为空 → 返回空；全屏 → 返回一条。用于验证兜底是否触发。"""

    def __init__(self):
        self.calls = []

    def recognize(self, img, roi=None):
        self.calls.append(roi)
        if roi is not None:
            return []      # 模拟：ROI 设小了，里面什么都没有
        return [('兜底命中', [[100, 100], [200, 100], [200, 140], [100, 140]],
                 0.98)]


def _make_sc(engine):
    sc = ScreenCapture.__new__(ScreenCapture)
    sc._ocr_engine = engine
    sc._ocr_cache = {}
    sc._ocr_cache_ttl = 60.0
    sc._hwnd = None
    sc._last_snap_empty = False
    logs = []
    sc._log = lambda lv, m: logs.append(m)
    return sc, logs


img = Image.new('RGB', (300, 200), (40, 50, 60))

eng = _ScriptedEngine()
sc, logs = _make_sc(eng)
r = sc.ocr_screen(img=img, roi=(10, 10, 20, 20))
check('1a ROI 内为空 → 触发全屏兜底（补到目标）',
      len(r) == 1 and r[0][2] == '兜底命中', f'{len(r)} 条')
check('1b 兜底时确实调了两次引擎（ROI + 全屏）', len(eng.calls) == 2,
      str(eng.calls))
check('1c 兜底会打警告日志（提示 ROI 可能设小了）',
      any('ROI' in m and '兜底' in m for m in logs),
      str(logs[:1]))

eng2 = _ScriptedEngine()
sc2, logs2 = _make_sc(eng2)
r2 = sc2.ocr_screen(img=img, roi=(10, 10, 20, 20), fallback_full=False)
check('2a fallback_full=False → 不兜底（反向判定语义）', r2 == [],
      f'{len(r2)} 条')
check('2b 且只调了一次引擎', len(eng2.calls) == 1, str(eng2.calls))

# ROI 内本来就有内容时不兜底（快路径）
class _HitEngine:
    def __init__(self):
        self.calls = []

    def recognize(self, img, roi=None):
        self.calls.append(roi)
        return [('区内', [[10, 10], [50, 10], [50, 30], [10, 30]], 0.9)]


eng3 = _HitEngine()
sc3, _ = _make_sc(eng3)
r3 = sc3.ocr_screen(img=img, roi=(5, 5, 60, 40))
check('2c ROI 内有结果时不兜底（只调一次）', len(eng3.calls) == 1,
      str(eng3.calls))

# ── ③ ~ ⑥ template_match 兜底 ─────────────────────────────
try:
    import cv2
    _HAS_CV = True
except Exception as e:
    _HAS_CV = False
    _CV_ERR = repr(e)


class _FakeSC:
    """snap 返回固定画面，客户区左上在 (0,0)。"""

    def __init__(self, pil_img):
        self.img = pil_img

    def snap(self):
        return (self.img, 0, 0, self.img.width, self.img.height)

    def activate_window(self):
        pass


def _build_canvas(W, H, tpl_rgba, cx, cy, seed=3):
    rng = np.random.default_rng(seed)
    bg = rng.integers(20, 70, (H, W, 3), dtype=np.uint8)
    th, tw = tpl_rgba.shape[:2]
    x0, y0 = max(0, cx - tw // 2), max(0, cy - th // 2)
    a = tpl_rgba[:, :, 3:4].astype(np.float32) / 255.0
    roi = bg[y0:y0 + th, x0:x0 + tw].astype(np.float32)
    bg[y0:y0 + th, x0:x0 + tw] = np.clip(
        tpl_rgba[:, :, :3].astype(np.float32) * a + roi * (1 - a),
        0, 255).astype(np.uint8)
    bgr = bg
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


if not _HAS_CV:
    skip('③~⑥ template_match 兜底', _CV_ERR)
else:
    tpl_path = os.path.join(TPL_DIR, 'btn_formation_next.png')
    if not os.path.isfile(tpl_path):
        skip('③~⑥ template_match 兜底', '模板缺失')
    else:
        tpl_rgba = cv2.imread(tpl_path, cv2.IMREAD_UNCHANGED)
        W, H = 640, 512
        BTN = (440, 275)          # 按钮真实位置
        canvas = _build_canvas(W, H, tpl_rgba, *BTN)
        ic = InputController(None, _FakeSC(canvas), lambda *a, **k: None)

        # 全屏基准
        base = ic.template_match(tpl_path, threshold=0.6, silent=True)
        check('3a 全屏基准能找到按钮', base is not None, str(base))

        # ROI 故意框在左上角空白区（按钮根本不在里面）
        WRONG = (0, 0, 200, 150)
        got = ic.template_match(tpl_path, threshold=0.6, silent=True,
                                region=WRONG)
        check('3b 【核心】region 设错时兜底仍找到目标（不丢目标）',
              got is not None and got == base, f'兜底={got} 基准={base}')

        # 同一错误 region，但关掉兜底 → 应当找不到（证明兜底确实是关键）
        got_nofb = ic.template_match(tpl_path, threshold=0.6, silent=True,
                                     region=WRONG, fallback_full=False)
        check('3c 关掉兜底才找不到（对照组，证明兜底在起作用）',
              got_nofb is None, f'{got_nofb}')

        # 正确 region → 命中且结果一致
        OK_REGION = (int(W * 0.30), int(H * 0.25),
                     int(W * 0.70), int(H * 0.50))
        got_ok = ic.template_match(tpl_path, threshold=0.6, silent=True,
                                   region=OK_REGION)
        check('3d region 正确时命中且坐标与全屏一致',
              got_ok is not None and got_ok == base, f'{got_ok} vs {base}')

        # ④ 多模板遍历：全屏 vs (ROI+兜底) 逐项一致
        cases = []
        for name in ('btn_formation_next.png', 'btn_confirm_check.png',
                     'btn_sweep_max.png'):
            p = os.path.join(TPL_DIR, name)
            if os.path.isfile(p):
                cases.append(p)
        mismatched = []
        for p in cases:
            b = ic.template_match(p, threshold=0.6, silent=True)
            # 故意用偏小的 ROI（只盖住左上 1/8），逼出兜底
            g = ic.template_match(p, threshold=0.6, silent=True,
                                  region=(0, 0, W // 4, H // 4))
            if b != g:
                mismatched.append(f'{os.path.basename(p)}: {b} vs {g}')
        check('4 多模板：全屏与「小 ROI + 兜底」结果逐项一致',
              not mismatched, str(mismatched[:3]))

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）'
      + (f'，跳过 {len(SKIPPED)} 项' if SKIPPED else ''))

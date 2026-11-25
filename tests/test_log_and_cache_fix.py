# -*- coding: utf-8 -*-
"""日志前缀 + OCR 缓存失效回归（v2.4.25）。

这两个问题都由真机日志定位：

① 双重级别前缀
   日志里出现 28 行 `[信息] [信息] xxx`。根因：调用方写
   `_log('info', '[信息] 战斗进行中')` —— 既传 level 又手写前缀，
   而 `AutoFarmLoop._log` 无条件再补一次。

② 点击后 OCR 缓存未作废
   2026-11-25 21:49:56~21:50:07 连续 3 轮点底部「玩法目录」，每轮仍判
   「当前在家园场景」（2/14→3/14→4/14），每轮白耗 5 秒。
   OCR 缓存键是「10 点稀疏采样指纹 + TTL 60s」，小面积变化不改指纹 →
   下一轮读到**点击前**的旧结果。点击/按键/拖拽后必须作废缓存。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import afk_bot.infra.input as I
from afk_bot.auto_farm.loop import AFKBotCore
from afk_bot.infra.input import InputController
from afk_bot.infra.screen import ScreenCapture

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


# ── ① 日志前缀不重复 ────────────────────────────────────────
class _FakeLoop:
    """只提供 _log 需要的两个依赖：_verbose 与 log()。"""

    _verbose = True

    def __init__(self):
        self.logged = []

    def log(self, msg):
        self.logged.append(msg)


def run_log(level, msg, verbose=True):
    f = _FakeLoop()
    f._verbose = verbose
    AFKBotCore._log(f, level, msg)
    return f.logged


r = run_log('info', '[信息] 战斗进行中，等待...')
check('1a 已带级别前缀 → 原样保留（不再叠加）', r == ['[信息] 战斗进行中，等待...'],
      str(r))

r = run_log('info', '战斗进行中')
check('1b 未带前缀 → 按 level 补', r == ['[信息] 战斗进行中'], str(r))

r = run_log('warn', '[警告] 未找到按钮')
check('1c 级别与手写前缀一致时不重复', r == ['[警告] 未找到按钮'], str(r))

r = run_log('info', '[成功] 已进入备战')
check('1d 手写前缀与 level 不同时也以手写为准（不叠加双前缀）',
      r == ['[成功] 已进入备战'], str(r))

r = run_log('info', '[自动推图] 已停止')
check('1e 业务标签不是级别前缀 → 正常叠加', r == ['[信息] [自动推图] 已停止'],
      str(r))

r = run_log('info', '[阶段] 备战')
check('1f [阶段] 同样正常叠加', r == ['[信息] [阶段] 备战'], str(r))

r = run_log('info', 'ℹ️ OCR ok', verbose=False)
check('1g 非 verbose 下 ℹ️ 日志仍被过滤', r == [], str(r))

# ── ② OCR 缓存失效 ─────────────────────────────────────────
sc = ScreenCapture.__new__(ScreenCapture)
sc._ocr_cache = {111: (0.0, []), 222: (0.0, [])}
sc.invalidate_ocr_cache()
check('2a invalidate_ocr_cache 清空缓存', sc._ocr_cache == {},
      str(sc._ocr_cache))

sc2 = ScreenCapture.__new__(ScreenCapture)   # __new__ 后没有 _ocr_cache
try:
    sc2.invalidate_ocr_cache()
    check('2b 缓存字段缺失时不抛异常', True)
except Exception as e:
    check('2b 缓存字段缺失时不抛异常', False, repr(e))


class _FakeSC:
    def __init__(self):
        self.activated = 0
        self.invalidated = 0

    def activate_window(self):
        self.activated += 1

    def invalidate_ocr_cache(self):
        self.invalidated += 1


_orig_click = I.pyautogui.click
_orig_down = I.pyautogui.mouseDown
_orig_up = I.pyautogui.mouseUp
_orig_move = I.pyautogui.moveTo
_orig_key = I.win32api.keybd_event
try:
    I.pyautogui.click = lambda *a, **k: None
    I.pyautogui.mouseDown = lambda *a, **k: None
    I.pyautogui.mouseUp = lambda *a, **k: None
    I.pyautogui.moveTo = lambda *a, **k: None
    I.win32api.keybd_event = lambda *a, **k: None

    fsc = _FakeSC()
    ic = InputController(None, fsc, lambda *a, **k: None)

    ic.click_abs(100, 200)
    check('3a click_abs 后作废 OCR 缓存', fsc.invalidated == 1,
          f'invalidated={fsc.invalidated}')

    ic.drag_abs(10, 10, 50, 50, duration=0.01)
    check('3b drag_abs 后作废 OCR 缓存', fsc.invalidated == 2,
          f'invalidated={fsc.invalidated}')

    ic.send_esc()
    check('3c send_esc 后作废 OCR 缓存', fsc.invalidated == 3,
          f'invalidated={fsc.invalidated}')

    ic.send_key_f()
    check('3d send_key_f 后作废 OCR 缓存', fsc.invalidated == 4,
          f'invalidated={fsc.invalidated}')
finally:
    I.pyautogui.click = _orig_click
    I.pyautogui.mouseDown = _orig_down
    I.pyautogui.mouseUp = _orig_up
    I.pyautogui.moveTo = _orig_move
    I.win32api.keybd_event = _orig_key

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')

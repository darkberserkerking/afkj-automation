"""结算界面「挑战」按钮判据验证（v2.2.19）。

真机 2026-09-30 12:10~12:30 报障：**每次战斗胜利后**在结算界面都报
`未找到挑战按钮，重试 #0..#4` → 然后误判「当前挑战已通关」并 ESC 回挂机关卡页
（每轮白等约 5 秒 ✗）。

根因：`click_challenge_btn` 的判据写的是 `'挑战' in _t and '幻灵' not in _t`
—— 而**结算界面的按钮文字正是「幻灵挑战」**（用户截图 OCR 实测：
@(875.5, 961.5) rx0.683/ry0.933 conf0.99，下方紧跟战力「10090万」）✗

本测试用**真机实测坐标**构造帧，并覆盖两个方向：
  · 结算界面只有「幻灵挑战」→ 必须点到 ✓（旧判据 0 次 ✗）
  · 备战界面的「幻灵挑战」**标题在顶部** → 不得误点 ✓（旧排除规则想防的就是它）
  · 无「挑战」文本 → 走「已通关」判定 ✓

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_challenge_btn_verify.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

import fast_time                      # noqa: E402  虚拟时钟（本测试显式 install）
fast_time.CLOCK.install()

from afk_bot.auto_farm.formation import (  # noqa: E402
    _CHALLENGE_BTN_RY_MIN, FormationManager,
)

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f'[{"PASS" if cond else "FAIL"}] {name}'
          + (f'  {detail}' if detail else ''))


def _it(cy, cx, text, conf=0.98):
    return (cy, cx, text, conf, cx - 40, cx + 40)


# 真机实测帧（用户 2026-09-30 截图 OCR）
SETTLE = [_it(961.5, 875.5, '幻灵挑战', 0.99),      # 结算界面按钮（底部）
          _it(996.0, 874.0, '10090万', 0.98)]
READY = [_it(200, 640, '通关阵容', 0.98)]           # 备战界面
TITLE_BOT = [_it(120, 640, '幻灵挑战', 0.99)]       # 备战界面的「幻灵挑战」标题
PLAIN_BTN = [_it(900, 640, '挑战', 0.99)]           # 挂机关卡页按钮


class _Screen:
    def __init__(self, frames):
        self.frames = list(frames)
        self.hits = 0

    def ocr_screen(self, img=None, no_cache=False):
        f = self.frames[min(self.hits, len(self.frames) - 1)]
        self.hits += 1
        return f


class _Input:
    def __init__(self):
        self.clicked = []

    def click_at(self, x, y, label=''):
        self.clicked.append((x, y, label))
        return True


class _Det:
    def detect_phase(self):
        return 'result'


def _mk(frames):
    scr, inp = _Screen(frames), _Input()
    mgr = FormationManager(
        screen_capture=scr, recognizer=None, input_controller=inp,
        detector=_Det(), log_callback=lambda m: None, config={},
        stop_check=lambda: False, pause_check=lambda: True,
        battle_mode='manual',
    )
    mgr._should_stop = lambda: False
    return mgr, scr, inp


print('=' * 62)
print('结算界面「挑战」按钮判据验证（v2.2.19）')
print('=' * 62)

# ── 1. 【关键分叉】结算界面只有「幻灵挑战」→ 必须点到 ──
m1, s1, i1 = _mk([SETTLE] + [READY] * 30)
r1 = m1.click_challenge_btn('victory', 'huanling')
check('1a 【分叉】结算界面「幻灵挑战」被点到（旧判据排除「幻灵」→ 0 次 ✗）',
      any(c[2] == '挑战' for c in i1.clicked), str(i1.clicked[:2]))
check('1b 点击坐标 = 真机实测按钮位置 (875.5, 961.5)',
      any(abs(c[0] - 875.5) < 1 and abs(c[1] - 961.5) < 1
          for c in i1.clicked), str(i1.clicked[:2]))
check('1c 点击后进入备战界面 → 返回 True', r1 is True, f'r={r1!r}')

# ── 2. 挂机关卡页的普通「挑战」按钮仍优先 ──
m2, _s2, i2 = _mk([PLAIN_BTN] + [READY] * 30)
r2 = m2.click_challenge_btn('victory', 'huanling')
check('2a 普通「挑战」按钮正常点击', any(c[2] == '挑战' for c in i2.clicked),
      str(i2.clicked[:2]))
check('2b 返回 True', r2 is True, f'r={r2!r}')

# ── 3. 【防误点】备战界面的「幻灵挑战」标题（顶部）不得被点 ──
m3, _s3, i3 = _mk([TITLE_BOT] * 40)
r3 = m3.click_challenge_btn('victory', 'huanling')
check('3a 【防误点】顶部「幻灵挑战」标题不被点击（位置约束生效）',
      not any(c[2] == '挑战' for c in i3.clicked), str(i3.clicked[:2]))
check('3b 无按钮 → 判「已通关」(mode_completed)',
      r3 == 'mode_completed', f'r={r3!r}')
check('3c 顶部标题的 ry 确实低于底部阈值（判据自检）',
      120 / 1024.0 < _CHALLENGE_BTN_RY_MIN < 961.5 / 1024.0,
      f'ry_top={120/1024:.3f} ry_btn={961.5/1024:.3f} '
      f'阈值={_CHALLENGE_BTN_RY_MIN}')

print()
print(f'══ 结算界面挑战按钮：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)

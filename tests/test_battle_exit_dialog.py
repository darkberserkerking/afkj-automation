"""v1.8.56：battle._close_exit_dialog 轴修正专项。

背景：items=(cy,cx,text,conf,...)，修复前解包写成 `for cx, cy, ...`，
变量名与实际轴相反——筛选带 `0.55*h < cy < 0.7*h` 实际比对的是 x，
点击坐标 sx=l+x_btn_pos[0] 用的是 y（x/y 互换）。

用例构造 × 按钮位于图像坐标 (x=900, y=600)，h=1024：
  - 修复后：y=600 ∈ (0.55h, 0.7h)=(563,717) 命中 → 点击屏幕 (900,600)
  - 修复前：cy 收到 900（x）∉ (563,717) → 漏检走比例兜底（不同行为）

运行：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_battle_exit_dialog.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afk_bot.auto_farm.battle import BattleMonitor  # noqa: E402

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    tag: str = '✅' if cond else '❌'
    print(f'{tag} {name}' + (f' — {detail}' if detail else ''))
    (_PASS if cond else _FAIL).append(name)


class FakeScreen:
    """snap 返回窗口 (l=0,t=0,w=1280,h=1024)；OCR 结果按 (cy,cx,..) 构造。"""

    def __init__(self, items):
        self._items = items

    def activate_window(self):
        pass

    def snap(self):
        return (None, 0, 0, 1280, 1024)

    def _ocr_img(self, img):
        return self._items


class FakeDetector:
    def detect_phase(self):
        return 'idle'   # 首次点击后即判弹窗消失 → 单次循环


class FakeInput:
    def __init__(self):
        self.ratio_calls: list = []

    def click_ratio_dual(self, a, b, label=''):
        self.ratio_calls.append((a, b, label))

    def send_esc(self):
        pass


def make_battle(items):
    b = BattleMonitor.__new__(BattleMonitor)   # 跳过 __init__，只注入被测依赖
    b._screen = FakeScreen(items)
    b._detector = FakeDetector()
    b._input = FakeInput()
    b._log = lambda m: None
    return b


# ── 1. × 在 y 带内 → 按真实 (x,y) 点击 ────────────────────────
b1 = make_battle([
    (600, 900, '×', 0.9, 890, 910),
    (300, 640, '是否确认退出游戏？', 0.9, 500, 780),
])
with patch('afk_bot.auto_farm.battle.pyautogui') as pg, \
     patch('afk_bot.auto_farm.battle.time.sleep'):
    b1._close_exit_dialog()
clicks = list(pg.click.call_args_list)
check('1 ×按钮 (x=900,y=600) → 点击 (900,600)（修复前互换点 (600,900)）',
      bool(clicks) and clicks[0][0] == (900, 600),
      str([c[0] for c in clicks]))
check('2 未走比例兜底', not b1._input.ratio_calls,
      str(b1._input.ratio_calls))

# ── 2. 无 × 文本 → 走比例兜底 ────────────────────────────────
b2 = make_battle([(300, 640, '是否确认退出游戏？', 0.9, 500, 780)])
with patch('afk_bot.auto_farm.battle.pyautogui') as pg, \
     patch('afk_bot.auto_farm.battle.time.sleep'):
    b2._close_exit_dialog()
check('3 无 × → pyautogui 不点击', not pg.click.called)
check('4 无 × → 兜底 click_ratio_dual 被调',
      bool(b2._input.ratio_calls), str(b2._input.ratio_calls))

# ── 3. × 的 y 在带外 → 不误选（漏检走兜底，与旧筛选语义相反）──
b3 = make_battle([
    (300, 900, '×', 0.9, 890, 910),   # y=300 ∉ (563,717)
    (300, 640, '是否确认退出游戏？', 0.9, 500, 780),
])
with patch('afk_bot.auto_farm.battle.pyautogui') as pg, \
     patch('afk_bot.auto_farm.battle.time.sleep'):
    b3._close_exit_dialog()
check('5 ×y 带外 → 不点击该位置', not pg.click.called)
check('6 ×y 带外 → 走兜底', bool(b3._input.ratio_calls))

# ── 汇总 ────────────────────────────────────────────────────
print(f'══ _close_exit_dialog 专项：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
sys.exit(0 if not _FAIL else 1)

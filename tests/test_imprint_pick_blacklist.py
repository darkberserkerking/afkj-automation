# -*- coding: utf-8 -*-
"""烙印选卡：黑名单「同卡」判定窗口（v2.1.23，用户 17:11 真机）

真机现象（单核模式）：
    [17:09:28] [点击] 烙印选卡「稀有」 | 图像=(176,377)
    [17:09:33] [数据] 卡名识别「各尽其职」（稀有）
明明场上有**史诗**，却选了第一张稀有卡。

真机布局（用 `烙印四选一.png` 实测，1282x1024）：

    | 卡 | 卡名 y   | 稀有度标签 y |
    |----|---------|------------|
    | 1  | 293.5   | 376.5      |
    | 2  | 456.0   | 536.5      |
    | 3  | 616.0   | 698.5      |
    | 4  | 777.5   | 859.5      |

  → 行距 ≈162.5px；同卡「标签 = 卡名 + 83px」；
  → 于是**下一张卡的卡名**距**上一张卡的标签**只有 ≈79.5px。

根因：`_imprint_pick` 的黑名单排除用对称 `abs(Δy)<=150`，会把
「下一张黑名单卡的卡名」也当成同卡 → **把上一张卡的稀有度标签一起排除**。
若被排除的正是史诗卡 → 退而选中稀有卡（即真机现象）。
这与 `_card_quality` v2.1.11 修的是同一类错误，属当时漏改的尾巴。

修复：改为**方向性**窗口——标签位于黑名单卡名**下方 30~130px** 才算同卡
（与 `_card_quality` 取法互为逆运算）。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_imprint_pick_blacklist.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import (LabyrinthTask,  # noqa: E402
                             _BL_LABEL_DY_MIN, _BL_LABEL_DY_MAX)

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def it(cy, cx, text, conf=0.99):
    return (cy, cx, text, conf, cx - 50, cx + 50)


# ── 真机布局：卡名 y 起点 293.5、行距 162.5、同卡标签 = 卡名 + 83 ──
_ROW0 = 293.5
_PITCH = 162.5
_LABEL_DY = 83          # 同卡：标签在卡名下方 83px（实测）


def frame(cards):
    """cards=[(卡名, 稀有度)] → 真机四选一 items。"""
    items = []
    y = _ROW0
    for name, qual in cards:
        items.append(it(int(y), 366, name))               # 卡名
        items.append(it(int(y + 40), 500, '效果描述文字'))   # 描述
        items.append(it(int(y + _LABEL_DY), 176, qual))    # 稀有度标签
        y += _PITCH
    return items


class MockBot:
    """只需要 _ocr_screen（漏检重试用不到，本测试帧都含标签）。"""

    def __init__(self, frames=None):
        self._frames = list(frames or [])
        self.calls = []

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        return self._frames.pop(0) if self._frames else []

    def find_text(self, items, kw, min_conf=0.5):
        for t in items:
            if len(t) >= 4 and t[3] >= min_conf and kw in t[2]:
                return (t[1], t[0])
        return None

    def click_at(self, x, y, label=''):
        return True


def mk(bot):
    se, pe = threading.Event(), threading.Event()
    pe.set()
    return LabyrinthTask({
        'bot': bot, 'logger': lambda m: None, 'stats': {},
        'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#blpick',
    })


def label_y(i: int) -> int:
    """第 i 张卡（0 基）的稀有度标签 y —— 避免测试里硬编码错值。"""
    return int(_ROW0 + i * _PITCH) + _LABEL_DY


def black_ys(items):
    return [t[0] for t in items if t[2] in ('泰坦体魄', '献祭之盾')]


print('══ 烙印选卡：黑名单同卡窗口（v2.1.23）══')
print()

t0 = mk(MockBot())

# ── A. 复现真机：史诗卡被「下一张黑名单卡」误伤 ──
print('── A. 复现用户 17:11 真机场景 ──')
fA = frame([('各尽其职', '稀有'),      # 卡1 标签 y=376.5
            ('某史诗卡', '史诗'),      # 卡2 标签 y=539   ← 应选中它
            ('泰坦体魄', '史诗'),      # 卡3 黑名单卡名 y=618.5
            ('某稀有卡', '稀有')])     # 卡4 标签 y=864
_pickA = t0._imprint_pick(fA, blacklist_ys=black_ys(fA))
check('A1 【核心】应选中史诗（卡2, y=539），而不是最上方的稀有（y=376.5）',
      _pickA is not None and _pickA[2].strip() == '史诗'
      and int(_pickA[0]) == 539,
      f'pick={None if _pickA is None else (_pickA[2], _pickA[0])}')

# 复现旧实现：对称 ±150 会误伤
_old = [x for x in fA if x[2].strip() == '史诗'
        and all(abs(x[0] - by) > 150 for by in black_ys(fA))]
check('A2 【证伪】旧对称 ±150 确实会把史诗排除 → 退选稀有（复现真机）',
      len(_old) == 0,
      f'旧实现下的史诗候选={[(x[2], x[0]) for x in _old]} '
      f'黑名单y={black_ys(fA)}')
_old_pick = min(
    [x for x in fA if x[2].strip() in ('稀有', '精英')
     and all(abs(x[0] - by) > 150 for by in black_ys(fA))],
    key=lambda x: x[0], default=None)
check('A3 【证伪】旧实现退选到卡1 稀有 y=376.5 —— 与日志「图像=(176,377)」吻合',
      _old_pick is not None and int(_old_pick[0]) == 376,
      f'旧实现选中={None if _old_pick is None else (_old_pick[2], _old_pick[0])}')
print()

# ── B. 黑名单卡自身仍必须被排除（保护不能丢）──
print('── B. 黑名单卡自身仍被排除 ──')
fB = frame([('各尽其职', '稀有'),        # 卡1 稀有 376.5
            ('泰坦体魄', '史诗'),        # 卡2 黑名单（史诗）→ 必须排除
            ('某精英卡', '精英')])       # 卡3 精英 536.5
_pickB = t0._imprint_pick(fB, blacklist_ys=black_ys(fB))
check(f'B1 黑名单卡（史诗）被排除 → 选精英卡3（y={label_y(2)}）',
      _pickB is not None and _pickB[2].strip() == '精英'
      and int(_pickB[0]) == label_y(2),
      f'pick={None if _pickB is None else (_pickB[2], _pickB[0])}')
check('B2 未选中黑名单卡',
      _pickB is not None and int(_pickB[0]) != label_y(1),
      f'pick_y={None if _pickB is None else _pickB[0]}')

# 交换：黑名单在最上，下一张是史诗 → 史诗可用
fB2 = frame([('泰坦体魄', '史诗'),        # 卡1 黑名单 376.5
             ('某史诗卡', '史诗'),        # 卡2 史诗 539 ← 应选中
             ('某稀有卡', '稀有')])
_pickB2 = t0._imprint_pick(fB2, blacklist_ys=black_ys(fB2))
check('B3 黑名单在首行 → 下一行的史诗**不受牵连**（旧实现会误伤它）',
      _pickB2 is not None and _pickB2[2].strip() == '史诗'
      and int(_pickB2[0]) == 539,
      f'pick={None if _pickB2 is None else (_pickB2[2], _pickB2[0])}')
print()

# ── C. 无黑名单 → 纯优先级（史诗>精英>稀有），回归 ──
print('── C. 无黑名单：纯稀有度优先级 ──')
fC = frame([('a', '稀有'), ('b', '精英'), ('c', '史诗'), ('d', '稀有')])
_pickC = t0._imprint_pick(fC, blacklist_ys=[])
check(f'C1 有史诗 → 选史诗（卡3, y={label_y(2)}）',
      _pickC is not None and _pickC[2].strip() == '史诗'
      and int(_pickC[0]) == label_y(2),
      f'pick={None if _pickC is None else (_pickC[2], _pickC[0])}')

fC2 = frame([('a', '稀有'), ('b', '精英'), ('c', '稀有')])
_pickC2 = t0._imprint_pick(fC2, blacklist_ys=[])
check(f'C2 无史诗 → 选精英（卡2, y={label_y(1)}）',
      _pickC2 is not None and _pickC2[2].strip() == '精英'
      and int(_pickC2[0]) == label_y(1),
      f'pick={None if _pickC2 is None else (_pickC2[2], _pickC2[0])}')

fC3 = frame([('a', '稀有'), ('b', '稀有')])
_pickC3 = t0._imprint_pick(fC3, blacklist_ys=[])
check(f'C3 全稀有 → 取最上方（y={label_y(0)}）',
      _pickC3 is not None and int(_pickC3[0]) == label_y(0),
      f'pick={None if _pickC3 is None else (_pickC3[2], _pickC3[0])}')
print()

# ── D. 窗口参数自证：方向性 + 与 _card_quality 互为逆运算 ──
print('── D. 窗口参数 ──')
check(f'D1 窗口 = 卡名下方 {_BL_LABEL_DY_MIN}~{_BL_LABEL_DY_MAX}px',
      (_BL_LABEL_DY_MIN, _BL_LABEL_DY_MAX) == (30, 130),
      f'({_BL_LABEL_DY_MIN}, {_BL_LABEL_DY_MAX})')
check('D2 同卡偏移 83px 落在窗口内（能正确排除同卡）',
      _BL_LABEL_DY_MIN <= _LABEL_DY <= _BL_LABEL_DY_MAX,
      f'83 ∈ [{_BL_LABEL_DY_MIN},{_BL_LABEL_DY_MAX}]')
check('D3 下一张卡名的偏移 −79.5px 不在窗口内（不会误伤邻卡）',
      not (_BL_LABEL_DY_MIN <= -79.5 <= _BL_LABEL_DY_MAX),
      '-79.5 ∉ 窗口')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
            encoding='utf-8').read()
check('D4 旧的对称 ±150 判定已从 _imprint_pick 移除',
      'abs(it[0] - by) > 150' not in _src)
check('D5 候选诊断日志已加入（下次选错卡可直接定位根因）',
      '烙印候选 | 标签=' in _src)
print()

print('═' * 56)
print(f'烙印黑名单同卡窗口：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

# -*- coding: utf-8 -*-
"""烙印品质读取纠错（v2.1.11，用户 2026-09-11 00:04 真机两案例）

真机现象：
  · 23:43:59 具名「坚定意志」→ 记录品质「精英」，实际**史诗**
  · 23:48:58 具名「执裁之盾」→ 记录品质「精英」，实际「制裁之盾」**史诗**

根因：`_card_quality` 返回「第一个 |Δy|≤120 命中的标签」，而上一条相邻卡的
标签（Δy≈-100）仍在容差内 → 取到**邻卡**品质 → 「候选不如旧烙印（品质保护）」
误判 → 反复保留烙印空转。

本测试用真机坐标数据固化为断言。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _item(cy, cx, text, conf=0.9):
    """构造 OCR 项（y, x, text, conf, x1, x2）——与项目 items 结构一致。"""
    return (cy, cx, text, conf, cx - 50, cx + 50)


q = LabyrinthTask._card_quality

print('══ 烙印品质读取纠错（v2.1.11）══')
print()
print('── A. 真机案例 1：坚定意志（实际史诗）──')
frame_a = [
    _item(458, 176, '精英'),          # ← 上方相邻卡的标签（旧实现会命中它）
    _item(558, 365, '坚定意志'),      # 本卡卡名（真机 y=558）
    _item(640, 176, '史诗'),          # 本卡标签（卡名下方 +82）
]
_r = q(frame_a, (365, 558))
check('A1 取到本卡「史诗」（旧实现误取邻卡「精英」）', _r == '史诗', repr(_r))

print('── B. 真机案例 2：制裁之盾（实际史诗）──')
frame_b = [
    _item(638, 176, '精英'),          # 上方相邻卡标签
    _item(738, 351, '制裁之盾'),      # 本卡卡名（真机 y=738）
    _item(820, 176, '史诗'),          # 本卡标签（+82）
]
_r = q(frame_b, (351, 738))
check('B1 取到本卡「史诗」', _r == '史诗', repr(_r))

print('── C. 边界：下方无标签 → 退化取最近 ──')
frame_c = [
    _item(620, 176, '稀有'),          # 上方 +60（唯一标签）
    _item(560, 365, '某卡名'),
]
_r = q(frame_c, (365, 560))
check('C1 退化取最近（上方 60px 的「稀有」）', _r == '稀有', repr(_r))

print('── D. 边界：完全没有标签 → 未知（不得瞎猜）──')
frame_d = [_item(560, 365, '某卡名')]
_r = q(frame_d, (365, 560))
check('D1 无标签 → 未知', _r == '未知', repr(_r))

print('── E. 边界：下方 128px（略超 130 内）仍应取到 ──')
frame_e = [
    _item(440, 176, '精英'),          # 上方 -120（贴容差边界）
    _item(560, 365, '某卡名'),
    _item(680, 176, '史诗'),          # 下方 +120
]
_r = q(frame_e, (365, 560))
check('E1 下方 +120 优先于上方 -120', _r == '史诗', repr(_r))

print('── F. 回归：单卡帧（只有本卡标签）──')
frame_f = [_item(560, 365, '某卡名'), _item(642, 176, '精英')]
_r = q(frame_f, (365, 560))
check('F1 常规布局 → 正确取到「精英」', _r == '精英', repr(_r))

print('── G. 代码事实：具名路径已改为记录实际 OCR 文本 ──')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
            encoding='utf-8').read()
check('G1 具名分支记录 _actual（实读名）而非 kw',
      'self._record_imprint_gain(_actual, qual)' in _src)
check('G2 名称不一致时打警告（暴露字形误认）',
      '疑似字形误认，按实读记录' in _src)
check('G3 品质读取改为「优先卡名下方」',
      '正面 = 位于卡名下方' in _src or ' 30 <= delta <= 130' in _src)

print()
print('═' * 56)
print(f'品质读取纠错：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

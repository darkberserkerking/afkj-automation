# -*- coding: utf-8 -*-
"""烙印选择界面被误判成胜利弹层（v2.2.1 真机修复）

## 报障（用户 2026-09-12 21:25）
真机日志 14:31:18~14:32:39：烙印选择界面被判成 `victory(战斗胜利弹层)`
→ ESC/返回键交替空转 18 轮 → `脱险动作无效，玩法循环终止`
→ 任务失败（耗时 **5444 秒**）。

## 根因（证据链闭环）
烙印卡「加速叠伤」真实文案：
    「我方英雄每次使用普通攻击时，这名英雄**直至战斗结束**增加10点攻击速度…」
→ 子串 `「战斗结束」` 撞 `labyrinth.victory` 的宽泛判据；而 victory(102)
   **先于** imprint(105) 评估 → 烙印界面被吃成胜利弹层。

实证（同一张真机截图喂检测器，仅 OCR 结果不同）：
    OCR 丢字（读成「直战结束」） → labyrinth.imprint  ✅
    OCR 读全（含「直至战斗结束」）→ labyrinth.victory  ❌ ← bug

与 v1.8.57「商店暖流遗物描述含「战斗胜利」撞 victory」属**同一类**；
按项目既有约定：**判据优先级不动，victory 加反向排除词表**。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_imprint_victory_misjudge.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
fast_time.install()

import tasks.catalog  # noqa: F401,E402
from tasks.screen_registry import (  # noqa: E402
    GLOBAL_SCREEN_REGISTRY as REG, IMPRINT_MASK_WORDS,
)

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def it(cy, cx, text, conf=0.99):
    return (cy, cx, text, conf)


# ── 真机实测帧（来自用户截图 MaaOCR 实测，尺寸 1278x1056）──────────
# 坐标原样保留，便于日后核对；这里只用文本参与判据
_REAL_SHOT = [
    it(17.8, 60.8, 'AFKJourey', 0.85), it(44.0, 277.0, '±100.0%', 0.96),
    it(47.5, 641.0, '难度15'), it(55.5, 1074.0, '103'),
    it(58.5, 231.5, '1098万', 0.94), it(64.0, 641.0, '10/15'),
    it(106.0, 162.0, '中', 0.95), it(144.5, 239.0, '3/4 2/4 2/4 6/8', 0.93),
    it(341.5, 1101.5, '25', 0.98), it(413.0, 365.5, '加速叠伤', 0.94),
    it(452.0, 695.5, '我方英雄每次使普通攻击时，这名英雄直战结束增加10点'
                     '攻击速度，多增加80点攻击', 0.96),
    it(479.5, 339.5, '速度。'), it(509.0, 178.5, '史诗'),
    it(592.5, 354.5, '藏品柜'), it(630.5, 535.0,
                                  '我方每拥有一个烙印，增加10%命，至多增加100%。', 0.97),
    it(689.5, 177.5, '精英'), it(773.0, 367.0, '溃烂代价'),
    it(811.5, 699.0, '战的第3秒，我所有英雄以失去10%的当前命值为代价，'
                     '使敌所有英雄失去35%当前', 0.97),
    it(838.5, 599.0, '命值的命。上阵数不满且敌为领时，效果根据上阵数折损。', 0.98),
    it(869.0, 178.0, '精英'), it(987.5, 639.5, '选择烙印'),
]

_CARD1_TRUE = ('我方英雄每次使用普通攻击时，这名英雄直至战斗结束'
               '增加10点攻击速度，至多增加80点攻击速度。')


def _with_card1(text):
    """把实测帧里的卡1描述替换为指定文案（模拟 OCR 读全/读部分）。"""
    out = []
    for cy, cx, t, sc in _REAL_SHOT:
        if '直战结束' in t or '这名英雄直' in t:
            t = text
        out.append(it(cy, cx, t, sc))
    return out


print('══ 烙印界面 vs 胜利弹层 误判（v2.2.1）══')
print()

# ── A. 真机实测：两种 OCR 结果都必须判烙印 ──
print('── A. 真机截图（两种 OCR 结果）──')
check('A1 实测帧（OCR 丢字「直战结束」）→ 烙印选择',
      REG.detect(_REAL_SHOT) == 'labyrinth.imprint',
      REG.detect(_REAL_SHOT))
check('A2 【分叉】实测帧 + 卡1 读全（含「直至战斗结束」）→ 烙印选择',
      REG.detect(_with_card1(_CARD1_TRUE)) == 'labyrinth.imprint',
      f"{REG.detect(_with_card1(_CARD1_TRUE))} ← 修复前为 "
      f"labyrinth.victory（已实证，任务失败 5444s）")

# ── B. 同类撞词句式（防其它烙印卡复现）──
print()
print('── B. 同类撞词句式 ──')
for variant in ('直到战斗结束', '战斗结束时', '战斗结束后',
                '战斗结束前'):
    frame = _with_card1(f'某烙印效果：{variant}增加10%攻击。')
    check(f'B 卡描述含「{variant}」→ 仍判烙印选择',
          REG.detect(frame) == 'labyrinth.imprint', REG.detect(frame))

# ── C. 只读到主按钮（卡文案全没读到）──
print()
print('── C. 退化场景 ──')
check('C1 只读到主按钮「选择烙印」→ 烙印选择',
      REG.detect([it(987.5, 639.5, '选择烙印')]) == 'labyrinth.imprint')
check('C2 主按钮+标题（无描述）→ 烙印选择',
      REG.detect([it(987.5, 639.5, '选择烙印'), it(413.0, 365.5, '加速叠伤')])
      == 'labyrinth.imprint')

# ── D. 【反向保护】真胜利弹层不能被误伤 ──
print()
print('── D. 反向保护：真胜利弹层仍须判 victory ──')
check('D1 「战斗胜利」弹层 → victory',
      REG.detect([it(400, 640, '战斗胜利')]) == 'labyrinth.victory',
      REG.detect([it(400, 640, '战斗胜利')]))
check('D2 「挑战成功」弹层 → victory',
      REG.detect([it(400, 640, '挑战成功')]) == 'labyrinth.victory')
check('D3 标题就是「战斗结束」的弹层 → victory（排除词不得误伤）',
      REG.detect([it(400, 640, '战斗结束')]) == 'labyrinth.victory',
      REG.detect([it(400, 640, '战斗结束')]))
check('D4 战斗失败弹层 → failure（不受影响）',
      REG.detect([it(400, 640, '战斗失败')]) == 'labyrinth.failure')
check('D5 女神结算页（战斗胜利+再次挑战）→ goddess.victory_result',
      REG.detect([it(400, 640, '战斗胜利'), it(700, 640, '再次挑战')])
      == 'goddess.victory_result')
check('D6 商店面板 → labyrinth.shop（v1.8.57 既有修正未被破坏）',
      REG.detect([it(300, 640, '商品列表')]) == 'labyrinth.shop')

# ── E. 静态：判据确实用了排除词表 ──
print()
print('── E. 静态检查 ──')
_src = open(os.path.join(ROOT, 'tasks', 'screen_registry.py'),
            encoding='utf-8').read()
check('E1 定义 IMPRINT_MASK_WORDS 且含两个词',
      'IMPRINT_MASK_WORDS: Tuple[str, ...] = (' in _src
      and '选择烙印' in IMPRINT_MASK_WORDS
      and '直至战斗结束' in IMPRINT_MASK_WORDS, str(IMPRINT_MASK_WORDS))
_vic = _src.split("'labyrinth.victory'")[1].split('reg.register')[0]
check('E2 victory 判据已加烙印词排除',
      'IMPRINT_MASK_WORDS' in _vic, '')
check('E3 优先级未改动（victory 仍 102 / imprint 仍 105）',
      'priority=102' in _vic, '按项目约定不动优先级')
check('E4 victory 原有排除（door/shop/再次挑战）仍在',
      'DOOR_WORDS' in _vic and 'SHOP_MASK_WORDS' in _vic
      and "h('再次挑战')" in _vic)

print()
print('═' * 56)
print(f'烙印误判修复：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

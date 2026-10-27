# -*- coding: utf-8 -*-
"""菲茨小店购买流程（v2.1.12，用户 2026-09-11 00:29 真机）

真机现象：进入 shop 后仅 2 秒就「商店流程结束」——店里明明有目标商品，
却一件未买。根因：`_shop_flow` 首帧判据 `if any('继续前进' ...): break`
（注释写"已退出商店"），而**商店界面底部本身就有「继续前进」** → 首帧即退出。

本测试固化修复后的正确行为：有目标商品就必须买。
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _it(cy, cx, text, conf=0.9):
    return (cy, cx, text, conf, cx - 50, cx + 50)


class MockBot:
    def __init__(self, texts_by_call=None):
        self._t = list(texts_by_call or [])
        self.calls = []
        self.clicked = []

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._t:
            return self._t.pop(0)
        return [_it(900, 640, '继续前进')]

    def click_at(self, x, y, label=''):
        self.clicked.append(label)
        return True

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def window_rect(self):
        return (0, 0, 1280, 1024)

    def press_esc(self, label=''):
        self.clicked.append('ESC')
        return True

    def scroll_wheel(self, *a, **kw):
        return True

    def _click_abs(self, x, y):
        self.clicked.append(f'_click_abs:({x},{y})')
        return True

    def template_match(self, *a, **kw):
        return None

    def click_ratio(self, *a, **kw):
        return True

    def long_press_at(self, *a, **kw):
        return True


def _mk(bot, logger=None):
    se, pe = threading.Event(), threading.Event()
    pe.set()
    return LabyrinthTask({
        'bot': bot, 'logger': logger or (lambda m: None), 'stats': {},
        'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#shop',
    })


ESSENCE_OK = _it(30, 1100, '500')      # 右上角精粹（x>0.78W, y<0.08H）
ESSENCE_LOW = _it(30, 1100, '10')      # 不足 50

print('══ 菲茨小店购买流程（v2.1.12）══')
print()
print('── A. 【核心】店内有目标商品 + 底部有「继续前进」→ 必须购买 ──')
shop1 = [_it(300, 400, '商品列表'), _it(400, 500, '菲茨小店'),
         _it(600, 640, '庇护'), _it(950, 640, '继续前进'), ESSENCE_OK]
buy_btn = [_it(700, 640, '购买')]
shop2 = [_it(300, 400, '商品列表'), _it(400, 500, '菲茨小店'),
         _it(950, 640, '继续前进'), ESSENCE_OK]
logs_a = []
ba = MockBot(texts_by_call=[shop1, buy_btn, shop2, shop2])
ta = _mk(ba, logger=lambda m: logs_a.append(m))
ta._shop_flow()
check('A1 【回归守卫】点了目标商品「庇护」',
      '商店_庇护' in ba.clicked, str(ba.clicked))
check('A2 【回归守卫】点了「购买」（真机此前一件未买）',
      '商店_购买' in ba.clicked, str(ba.clicked))
check('A3 日志显示已买 1 件',
      any('已买 1 件' in m for m in logs_a),
      str([m for m in logs_a if '已买' in m][:1]))
check('A4 收尾仍点「继续前进」退出',
      '继续前进' in ba.clicked, str(ba.clicked[-2:]))
print()

print('── B. 精粹不足 50 → 不买，直接退出 ──')
shop_low = [_it(300, 400, '商品列表'), _it(600, 640, '庇护'),
            _it(950, 640, '继续前进'), ESSENCE_LOW]
logs_b = []
bb = MockBot(texts_by_call=[shop_low] * 4)
tb = _mk(bb, logger=lambda m: logs_b.append(m))
tb._shop_flow()
check('B1 未点商品', '商店_庇护' not in bb.clicked, str(bb.clicked))
check('B2 日志「已买 0 件」+ 精粹 10',
      any('已买 0 件' in m and '10' in m for m in logs_b),
      str([m for m in logs_b if '已买' in m][:1]))
check('B3 正常退出（点继续前进）', '继续前进' in bb.clicked, str(bb.clicked))
print()

print('── C. 店内无目标商品 → 直接退出（不空转）──')
shop_none = [_it(300, 400, '商品列表'), _it(950, 640, '继续前进'),
             ESSENCE_OK]
logs_c = []
bc = MockBot(texts_by_call=[shop_none] * 4)
tc = _mk(bc, logger=lambda m: logs_c.append(m))
tc._shop_flow()
check('C1 未点任何商品',
      not any(c.startswith('商店_') for c in bc.clicked), str(bc.clicked))
check('C2 只取 1 帧 OCR 即退出（不空转）',
      len([c for c in bc.calls if c == '_ocr_screen']) == 1,
      f'ocr={len([c for c in bc.calls if c == "_ocr_screen"])}')
print()

print('── D. 购满 2 件后退出（不超买）──')
shop_d = [_it(300, 400, '商品列表'), _it(600, 640, '庇护'),
          _it(950, 640, '继续前进'), ESSENCE_OK]
logs_d = []
bd = MockBot(texts_by_call=[shop_d, buy_btn, shop_d, buy_btn, shop_d,
                            shop_d, shop_d])
td = _mk(bd, logger=lambda m: logs_d.append(m))
td._shop_flow()
check('D1 恰好购买 2 件',
      len([c for c in bd.clicked if c == '商店_购买']) == 2,
      str([c for c in bd.clicked if c == '商店_购买']))
check('D2 日志「已购满 2 件」',
      any('已购满 2 件' in m for m in logs_d),
      str([m for m in logs_d if '购满' in m][:1]))
print()

print('── E. 选门优先级：普通遗物门已降到菲茨小店之下 ──')
te = _mk(MockBot())
_door_desc_plain = [_it(300, 500, '遗物之门'), _it(400, 500, '净化遗物，获得普通强化')]
_r_plain = te._door_score('遗物之门', _door_desc_plain, 500, 300)
_r_strong = te._door_score(
    '遗物之门', [_it(400, 500, '获得强壮遗物')], 500, 300)
_r_shop = te._door_score(
    '菲茨小店', [_it(400, 500, '神秘商人菲茨经营的小店')], 500, 300)
check('E1 普通遗物门 = 5.5（低于小店 5）', _r_plain[0] == 5.5, str(_r_plain))
check('E2 菲茨小店 = 5（不变）', _r_shop[0] == 5, str(_r_shop))
check('E3 三目标遗物门仍为 0（强壮）', _r_strong[0] == 0, str(_r_strong))
check('E4 普通遗物门排序低于小店',
      _r_shop[0] < _r_plain[0], f'小店={_r_shop[0]} 普通遗物门={_r_plain[0]}')

print()
print('═' * 56)
print(f'菲茨小店流程：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

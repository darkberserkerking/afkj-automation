# -*- coding: utf-8 -*-
"""烙印选卡「选中态校验与重试」回归守卫（v2.1.13，用户 2026-09-11 真机）

真机现象（03:55:34 与 03:57:28 等，共 5 处 16 次）：
  转场刚结束就点选卡 → 点击被吞（卡未选中、底部按钮仍为「选择烙印」）
  → 旧逻辑把该按钮当"确认钮"点击并**记录烙印**（假成功）
  → 下一轮界面稳定后点击生效 → **同一张卡被记两次甚至多次**
  （沸腾怒火×4 / 强壮之力×3 / 弱点洞悉×2 / 炽热守护×5）
  → 极端情况连续被吞 → 空转 18 轮 → 任务失败

修复（v2.1.13）：
  · `_imprint_select_and_confirm`：点选卡 → 校验按钮是否变「确认」；
    未变（仍是「选择烙印」）→ **换备用坐标重试一次**；两次都未选中 →
    **返回 False，调用方不记录烙印**（交主循环下一帧重试）
  · `_imprint_card_click_pos`：备用坐标（优先同卡名称位置，退化 0.45W）
  · `_handle_imprint` 入口：距上次处理 >8s 视为刚进入界面 → 就绪等待 0.9s
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
        return []

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

    def template_match(self, *a, **kw):
        return None

    def _click_abs(self, x, y):
        return True

    def press_esc(self, label=''):
        self.clicked.append('ESC')
        return True

    def scroll_wheel(self, *a, **kw):
        return True

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
        'inst_id': 'labyrinth#sel',
    })


# 帧：未选中态（底部按钮 = 「选择烙印」）
UNSEL = [_it(300, 176, '史诗'), _it(400, 365, '弱点洞悉'),
         _it(950, 640, '选择烙印')]
# 帧：已选中态（底部按钮 = 「确认」）
SEL = [_it(300, 176, '史诗'), _it(400, 365, '弱点洞悉'),
       _it(950, 640, '确认')]

print('══ 烙印选卡选中态校验（v2.1.13）══')
print()
print('── A. 【核心】首次点击被吞 → 必须重试，且只记录一次 ──')
logs_a = []
ba = MockBot(texts_by_call=[UNSEL, SEL, SEL])
ta = _mk(ba, logger=lambda m: logs_a.append(m))
_u = [(365, 176, '史诗', 0.9, 126, 226)]          # pick 形态 (cy,cx,text,...)
ta._imprint_select_and_confirm(_u[:1] + [_it(400, 365, '弱点洞悉')], 
                               (176, 300, '史诗'))
check('A1 首次点击后校验失败（打了「选中态校验失败」日志）',
      any('选中态校验失败' in m for m in logs_a), str(logs_a[:2]))
check('A2 触发了备用坐标重试（烙印_选卡_重试）',
      '烙印_选卡_重试' in ba.clicked, str(ba.clicked))
check('A3 重试后点中「确认」（选中态）',
      '烙印_确认' in ba.clicked, str(ba.clicked))
print()

print('── B. 【核心】两次都没被吞生效 → 不得记录烙印 ──')
logs_b = []
bb = MockBot(texts_by_call=[UNSEL, UNSEL, UNSEL])
tb = _mk(bb, logger=lambda m: logs_b.append(m))
_ok_b, _ = tb._imprint_select_and_confirm(
    [_it(300, 176, '史诗'), _it(400, 365, '弱点洞悉')], (176, 300, '史诗'))
check('B1 返回 False（未确认成功）', _ok_b is False, str(_ok_b))
check('B2 未点「确认」', '烙印_确认' not in bb.clicked, str(bb.clicked))
check('B3 日志提示「本次不记录」',
      any('不记录' in m for m in logs_b), str([m for m in logs_b if '不记录' in m][:1]))
print()

print('── C. 回归：首次即生效 → 正常确认 ──')
logs_c = []
bc = MockBot(texts_by_call=[SEL, SEL])
tc = _mk(bc, logger=lambda m: logs_c.append(m))
_ok_c, _ = tc._imprint_select_and_confirm(
    [_it(300, 176, '史诗'), _it(400, 365, '弱点洞悉')], (176, 300, '史诗'))
check('C1 返回 True', _ok_c is True, str(_ok_c))
check('C2 直接点「确认」、无重试',
      '烙印_确认' in bc.clicked and '烙印_选卡_重试' not in bc.clicked,
      str(bc.clicked))
print()

print('── D. 备用坐标：优先同卡名称位置，退化 0.45W ──')
# 真实布局：品质标签在**名称下方** ~82px（见 _collect_imprint_entries 注释）
# 故名称 y=220、标签 y=300（差 80，落在 40~130 窗口内）
td = _mk(MockBot())
_frame_real = [_it(220, 365, '弱点洞悉'), _it(300, 176, '史诗')]
_p1 = td._imprint_card_click_pos(_frame_real, (300, 176, '史诗'))
check('D1 取到同卡名称位置 (365, 220)', _p1 == (365, 220), str(_p1))
_p2 = td._imprint_card_click_pos([_it(300, 176, '史诗')], (300, 176, '史诗'))
check('D2 无名称 → 退化 0.45W（与标签同 y）',
      _p2 == (int(0.45 * 1280), 300), str(_p2))
print()

print('── E. 就绪等待：首次进入 imprint 时等待，连续调用不重复等 ──')
logs_e = []
be = MockBot(texts_by_call=[SEL, SEL, SEL])
te = _mk(be, logger=lambda m: logs_e.append(m))
te._last_imprint_at = 0.0
te._handle_imprint([_it(300, 176, '史诗'), _it(400, 365, '弱点洞悉')])
_n1 = sum(1 for m in logs_e if '就绪等待' in m)
te._handle_imprint([_it(300, 176, '史诗'), _it(400, 365, '弱点洞悉')])
_n2 = sum(1 for m in logs_e if '就绪等待' in m)
check('E1 首次进入 → 打一次「就绪等待」', _n1 == 1, f'n={_n1}')
check('E2 紧接着再次调用 → 不再等待（8s 内）', _n2 == 1, f'n={_n2}')
print()

print('── F. 代码事实 ──')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
            encoding='utf-8').read()
check('F1 新增 _imprint_select_and_confirm',
      'def _imprint_select_and_confirm' in _src)
check('F2 新增备用坐标方法 _imprint_card_click_pos',
      'def _imprint_card_click_pos' in _src)
check('F3 就绪等待常量存在', '_IMPRINT_READY_WAIT' in _src)
check('F4 普通路径已改用校验方法（旧的无校验点击已移除）',
      "self.bot.click_at(int(pick[1]), int(pick[0]), '烙印_选卡')" not in _src)

print()
print('═' * 56)
print(f'选卡校验：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

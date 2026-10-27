# -*- coding: utf-8 -*-
"""未开战收尾修复（v2.1.14，用户 2026-09-11 真机 16 分钟空转）

真机现象（11:42~11:58，30 轮）：
  角色全部阵亡 → 按 F 无法开战 → 判定"未开战"（正确）
  → 走收尾：press_esc 退出备战 + 结束探索
  → press_esc 用 SendMessage（Unity 不响应）→ 退不出去
  → 备战页没有「结束探索」按钮 → _end_exploration 返回 ''（失败）
  → **失败被吞**（调用处只认 'continue'，其余一律 return 'done'）
  → run() 当成本轮完成 → 开新轮 → 画面仍在备战页 → 又按 F …… ×30

修复（v2.1.14）：
  1. `press_esc` 改为委托 `send_esc`（keybd_event 真实按键，Unity 有效）
  2. `_leave_battle_prep()`：ESC → **校验是否真的离开** → 未离开则点左下角
     「◀」返回按钮（真机实测有效）→ 再校验
  3. `_abort_round()`：带回执（'done'/'continue'/''），失败不再被当成功
  4. 跨轮失败计数 `_no_battle_rounds`（不在 _run_once 重置）→ 连续 2 轮失败
     → 停止任务（'stopped'）并提示调整阵容
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
        return [_it(300, 640, '战斗'), _it(31, 641, '17/20')]   # 默认仍是备战页

    def press_esc(self, label=''):
        self.clicked.append(f'ESC:{label}')
        return None

    def click_ratio(self, rx, ry, label=''):
        self.clicked.append(f'click_ratio:{label}@{rx},{ry}')
        return True

    def click_at(self, x, y, label=''):
        self.clicked.append(f'click_at:{label}')
        return True

    def _click_abs(self, x, y):
        return True

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def template_match(self, *a, **kw):
        return None

    def window_rect(self):
        return (0, 0, 1280, 1024)

    def scroll_wheel(self, *a, **kw):
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
        'inst_id': 'labyrinth#abort',
    })


# 备战页帧（含「难度」+「战斗」+ 顶部层数 —— 与真机备战页判据一致）
PREP = [_it(31, 641, '难度15'), _it(760, 490, '战斗'),
        _it(31, 700, '17/20'), _it(200, 300, '精兵来袭')]
# 选门地图帧（已离开备战页）
DOOR = [_it(700, 640, '结束探索'), _it(374, 517, '遗物之门')]

print('══ 未开战收尾修复（v2.1.14）══')
print()
print('── A. ESC 无效 → 返回按钮兜底 → 确认离开 ──')
logs_a = []
# v2.1.14 加固后：ESC 后若判备战会**二次 OCR 确认**，两帧都备战才点返回按钮
ba = MockBot(texts_by_call=[PREP, PREP, DOOR, DOOR])
ta = _mk(ba, logger=lambda m: logs_a.append(m))
ok_a = ta._leave_battle_prep()
check('A1 返回 True（已离开备战页）', ok_a is True, str(ok_a))
check('A2 先发了 ESC', any(c.startswith('ESC:迷宫_退出备战') for c in ba.clicked),
      str(ba.clicked[:2]))
check('A3 ESC 无效 → 点了左下角返回按钮',
      any('返回按钮' in c for c in ba.clicked), str(ba.clicked))
check('A4 比例坐标正确 (0.139, 0.825)',
      any('@0.139,0.825' in c for c in ba.clicked), str(ba.clicked))
print()

print('── B. ESC 生效 → 不点返回按钮（回归）──')
logs_b = []
bb = MockBot(texts_by_call=[DOOR])
tb = _mk(bb, logger=lambda m: logs_b.append(m))
ok_b = tb._leave_battle_prep()
check('B1 返回 True', ok_b is True, str(ok_b))
check('B2 未点返回按钮（ESC 已够）',
      not any('返回按钮' in c for c in bb.clicked), str(bb.clicked))
print()

print('── C. 两种方式都无效 → 返回 False（关键：不再假装成功）──')
logs_c = []
bc = MockBot(texts_by_call=[PREP, PREP, PREP, PREP])
tc = _mk(bc, logger=lambda m: logs_c.append(m))
ok_c = tc._leave_battle_prep()
check('C1 返回 False', ok_c is False, str(ok_c))
check('C2 日志明示未能离开',
      any('均未能离开备战界面' in m for m in logs_c),
      str([m for m in logs_c if '均未能' in m][:1]))
print()

print('── D. _abort_round 回执语义 ──')
# D1: 退出失败 → ''
logs_d1 = []
bd = MockBot(texts_by_call=[PREP, PREP, PREP, PREP])
td = _mk(bd, logger=lambda m: logs_d1.append(m))
_r1 = td._abort_round()
check("D1 退不出备战 → 返回 ''（不是 done）", _r1 == '', repr(_r1))
# D2: 退出成功 + 结束探索成功 → 'done'
logs_d2 = []
bd2 = MockBot(texts_by_call=[DOOR, DOOR, DOOR])
td2 = _mk(bd2, logger=lambda m: logs_d2.append(m))
check("D2 退出成功+结束探索成功 → 'done'",
      td2._abort_round() == 'done', '')
# D3: 退出成功 + 结束探索失败（画面无「结束探索」）→ ''
logs_d3 = []
bd3 = MockBot(texts_by_call=[DOOR] + [[_it(760, 490, '战斗')]] * 6)
td3 = _mk(bd3, logger=lambda m: logs_d3.append(m))
check("D3 结束探索失败 → 返回 ''（不再吞成 done）",
      td3._abort_round() == '', '')
print()

print('── E. 跨轮失败计数（防空转 30 轮）──')
te = _mk(MockBot())
check('E1 `_no_battle_rounds` 由 run() 初始化（跨轮累计）',
      'self._no_battle_rounds: int = 0' in open(
          os.path.join(ROOT, 'tasks', 'labyrinth.py'),
          encoding='utf-8').read())
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'), encoding='utf-8').read()
check('E2 连续 2 轮失败 → return stopped（停止任务）',
      '连续 2 轮均无法退出备战' in _src and "return 'stopped'" in _src)
check('E3 失败分支不再直接 return done（有计数前置）',
      '_no_battle_rounds += 1' in _src)
print()

print('── F. press_esc 已改为真实按键（根因修复）──')
_inp = open(os.path.join(ROOT, 'afk_bot', 'infra', 'input.py'),
            encoding='utf-8').read()
check('F1 press_esc 委托 send_esc（keybd_event）',
      'self.send_esc()' in _inp)
check('F2 旧 SendMessage 实现降级为 press_esc_background（不再被主流程使用）',
      'def press_esc_background' in _inp)
check('F3 send_esc 仍是 keybd_event + 激活窗口',
      'keybd_event(0x1B' in _inp and 'activate_window' in _inp)

print()
print('═' * 56)
print(f'未开战收尾修复：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

# -*- coding: utf-8 -*-
"""结束探索 → 烙印库存镜像清空（v2.1.21，用户定案）

背景：`_orb_inventory` 是**本局探索**的烙印镜像（方案 1「获得即入库」，
见 `_record_imprint_gain`）。探索一结束，游戏侧烙印列表即作废；镜像若残留，
后续「替换目标」会拿过期清单判定：
  · v1.9.34 真机教训：第二轮必败 / 占位名追加在过时清单上
  · v2.1.7  真机教训：跨轮残留（第 2 轮第 1 枚被记成「库存共 11 枚」）

本次修复：把重置收敛为 `_reset_orb_mirror()`，两处调用
  ① `run()` 每轮入口（覆盖 final_settle 等不经「结束探索」的收尾）
  ② `_end_exploration()` 成功点掉「结束探索」时（与探索同生命周期，
     覆盖 boss_cleared / failure / _abort_round 全部收尾路径）
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_KEYS = ('_orb_scanned', '_orb_inventory', '_orb_downs',
         '_orb_epic_count', '_settle_logged')

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
        return [_it(500, 640, '玩法目录')]      # 中性帧：不触发任何状态

    def find_text(self, items, kw, min_conf=0.5):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, x, y, label=''):
        self.clicked.append(label)

    def click_ratio(self, rx, ry, label=''):
        self.clicked.append(label)

    def press_esc(self, label=''):
        self.clicked.append(f'esc:{label}')

    def press_f(self, label=''):
        self.clicked.append(f'f:{label}')

    def long_press_at(self, *a, **kw):
        return True

    def long_press_ratio(self, *a, **kw):
        return True

    def scroll_wheel(self, *a, **kw):
        return True

    def template_match(self, *a, **kw):
        return None

    def window_rect(self):
        return (0, 0, 1280, 1024)


def _mk(bot, logger=None):
    se, pe = threading.Event(), threading.Event()
    pe.set()
    return LabyrinthTask({
        'bot': bot, 'logger': logger or (lambda m: None), 'stats': {},
        'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#orbreset',
    })


def _dirty(t) -> bool:
    return any(hasattr(t, k) for k in _KEYS)


def _seed(t):
    """造一份"本局已累积"的镜像。"""
    t._orb_scanned = True
    t._orb_inventory = [('执裁之盾', '史诗'), ('溢出防御', '史诗')]
    t._orb_downs = 3
    t._orb_epic_count = 2
    t._settle_logged = True


print('══ 结束探索 → 烙印库存镜像清空（v2.1.21）══')
print()

# ── A. _reset_orb_mirror 本体 ────────────────────────────────
print('── A. _reset_orb_mirror 本体语义 ──')
_ta = _mk(MockBot())
_seed(_ta)
_r1 = _ta._reset_orb_mirror()
check('A1 清掉全部 5 个镜像属性', not _dirty(_ta), str([k for k in _KEYS if hasattr(_ta, k)]))
check('A2 返回 True（确实清掉了内容）', _r1 is True, repr(_r1))
_r2 = _ta._reset_orb_mirror()
check('A3 已空再清 → 返回 False（调用方可据此免去重复日志）', _r2 is False, repr(_r2))
check('A4 方法存在且可被静态引用',
      'def _reset_orb_mirror' in open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
                                     encoding='utf-8').read())
print()

# ── B. 结束探索路径会清镜像 ──────────────────────────────────
print('── B. _end_exploration 行为 ──')
DE = [_it(700, 640, '结束探索')]
logs_b = []
tb = _mk(MockBot(texts_by_call=[DE, DE, DE, DE]),
         logger=lambda m: logs_b.append(m))
_seed(tb)
_rb = tb._end_exploration(list(DE))
check("B1 点「结束探索」→ 返回 done", _rb == 'done', repr(_rb))
check('B2 镜像已清空', not _dirty(tb), str([k for k in _KEYS if hasattr(tb, k)]))
check('B3 有明确日志（探索结束 → 镜像清空）',
      any('烙印库存镜像已清空' in m for m in logs_b),
      str([m for m in logs_b if '镜像' in m][:1]))
print()

print('── C. 深层「继续探索」不清镜像（探索仍在继续）──')
GO = [_it(700, 640, '继续探索')]
logs_c = []
tc = _mk(MockBot(texts_by_call=[GO, GO, GO, GO]),
         logger=lambda m: logs_c.append(m))
tc._deep_mode = lambda: True          # 模式2/3：15 层通关点「继续探索」进深层
_seed(tc)
_rc = tc._end_exploration(list(GO))
check("C1 深层模式 → 返回 'continue'", _rc == 'continue', repr(_rc))
check('C2 镜像**未被清**（同一局探索未结束，库存仍然有效）',
      _dirty(tc) and tc._orb_inventory == [('执裁之盾', '史诗'), ('溢出防御', '史诗')],
      str(getattr(tc, '_orb_inventory', None)))
print()

# ── D. 静态：#两个调用点都在 ────────────────────────────────
print('── D. 静态检查调用点 ──')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'), encoding='utf-8').read()
check('D1 run() 每轮入口调用 _reset_orb_mirror()',
      'self._reset_orb_mirror()' in _src)
check('D2 旧内联 delattr 循环已收敛（不再散落）',
      "for _k in ('_orb_scanned', '_orb_inventory', '_orb_downs'," in _src
      and _src.count("delattr(self, _k)") == 1,
      f"delattr 次数={_src.count('delattr(self, _k)')}")
check('D3 _end_exploration 在 return done 之前清镜像',
      'if self._reset_orb_mirror():' in _src)
print()

print('── E. 正常通关：探索完成结算页（不经 _end_exploration 的路径）──')
FS = [_it(300, 640, '探索完成')]       # FINAL_SETTLE_MARKS 命中
logs_e = []
te = _mk(MockBot(texts_by_call=[FS, FS, FS]),
         logger=lambda m: logs_e.append(m))
_seed(te)
_re = te._play_loop()
check("E1 探索完成结算页 → 本轮 done", _re == 'done', repr(_re))
check('E2 镜像已清空（正常通关同样重置）',
      not _dirty(te), str([k for k in _KEYS if hasattr(te, k)]))
check('E3 有明确日志',
      any('烙印库存镜像已清空' in m for m in logs_e),
      str([m for m in logs_e if '镜像' in m][:1]))
print()

print('═' * 56)
print(f'烙印镜像重置：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

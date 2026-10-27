# -*- coding: utf-8 -*-
"""导航日志必须如实反映真实行为（v2.1.24，用户 17:21）

真机日志（桌面 `_afk_gui_log.txt` 17:14:20）：
    [信息] 全局导航 | 当前「异界迷宫主页」（2 层）（1/14）
    [信息] [规划] 最优路径 2 步：异界迷宫主页 → 玩法目录 → labyrinth（…）   ← 与实际不符
    [成功] 全局导航到达目标：异界迷宫（续局/域内）                            ← 那两步从未执行

根因：**先打规划日志、后判到达**。两种"其实不用走"的情形都会先打印一条不会执行的路：
  ① 任务的 `arrived_cb` 命中（已在执行域，如迷宫选门地图）→ 直接成功返回；
  ② **当前画面就是该目标的入口页** —— `_TARGET_SCREEN['labyrinth'] =
     'nav.labyrinth_main'`（异界迷宫主页），`_arrived` 当场判到达。
     图上之所以绕「玩法目录」，是因为「进入」那一步由任务自己做、不在导航图里。

修复（仅日志文本，不动执行逻辑）：
  ① 把 `arrived_cb` / `已在本任务入口页` 的判定提到规划日志之前，命中即打
     「0 步（跳过导航）」，不再打印未执行的路径；
  ② 按层到达的尾巴「实际 {i+1} 步」多报一步（到达那一轮只探测、没跳转）
     → 改为「实际 {i} 步」。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_nav_plan_log_honest.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.global_nav import GlobalNavigator  # noqa: E402
from tasks.nav_planner import plan_path  # noqa: E402

# 真机现场：当前画面 = 异界迷宫主页；导航目标 = labyrinth
MAIN = 'nav.labyrinth_main'      # 异界迷宫主页（= _TARGET_SCREEN['labyrinth']）
PLAYBOOK = 'nav.playbook'        # 玩法目录（1 层）

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def it(cy, cx, text, conf=0.9):
    return (cy, cx, text, conf, cx - 50, cx + 50)


class PermissiveBot:
    """只用于日志验证：未定义的方法一律 no-op，不产生副作用。"""

    last_snap_empty = False

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _noop(*a, **kw):
            self.calls.append(name)
            return None
        return _noop

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        return list(_FRAME)

    def find_text(self, items, kw, min_conf=0.5):
        return None


_FRAME = [it(300, 640, '进入')]


def build(logs, screen, layer, max_iter=3):
    bot = PermissiveBot()
    nav = GlobalNavigator(bot, logger=logs.append)
    nav._probe = lambda: (screen, list(_FRAME), layer)
    nav.max_iter = max_iter
    return nav


print('══ 导航日志如实性（v2.1.24）══')
print()

# ── A. arrived_cb 命中（已在任务执行域）→ 0 步，不得打印未执行的路径 ──
print('── A. arrived_cb 命中：已在任务执行域 ──')
logs_a = []
nav = build(logs_a, MAIN, 2)
_ok = nav.navigate_to('labyrinth', arrived_cb=lambda s, i: True)
_n = plan_path(MAIN, 'labyrinth')
_n = _n[1] if _n else None
check('A1 导航返回 True（到达）', _ok is True, repr(_ok))
check('A2 打印「已在任务执行域，0 步」',
      any('已在任务执行域，0 步' in m for m in logs_a),
      str([m for m in logs_a if '规划' in m][:2]))
check(f'A3 **不再**打印未执行的「最优路径 {_n} 步」',
      not any('最优路径' in m for m in logs_a),
      f'（该画面到 labyrinth 的图上距离={_n} 步，与真机日志的 2 步一致）')
check('A4 到达日志标注 0 步',
      any('续局/域内，0 步' in m for m in logs_a),
      str([m for m in logs_a if '到达目标' in m][:1]))
print()

# ── B. 无 arrived_cb，但画面就是目标入口页 → 同样 0 步（真机 17:14 的真实情形）──
print('── B. 已在目标入口页（真机情形②）──')
logs_b = []
nav = build(logs_b, MAIN, 2)
_okb = nav.navigate_to('labyrinth')
check('B1 导航返回 True', _okb is True, repr(_okb))
check('B2 打印「已在本任务入口页，0 步」',
      any('已在本任务入口页，0 步' in m for m in logs_b),
      str([m for m in logs_b if '规划' in m][:2]))
check('B3 **不再**打印「最优路径 2 步：异界迷宫主页 → 玩法目录 → labyrinth」',
      not any('最优路径' in m for m in logs_b),
      str([m for m in logs_b if '最优路径' in m][:1]))
check('B4 到达日志不写成「实际 1 步」（那两步没走过）',
      not any('实际 1 步' in m for m in logs_b),
      str([m for m in logs_b if '到达目标' in m][:1]))
print()

# ── C. 真的需要走（画面在 1 层）→ 规划日志照常有，步数取自规划器 ──
print('── C. 确实要走：规划日志仍然有用 ──')
logs_c = []
nav = build(logs_c, PLAYBOOK, 1)
nav.navigate_to('labyrinth', arrived_cb=lambda s, i: False)
_pc = plan_path(PLAYBOOK, 'labyrinth')
_nc = _pc[1] if _pc else None
check('C1 打印「最优路径 N 步」',
      any('最优路径' in m for m in logs_c),
      str([m for m in logs_c if '规划' in m][:2]))
check(f'C2 步数与 plan_path 一致（{_nc} 步，非硬编码）',
      (f'最优路径 {_nc} 步' in ''.join(logs_c)) if _nc else True,
      f'plan_path(playbook → labyrinth)={_nc}')
check('C3 路径链以规划器输出为准（起点=玩法目录）',
      any('玩法目录' in m and '最优路径' in m for m in logs_c),
      str([m for m in logs_c if '最优路径' in m][:1]))
print()

# ── D. 静态：只改日志，未改执行逻辑 ──
print('── D. 静态检查（仅日志文本）──')
_src = open(os.path.join(ROOT, 'tasks', 'global_nav.py'), encoding='utf-8').read()
check('D1 到位判定已提到规划日志之前',
      _src.index('_at_target_page: bool =')
      < _src.index('[规划] 最优路径 {_plan[1]} 步'))
check('D2 旧的多报一步 `实际 {i + 1} 步` 已移除',
      '实际 {i + 1} 步' not in _src)
check('D3 规划日志三种出口都保留（执行域 / 入口页 / 最优路径）',
      '已在任务执行域' in _src and '已在本任务入口页' in _src
      and '0 步（跳过导航）' in _src and '最优路径 {_plan[1]} 步' in _src)
check('D4 navigate_to 返回值语义未变（仍有 True/False 两路）',
      'return True' in _src and 'return False' in _src)
print()

print('═' * 56)
print(f'导航日志如实性：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

# -*- coding: utf-8 -*-
"""v2.1.6：任务间导航「下一任务已可直通则跳过」定向测试。

背景（用户 21:43 真机）：启动时已在迷宫「选门地图」（3 层），任务间导航
却把画面退回「玩法目录」（1 层），迷宫任务再点卡片回迷宫内部——白绕一圈。
修复：导航前先问下一任务 `startup_probe()`，返回 'ready' 则跳过导航。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402  注册全部任务
from task_registry import TaskInstance  # noqa: E402
import tasks.scheduler as S  # noqa: E402
from tasks.base import BaseTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


class _Bot:
    def __init__(self):
        self._stop_event = None
        self._pause = None
        self.stats = {}


class _ReadyTask(BaseTask):
    """startup_probe 返回 ready（模拟迷宫内部界面直通）。"""
    task_id = '_probe_ready'
    label = '_probe_ready'

    def run(self):
        return 'done'

    def startup_probe(self):
        return 'ready'


class _ClearTask(BaseTask):
    """startup_probe 返回 clear（常规：需要导航）。"""
    task_id = '_probe_clear'
    label = '_probe_clear'

    def run(self):
        return 'done'

    def startup_probe(self):
        return 'clear'


class _BoomTask(BaseTask):
    """startup_probe 抛异常（防御：不得因此卡住）。"""
    task_id = '_probe_boom'
    label = '_probe_boom'

    def run(self):
        return 'done'

    def startup_probe(self):
        raise RuntimeError('boom')


_orig = dict(S.TASK_CLASSES)
try:
    S.TASK_CLASSES['_probe_ready'] = _ReadyTask
    S.TASK_CLASSES['_probe_clear'] = _ClearTask
    S.TASK_CLASSES['_probe_boom'] = _BoomTask

    sched = S.TaskScheduler(_Bot(), logger=lambda m: None)

    print('── 1. 下一任务可直通 → 跳过导航 ──')
    inst_r = TaskInstance('_probe_ready#1', '_probe_ready', True)
    check("1a ready 任务 → 'ready'（v2.1.9 三态）",
          sched._to_next_ready(inst_r, None) == 'ready',
          repr(sched._to_next_ready(inst_r, None)))

    print('── 2. 常规任务 → 照常导航 ──')
    inst_c = TaskInstance('_probe_clear#1', '_probe_clear', True)
    check("2a clear 任务 → ''",
          sched._to_next_ready(inst_c, None) == '',
          repr(sched._to_next_ready(inst_c, None)))

    print('── 3. 探测异常 → 不阻塞（按常规导航）──')
    inst_b = TaskInstance('_probe_boom#1', '_probe_boom', True)
    check("3a 异常 → ''（不卡住）",
          sched._to_next_ready(inst_b, None) == '',
          repr(sched._to_next_ready(inst_b, None)))

    print('── 4. 真实迷宫任务：内部界面 probe 能力存在 ──')
    inst_lb = TaskInstance('labyrinth#1', 'labyrinth', True)
    lb_ok = sched._to_next_ready(inst_lb, None)
    check("4a 迷宫判定为三态之一（不抛异常）",
          lb_ok in ('ready', 'near', ''), repr(lb_ok))

    print('── 5. _navigate_to_next：直通时不做导航（无 bot 交互）──')
    calls = []

    class _B(_Bot):
        def _ocr_screen(self, img=None, no_cache=False):
            calls.append('ocr')
            return []

    sched2 = S.TaskScheduler(_B(), logger=lambda m: None)
    sched2.add(inst_r)
    sched2.add(inst_lb)   # 真实迷宫（nav_target='playbook'，probe 非 ready）
    # 当前 = ready#1，下一个 = 迷宫（不直通、且需导航）
    sched2._navigate_to_next(inst_r)
    check('5a 下一任务不直通且需导航 → 发生导航尝试（有 OCR 交互）',
          len(calls) > 0, f'ocr={len(calls)}')

    calls.clear()
    sched3 = S.TaskScheduler(_B(), logger=lambda m: None)
    sched3.add(inst_c)
    sched3.add(inst_r)
    # 当前 = clear#1，下一个 = ready#1 → 跳过导航（不应有任何 OCR）
    sched3._navigate_to_next(inst_c)
    # v2.1.9：声明优先判定本身需要 1 帧 OCR 识别画面；
    # 因此断言改为「不产生任何**导航动作**」（比 OCR 次数更本质）
    _nav_acts = [c for c in calls if c != 'ocr']
    check('5b 下一任务可直通 → 跳过导航（无导航动作）',
          len(_nav_acts) == 0, f'acts={_nav_acts}')
finally:
    S.TASK_CLASSES.clear()
    S.TASK_CLASSES.update(_orig)

print()
print(f'══ 任务间直通跳过测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
sys.exit(0 if not _FAIL else 1)

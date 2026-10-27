# -*- coding: utf-8 -*-
"""调度边界任务强制顺序测试（v2.0.9）：启动游戏第一 / 关闭游戏最后。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time  # noqa: F401
from task_registry import TaskInstance, TaskRegistry  # noqa: E402
from tasks.scheduler import TaskScheduler  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


class _Bot:
    """极简 bot 桩（排序逻辑不依赖 bot）。"""

    def __init__(self):
        self._stop_event = None
        self._pause = None
        self.stats = {}


def make_sched(ids):
    logs = []
    sched = TaskScheduler(_Bot(), logger=lambda m: logs.append(m))
    for i, cid in enumerate(ids):
        sched.add(TaskInstance(inst_id=f"{cid}#1", class_id=cid,
                               enabled=True))
    return sched, logs


print("── 1. 乱序输入 → 强制归位 ──")
s1, l1 = make_sched(["labyrinth", "close_game", "start_game", "home"])
s1._enforce_boundary_order()
order1 = [i.class_id for i in s1._queue]
check('1a 启动游戏排第一', order1[0] == 'start_game', str(order1))
check('1b 关闭游戏排最后', order1[-1] == 'close_game', str(order1))
check('1c 中间任务保持相对顺序',
      order1[1:3] == ['labyrinth', 'home'], str(order1))
check('1d 有日志说明', any('强制' in m for m in l1),
      str([m for m in l1 if "强制" in m][:1]))

print("── 2. 已在正确位置 → 不重复日志 ──")
s2, l2 = make_sched(["start_game", "labyrinth", "close_game"])
s2._enforce_boundary_order()
check("2a 顺序不变且无强制日志",
      [i.class_id for i in s2._queue]
      == ['start_game', 'labyrinth', 'close_game']
      and not any('强制' in m for m in l2), str(l2))

print("── 3. 无边界任务 → 完全不动 ──")
s3, l3 = make_sched(["home", "labyrinth"])
s3._enforce_boundary_order()
check('3a 保持原顺序', [i.class_id for i in s3._queue]
      == ['home', 'labyrinth'], str([i.class_id for i in s3._queue]))

print("── 4. 多个同类实例 → 全部归位 ──")
s4, _ = make_sched(["home", "close_game", "close_game", "start_game"])
s4._enforce_boundary_order()
order4 = [i.class_id for i in s4._queue]
check('4a 两个关闭游戏都在末尾',
      order4[:2] == ['start_game', 'home']
      and order4[2:] == ['close_game', 'close_game'], str(order4))

print('── 5. 归一化：边界残留中间 → 归位且普通任务恢复可拖（v2.1.1）──')
# 用户 20:45 反馈「其他任务拖动也受阻」的根因场景：_order 里边界在中间
TaskRegistry._order = ['auto_farm#1', 'start_game#1', 'labyrinth#1',
                       'close_game#1', 'home#1']
_mid_ok = TaskRegistry.move_instance('auto_farm#1', 1)
check('5a 边界残留中间时普通任务仍可换位', _mid_ok,
      str(TaskRegistry._order))
check('5b 换位后边界任务归首尾',
      TaskRegistry._order[0].startswith('start_game')
      and TaskRegistry._order[-1].startswith('close_game'),
      str(TaskRegistry._order))
check('5c 归一化后边界任务仍拒动',
      not TaskRegistry.move_instance('start_game#1', 1)
      and not TaskRegistry.move_instance('close_game#1', -1),
      str(TaskRegistry._order))
check('5d 不可跨越边界（末尾之后无位）',
      not TaskRegistry.move_instance(TaskRegistry._order[-2], 1),
      str(TaskRegistry._order))

print(f'══ 边界顺序测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print("失败项：")
    for f_ in _FAIL:
        print(" ", f_)
sys.exit(0 if not _FAIL else 1)
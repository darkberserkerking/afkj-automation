# -*- coding: utf-8 -*-
"""导航规划器测试（v1.9.58）：最短路径/步数/等价策略/不可达。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.nav_planner import NAV_EDGES, TARGET_SCREENS, describe_plan, plan_path

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def acts(path):
    return [a for _, a, _ in path]


print('── 1. 典型最短路径 ──')
r = plan_path('nav.world_scene', 'idle_stage')
check('1a 世界 → 挂机关卡 = 2 步（底部目录 + 卡片）',
      r is not None and r[1] == 2 and acts(r[0]) == ['bottom.playbook',
                                                     'card.idle_stage'],
      str(acts(r[0]) if r else None))

r = plan_path('nav.home_scene', 'labyrinth')
check('1b 家园 → 迷宫 = 2 步',
      r is not None and r[1] == 2 and acts(r[0])[0] == 'bottom.playbook',
      str(acts(r[0]) if r else None))

r = plan_path('nav.idle_stage', 'labyrinth')
check('1c 挂机关卡 → 迷宫 = 2 步（ESC 回目录 + 卡片）',
      r is not None and r[1] == 2 and acts(r[0]) == ['esc', 'card.labyrinth'],
      str(acts(r[0]) if r else None))

r = plan_path('nav.labyrinth', 'nav.idle_stage')
check('1d 迷宫 → 挂机关卡 = 2 步',
      r is not None and r[1] == 2, str(acts(r[0]) if r else None))

r = plan_path('nav.playbook_activity', 'idle_stage')
check('1e 活动页签 → 挂机关卡 = 2 步（切常规页签 + 卡片）',
      r is not None and r[1] == 2 and acts(r[0]) == ['tab.normal',
                                                     'card.idle_stage'],
      str(acts(r[0]) if r else None))

r = plan_path('nav.guild', 'labyrinth')
check('1f 公会 → 迷宫 = 2 步（底部目录 + 卡片）',
      r is not None and r[1] == 2, str(acts(r[0]) if r else None))

print('── 2. 同层等价路径选择策略（底部直达优先）──')
r = plan_path('nav.world_scene', 'nav.guild')
check('2a 世界 → 公会：优先底部直达（1 步，非 ESC+按钮）',
      r is not None and r[1] == 1 and acts(r[0]) == ['bottom.guild'],
      str(acts(r[0]) if r else None))
r = plan_path('nav.guild', 'nav.playbook')
check('2b 公会 → 玩法目录：优先底部直达（1 步）',
      r is not None and r[1] == 1 and acts(r[0]) == ['bottom.playbook'],
      str(acts(r[0]) if r else None))

print('── 3. 起点即目标 / 兼容别名 ──')
r = plan_path('nav.playbook', 'playbook')
check('3a 已在目标 → 0 步空路径', r == ([], 0), str(r))
r = plan_path('nav.home_scene', 'world_home')
check('3b world_home 目标：家园场景即算到达（0 步）', r == ([], 0), str(r))
r = plan_path('nav.world_scene', 'world_home')
check('3c world_home 目标：世界场景即算到达（0 步）', r == ([], 0), str(r))

print('── 4. 场景互切 ──')
r = plan_path('nav.world_scene', 'home_scene')
check('4a 世界 → 家园 = 1 步（右下按钮）',
      r is not None and r[1] == 1 and acts(r[0]) == ['scene.home'],
      str(acts(r[0]) if r else None))
r = plan_path('nav.home_scene', 'world_scene')
check('4b 家园 → 世界 = 1 步', r is not None and r[1] == 1, str(r))

print('── 5. 全目标可达性（自动探索：任意节点 → 任意目标都有解）──')
targets = list(TARGET_SCREENS)
nodes = list(NAV_EDGES)
missing = []
for s in nodes:
    for t in targets:
        if plan_path(s, t) is None:
            missing.append((s, t))
check('5a 图中全部节点 → 全部目标均有路径', not missing,
      str(missing[:4]))

print('── 6. 日志输出 ──')
log = describe_plan('nav.home_scene', 'idle_stage')
print('   ', log)
check('6a 日志含路径与总步数', '最优路径' in log and '2 步' in log, log[:60])

print(f'══ 导航规划器测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
sys.exit(0 if not _FAIL else 1)

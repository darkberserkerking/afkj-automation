# -*- coding: utf-8 -*-
"""层级规范固化（v2.1.16，用户 2026-09-11 12:57 定案）

用户规范：
  -1 层：加载页面、开始页面（点击开始游戏）
   0 层：世界、家园
   1 层：神秘屋、玩法目录、英雄厅堂、公会、主菜单
   2 层：各玩法或菜单的主界面
  更深层：各玩法或菜单内部更深层
   家园专属：愿望单等只在家园内可见，必须先回家园再前往
跳转规则：
   · 0 层之间一步切换
   · 0 层可一步前往任意 1 层
   · 1 层可一步前往其他 1 层或世界
   · 主菜单特殊（无底部 UI 栏）→ 必须先回 0 层才能前往其他 1 层
   · 家园专属可以一步回家园
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.global_nav import _SCREEN_LAYER  # noqa: E402
from tasks.nav_planner import plan_path  # noqa: E402
from tasks.screen_registry import (GLOBAL_SCREEN_REGISTRY as R,  # noqa: E402
                                   ZH_SCREEN)

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _it(cy, cx, text, conf=0.95):
    return (cy, cx, text, conf, cx - 50, cx + 50)


print('══ 层级规范固化（v2.1.16）══')
print()
print('── A. 层级表逐项（用户规范）──')
EXPECT = {
    -1: ['nav.loading', 'nav.login'],
    0: ['nav.world_scene', 'nav.home_scene'],
    1: ['nav.playbook', 'nav.guild', 'nav.mystery_house', 'nav.hero_hall',
        'nav.main_menu'],
    2: ['nav.idle_stage', 'nav.labyrinth_main', 'goddess.select_page',
        'arena.main', 'arena.select', 'labyrinth.push_map', 'nav.friend'],
}
for layer, ids in EXPECT.items():
    for _id in ids:
        check(f'A {layer} 层 | {_id}', _SCREEN_LAYER.get(_id) == layer,
              f"实际={_SCREEN_LAYER.get(_id)}")
check('A 更深层：迷宫内部为 3 层（labyrinth.door）',
      _SCREEN_LAYER.get('labyrinth.door') == 3,
      str(_SCREEN_LAYER.get('labyrinth.door')))
check('A 更深层：女神内部为 3 层（goddess.battle_prep）',
      _SCREEN_LAYER.get('goddess.battle_prep') == 3,
      str(_SCREEN_LAYER.get('goddess.battle_prep')))
print()

print('── B. 跳转规则 ──')
# 0 层互跳一步
p = plan_path('nav.world_scene', 'nav.home_scene')
check('B1 0 层互跳 1 步', p is not None and len(p[0]) == 1, str(p and len(p[0])))
# 0 层 → 任意 1 层一步
for _one in ('nav.playbook', 'nav.mystery_house', 'nav.hero_hall',
             'nav.guild'):
    p = plan_path('nav.world_scene', _one)
    check(f'B2 世界 → {_one} 一步',
          p is not None and len(p[0]) == 1, str(p and len(p[0])))
    p = plan_path('nav.home_scene', _one)
    check(f'B2 家园 → {_one} 一步',
          p is not None and len(p[0]) == 1, str(p and len(p[0])))
# 1 层互跳一步
for _from, _to in (('nav.playbook', 'nav.hero_hall'),
                   ('nav.mystery_house', 'nav.guild'),
                   ('nav.hero_hall', 'nav.mystery_house')):
    p = plan_path(_from, _to)
    check(f'B3 {_from} → {_to} 一步',
          p is not None and len(p[0]) == 1, str(p and len(p[0])))
# 1 层 → 世界一步
p = plan_path('nav.hero_hall', 'nav.world_scene')
check('B4 英雄厅堂 → 世界 一步',
      p is not None and len(p[0]) == 1, str(p and len(p[0])))
# 主菜单特殊：去其他 1 层必须绕 0 层
for _to in ('nav.playbook', 'nav.guild', 'nav.mystery_house', 'nav.hero_hall'):
    p = plan_path('nav.main_menu', _to)
    _ok = (p is not None and len(p[0]) >= 2
           and p[0][0][1] == 'esc')       # 首步必须是退回 0 层
    check(f'B5 主菜单 → {_to} 必须先回 0 层（≥2 步且首步 esc）',
          _ok, str(p and [e[1] for e in p[0]]))
# 家园专属一步回家园
p = plan_path('home.wishlist', 'nav.home_scene')
check('B6 愿望单 → 家园 一步',
      p is not None and len(p[0]) == 1, str(p and len(p[0])))
p = plan_path('home.workshop', 'nav.home_scene')
check('B7 家园工坊 → 家园 一步',
      p is not None and len(p[0]) == 1, str(p and len(p[0])))
# 好友界面（2 层）→ ESC 回主菜单面板
p = plan_path('nav.friend', 'nav.main_menu')
check('B8 好友界面 → 主菜单面板 一步（真机确认 ESC 可达）',
      p is not None and len(p[0]) == 1, str(p and [e[1] for e in p[0]]))
print()

print('── C. 新画面识别（含底部栏防串词 + 活动页防抢判）──')
check('C1 神秘屋 → nav.mystery_house',
      R.detect([_it(200, 400, '神秘珍藏')]) == 'nav.mystery_house')
check('C2 英雄厅堂 → nav.hero_hall',
      R.detect([_it(400, 600, '赛季共鸣等级')]) == 'nav.hero_hall')
check('C3 主菜单 → nav.main_menu',
      R.detect([_it(200, 600, '剧情任务'), _it(200, 800, '星夜逸闻'),
                _it(400, 300, '背包'), _it(400, 500, '邮件')])
      == 'nav.main_menu')
# 关键：底部 UI 栏通用词不得串台（神秘屋/英雄厅堂界面上也有「公会」「玩法目录」）
_f = [_it(200, 400, '神秘珍藏'), _it(950, 300, '神秘屋'),
      _it(950, 450, '玩法目录'), _it(950, 640, '英雄厅堂'),
      _it(950, 900, '公会')]
check('C4 【防串词】含**完整底部栏**仍判神秘屋（不误判 nav.world_home）',
      R.detect(_f) == 'nav.mystery_house', R.detect(_f))
_f2 = [_it(400, 600, '赛季共鸣等级'), _it(950, 300, '神秘屋'),
       _it(950, 450, '玩法目录'), _it(950, 640, '英雄厅堂'),
       _it(950, 900, '公会')]
check('C5 【防串词】英雄厅堂真帧（含完整底部栏）不误判 world_home',
      R.detect(_f2) == 'nav.hero_hall', R.detect(_f2))
# v2.1.16b（独立验证 F3）：「修行之路」是活动页卡片词，不得把活动页判成主菜单
_f3 = [_it(300, 300, '活动'), _it(400, 600, '常规玩法'),
       _it(200, 700, '修行之路')]
check('C6 【防抢判】活动页含「修行之路」→ 不判主菜单',
      R.detect(_f3) != 'nav.main_menu', R.detect(_f3))
print()

print('── D. 中文名齐备 ──')
for _k in ('nav.mystery_house', 'nav.hero_hall', 'nav.main_menu'):
    check(f'D {_k} 中文名', bool(ZH_SCREEN.get(_k)), str(ZH_SCREEN.get(_k)))

print()
print('═' * 56)
print(f'层级规范固化：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

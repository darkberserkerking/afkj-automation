# -*- coding: utf-8 -*-
"""导航出边完备性测试（v1.9.58）：注册表画面 → NAV_EDGES 出边覆盖。

用户要求「精准识别每一个已录入画面 → 确认导航目标 → 算出最短路径」。
本测试把该要求落成可验证判据：除系统弹层白名单外，注册表里每个画面
都必须在 NAV_EDGES 中有出边（含兜底 CHAIN），否则规划器只会打印
「无可达路径…按贪心规则继续」。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tasks import nav_planner as NP

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


# ── 从注册表源码用正则抽取全部已注册画面 id ──────────────────────────
#    register 调用可能跨行（reg.register(\n    'xxx', ...)），\s* 已含换行
_REG_SRC = os.path.join(ROOT, 'tasks', 'screen_registry.py')
with open(_REG_SRC, encoding='utf-8') as _f:
    _SRC = _f.read()
REGISTERED = re.findall(r"reg\.register\(\s*'([^']+)'", _SRC)

# ── 系统弹层白名单（硬编码，须与 nav_planner.NAV_EDGES 注释同步维护）──
#    这些画面是覆盖层/过渡态，无稳定"上一层"语义，不入路径图，
#    由统一清理逻辑（点 X / 等待 / 领奖）处理，故不要求有出边。
OVERLAY_WHITELIST = {
    'nav.quit_confirm',    # 退出确认弹窗（ESC 陷阱）
    'nav.loading',         # 加载界面（动作=等待）
    'nav.login',           # 游戏登录界面（重启/掉线后停此页）
    'nav.monthly_reward',  # 伊索米亚月刊弹窗（动作=领取奖励）
              # 好友界面（社交页，非导航目标）
    'home.res_short',      # 一键家园·资源不足弹窗（材料耗尽提示）
}

EDGES = NP.NAV_EDGES

print('── 1. 画面注册表解析 ──')
check('1a 已注册画面数 ≥ 35', len(REGISTERED) >= 35,
      f'实际 {len(REGISTERED)} 个')

print('── 2. 出边完备性（除系统弹层外，每个已录入画面都必须有出边）──')
_missing = [s for s in REGISTERED
            if s not in OVERLAY_WHITELIST and not EDGES.get(s)]
check('2a 全部非弹层画面均有出边', not _missing, str(_missing[:6]))

print('── 3. 出边目标合法（注册画面 或 TARGET_SCREENS 目标值）──')
_valid = set(REGISTERED) | set(NP.TARGET_SCREENS.values())
_bad = [(s, to) for s, outs in EDGES.items() for to, _, _ in outs
        if to not in _valid]
check('3a 边目标均为合法画面 id（防拼错）', not _bad, str(_bad[:6]))

print('── 4. 无自环 ──')
_loops = [(s, to) for s, outs in EDGES.items() for to, _, _ in outs
          if to == s]
check('4a 无边指向自身', not _loops, str(_loops[:6]))

print('── 5. 可达性抽样（新补边后关键路径必须非 None 且步数 ≥1）──')
for start, goal in (('labyrinth.door', 'playbook'),
                    ('nav.labyrinth_main', 'playbook'),
                    ('home.wishlist', 'home_scene'),
                    ('labyrinth.door', 'labyrinth')):
    _r = NP.plan_path(start, goal)
    check(f'5 {start} → {goal} 可达',
          _r is not None and _r[1] >= 1,
          f'{_r[1] if _r else None} 步')

print('── 6. 顶层目标回归（既有最优路径不因新增边而改变）──')
_r = NP.plan_path('nav.playbook', 'labyrinth')
check('6a 玩法目录 → 迷宫 仍为 1 步（卡片直达）',
      _r is not None and _r[1] == 1, str(_r[1] if _r else None))

print(f'══ 通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for _f in _FAIL:
        print(' ', _f)
sys.exit(0 if not _FAIL else 1)

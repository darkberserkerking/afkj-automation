# -*- coding: utf-8 -*-
"""独立验证 + 证伪：导航层级校准（用户 2026-09-11 12:57 规范）。

与作者自测 tests/test_layer_convention.py **相互独立**：场景全部自建，
不照抄其用例。本脚本同时承担「证伪」职责——把确认的判据误判/抢判以
characterization 形式固定下来（见 F 段）。

覆盖：
  A 层级表            B 跳转规则矩阵（plan_path 实测）
  C 出边结构          D 三新画面识别（内部词单帧）
  E 判据词归属冲突    F 误判/抢判证伪（重点）
  G 可达性缺口

退出码：
  python tests/test_layer_convention_verify.py            -> exit 0
       （F/G 段已知缺陷记为 ⚠DEFECT，不改变退出码）
  python tests/test_layer_convention_verify.py --strict   -> 已知缺陷计入失败
       （或环境变量 NAV_VERIFY_STRICT=1）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402  与既有测试同款时间加速
import tasks.catalog  # noqa: F401,E402

from tasks.global_nav import _SCREEN_LAYER  # noqa: E402
from tasks.nav_planner import NAV_EDGES, plan_path  # noqa: E402
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY as R  # noqa: E402

STRICT = ('--strict' in sys.argv
          or os.environ.get('NAV_VERIFY_STRICT') == '1')
_PASS, _FAIL, _DEFECT = [], [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    tail = f'  {detail}' if detail else ''
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{tail}")


def defect(name, present, detail=''):
    """存在性证伪：present=True 表示该缺陷在现行代码里确已成立。"""
    if present:
        _DEFECT.append(name)
    tail = f'  {detail}' if detail else ''
    print(f"[{'⚠DEFECT' if present else 'OK'}] {name}{tail}")


def it(cy, cx, text, conf=0.95):
    """构造 OCR 6 元组（与 _texts/_mk_has 约定一致）。"""
    return (cy, cx, text, conf, cx - 50, cx + 50)


def steps(p):
    return None if p is None else p[1]


def acts(p):
    return None if p is None else [e[1] for e in p[0]]


ONE = ('nav.playbook', 'nav.guild', 'nav.mystery_house', 'nav.hero_hall')
ZERO = ('nav.world_scene', 'nav.home_scene')

print('══ A. 层级表（用户规范逐项）══')
for sid, lv in (('nav.mystery_house', 1), ('nav.hero_hall', 1),
                ('nav.main_menu', 1), ('nav.friend', 2),
                ('nav.playbook', 1), ('nav.guild', 1),
                ('nav.world_scene', 0), ('nav.home_scene', 0),
                ('home.wishlist', 3), ('home.workshop', 3)):
    check(f'A {sid} == {lv} 层', _SCREEN_LAYER.get(sid) == lv,
          f'实际={_SCREEN_LAYER.get(sid)}')
print()

print('══ B. 跳转规则矩阵（plan_path 实测）══')
print('── B1 0 层互切 1 步 ──')
for a, b in (('nav.world_scene', 'nav.home_scene'),
             ('nav.home_scene', 'nav.world_scene')):
    check(f'B1 {a} → {b} 一步', steps(plan_path(a, b)) == 1,
          str(steps(plan_path(a, b))))
print('── B2 0 层 → 底栏可达的 1 层 一步 ──')
for z in ZERO:
    for o in ONE:
        check(f'B2 {z} → {o} 一步', steps(plan_path(z, o)) == 1,
              str(steps(plan_path(z, o))))
print('── B3 1 层 ↔ 1 层 一步（全序对）──')
for a in ONE:
    for b in ONE:
        if a == b:
            continue
        check(f'B3 {a} → {b} 一步', steps(plan_path(a, b)) == 1,
              str(steps(plan_path(a, b))))
print('── B4 1 层 → 0 层 一步 ──')
for o in ONE:
    for z in ZERO:
        check(f'B4 {o} → {z} 一步', steps(plan_path(o, z)) == 1,
              str(steps(plan_path(o, z))))
print('── B5 主菜单特殊：去其它 1 层必须先回 0 层 ──')
for o in ONE:
    p = plan_path('nav.main_menu', o)
    ok = (p is not None and p[1] >= 2 and p[0][0][1] == 'esc')
    check(f'B5 主菜单 → {o} ≥2 步且首步 esc', ok, str(acts(p)))
_mm_acts = [a for _t, a, _l in NAV_EDGES.get('nav.main_menu', ())]
check('B5b 【v2.1.16b】主菜单出边无底栏/卡片直达（menu.* 菜单项允许）',
      bool(_mm_acts) and not any(a.startswith(('bottom.', 'card.'))
                                 for a in _mm_acts), str(_mm_acts))
print('── B6 家园专属：一步回家园 ──')
check('B6 愿望单 → 家园 一步',
      steps(plan_path('home.wishlist', 'nav.home_scene')) == 1)
check('B6 工坊 → 家园 一步',
      steps(plan_path('home.workshop', 'nav.home_scene')) == 1)
print('── B7/B8 好友与主菜单 ──')
p_fm = plan_path('nav.friend', 'nav.main_menu')
check('B7 好友 → 主菜单 一步(esc)',
      steps(p_fm) == 1 and acts(p_fm) == ['esc'], str(acts(p_fm)))
for z in ZERO:
    check(f'B8 主菜单 → {z} 一步', steps(plan_path('nav.main_menu', z)) == 1,
          str(steps(plan_path('nav.main_menu', z))))
print()

print('══ C. 出边结构 ══')
for a in ONE:
    outs = {t for t, _a, _l in NAV_EDGES.get(a, ())}
    check(f'C1 {a} 出边覆盖其它全部 1 层',
          all(b in outs for b in ONE if b != a), str(sorted(outs)))
check('C2 主菜单无指向 1 层的出边（只能回 0 层）',
      not ({t for t, _a, _l in NAV_EDGES.get('nav.main_menu', ())} & set(ONE)),
      str(sorted({t for t, _a, _l in NAV_EDGES.get('nav.main_menu', ())})))
print()

print('══ D. 三新画面识别（内部词单帧）══')
D_CASES = (
    ('D1 神秘屋·神秘珍藏', [it(200, 400, '神秘珍藏')], 'nav.mystery_house'),
    ('D2 神秘屋·金玫瑰高定', [it(200, 400, '金玫瑰高定')], 'nav.mystery_house'),
    ('D3 神秘屋·月桂酒馆', [it(200, 400, '月桂酒馆')], 'nav.mystery_house'),
    ('D4 英雄厅堂·赛季共鸣等级', [it(400, 600, '赛季共鸣等级')], 'nav.hero_hall'),
    ('D5 英雄厅堂·幻灵之契+共鸣之手',
     [it(400, 500, '幻灵之契'), it(400, 800, '共鸣之手')], 'nav.hero_hall'),
    ('D7 主菜单·剧情任务+星夜逸闻',
     [it(200, 500, '剧情任务'), it(300, 500, '星夜逸闻')], 'nav.main_menu'),
    ('D8 主菜单·旅行指南+月桂之旅',
     [it(200, 500, '旅行指南'), it(300, 500, '月桂之旅')], 'nav.main_menu'),
)
# v2.1.16b（已修）：『修行之路』是活动页卡片词，单词**不再**命中主菜单
check('D6 【v2.1.16b 已修】活动页词「修行之路」单帧不再判主菜单',
      R.detect([it(200, 600, '修行之路')]) != 'nav.main_menu',
      f"实际={R.detect([it(200, 600, '修行之路')])}")

for name, frame, exp in D_CASES:
    got = R.detect(frame)
    check(name, got == exp, f'期望={exp} 实际={got}')
print()

print('══ E. 判据词归属（是否被别的画面判据复用）══')
_WORD_OWNER = {
    '神秘珍藏': 'nav.mystery_house',
    '金玫瑰高定': 'nav.mystery_house',
    '月桂酒馆': 'nav.mystery_house',
    '赛季共鸣等级': 'nav.hero_hall',
    '幻灵之契': None,      # AND 词：单现不应命中任何画面
    '共鸣之手': None,
    '修行之路': None,       # v2.1.16b：活动页卡片词，单词不再命中主菜单
    '剧情任务': None,
    '星夜逸闻': None,
    '旅行指南': None,
    '月桂之旅': None,
}
_rules = sorted(R._rules, key=lambda r: (r[0], r[1]))


def _single_frame_hits(word):
    fr = [it(200, 640, word)]
    out = []
    for _pri, _seq, sid, matcher, _d in _rules:
        try:
            if matcher(fr):
                out.append(sid)
        except Exception:
            pass
    return out


for word, owner in _WORD_OWNER.items():
    hits = _single_frame_hits(word)
    exp = [] if owner is None else [owner]
    check(f'E 词「{word}」单帧命中集 == {exp}', hits == exp, f'实际={hits}')
print()

print('══ F. 误判 / 抢判证伪（重点）══')
# 1 层画面底部常驻 UI 栏（screen_registry.py:440-441 与 global_nav.py:36 注释）
BAR = [it(950, 300, '神秘屋'), it(950, 640, '玩法目录'),
       it(950, 900, '英雄厅堂'), it(950, 1100, '公会')]
_f_mystery = [it(200, 400, '神秘珍藏')] + BAR
_f_hero = [it(400, 600, '赛季共鸣等级')] + BAR
_f_activity = [it(200, 300, '活动'), it(200, 900, '常规玩法'),
               it(400, 500, '修行之路')]
_f_fr_main = [it(300, 500, '收礼进度 10/10'),
              it(200, 600, '剧情任务'), it(200, 900, '星夜逸闻')]
_f_fr_only = [it(300, 500, '收礼进度 10/10'), it(300, 800, '一键赠礼')]

defect('F1 神秘屋真实帧(内部词+底栏四入口) 被 nav.world_home(145) 抢判',
       R.detect(_f_mystery) == 'nav.world_home',
       f'实际={R.detect(_f_mystery)}（期望 nav.mystery_house）')
defect('F2 英雄厅堂真实帧(内部词+底栏四入口) 被 nav.world_home(145) 抢判',
       R.detect(_f_hero) == 'nav.world_home',
       f'实际={R.detect(_f_hero)}（期望 nav.hero_hall）')
defect('F3 活动页含「修行之路」卡片 被 nav.main_menu(136) 抢判',
       R.detect(_f_activity) == 'nav.main_menu',
       f'实际={R.detect(_f_activity)}（期望 nav.activity；'
       'docs/界面判据清单.md:29 实测修行之路是活动页卡片词）')
defect('F4 好友词与主菜单词同帧 → 主菜单面板覆盖在好友页之上时应判 nav.main_menu',
       R.detect(_f_fr_main) != 'nav.main_menu',
       f'实际={R.detect(_f_fr_main)}（期望 nav.main_menu）')
check('F4b 对照：纯好友页(收礼进度+一键赠礼) → nav.friend（防过度矫正）',
      R.detect(_f_fr_only) == 'nav.friend',
      f'实际={R.detect(_f_fr_only)}')
# 对照组：内部词单帧 / 主菜单面板自身应正确
check('F0a 对照：神秘屋内部词单帧 → nav.mystery_house',
      R.detect([it(200, 400, '神秘珍藏')]) == 'nav.mystery_house')
check('F0b 对照：主菜单面板(修行之路+剧情任务+星夜逸闻) → nav.main_menu',
      R.detect([it(200, 600, '修行之路'), it(300, 400, '剧情任务'),
                it(300, 700, '星夜逸闻')]) == 'nav.main_menu')
# 根因：通用底栏判据 world_home 的优先级数值 < 两个新画面 → 先评估即抢判
def _prio(sid):
    return next((p for p, _s, s, _m, _d in _rules if s == sid), None)


_p_wh = _prio('nav.world_home')
_p_mh = _prio('nav.mystery_house')
_p_hh = _prio('nav.hero_hall')
defect('F5 根因：world_home 优先级数值 < mystery_house/hero_hall（先评估 → 抢判）',
       None not in (_p_wh, _p_mh, _p_hh) and _p_wh < _p_mh and _p_wh < _p_hh,
       f'world_home={_p_wh}, mystery_house={_p_mh}, hero_hall={_p_hh}')
print()

print('══ G. 可达性缺口 ══')
defect('G1 0/1 层 → nav.main_menu 无入边（plan_path=None，'
       '不满足「0 层可一步前往任意 1 层」）',
       plan_path('nav.world_scene', 'nav.main_menu') is None,
       f'实际={plan_path("nav.world_scene", "nav.main_menu")}')
defect('G2 nav.friend 无入边（plan_path 无法规划到好友页）',
       plan_path('nav.playbook', 'nav.friend') is None,
       f'实际={plan_path("nav.playbook", "nav.friend")}')
check('G3 对照：好友 → 世界 可达',
      plan_path('nav.friend', 'nav.world_scene') is not None)
check('G4 对照：主菜单 → 世界 可达',
      plan_path('nav.main_menu', 'nav.world_scene') is not None)
print()

print('═' * 60)
print(f'独立验证：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for _f in _FAIL:
        print('  -', _f)
if _DEFECT:
    print(f'⚠ 已知缺陷 {len(_DEFECT)} 项（默认不影响退出码；--strict 时计失败）：')
    for _d in _DEFECT:
        print('  -', _d)
print('═' * 60)

if _FAIL:
    sys.exit(1)
if STRICT and _DEFECT:
    sys.exit(1)
sys.exit(0)

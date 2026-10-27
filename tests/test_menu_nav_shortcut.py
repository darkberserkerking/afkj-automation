# -*- coding: utf-8 -*-
"""主菜单入口导航收敛（v2.1.15，用户 2026-09-11 12:28 真机）

用户反馈：
  3. 从收取友情点到每日任务，只需要两步：①返回上一层（主菜单界面）
     ②点击任务按钮
  —— 但真机 12:18:26~12:18:40 白花 14 秒：好友界面 → ESC/返回按钮 →
     推图副本 → 再 ESC/返回按钮 → 玩法目录 → 才点主菜单。

根因：`tasks/menu_nav.py` 的 `MENU_BY_SCREEN` 只登记 5 个画面，
      其它画面（含「好友界面」）走 `tpl is None` 分支 → **直接全局导航到玩法目录**。

修复：① 好友界面（主菜单子页，菜单面板仍在下层）登记为 '' → 直接点菜单项；
      ② 未登记画面 → **先试两款主菜单模板**，都不命中才走导航兜底。

另覆盖：新注册画面（家园工坊内部 / 竞技场）的识别与路径可达。
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
import tasks.menu_nav as MN  # noqa: E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY as R  # noqa: E402
from tasks.global_nav import _SCREEN_LAYER  # noqa: E402
from tasks.nav_planner import plan_path  # noqa: E402
from tasks.screen_registry import ZH_SCREEN  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _it(cy, cx, text, conf=0.9):
    return (cy, cx, text, conf, cx - 50, cx + 50)


class MockBot:
    """template_match 只对指定模板返回坐标；其余 None。"""

    def __init__(self, texts_by_call=None, hit_tpls=None, nav_logs=None):
        self._t = list(texts_by_call or [])
        self.hit_tpls = set(hit_tpls or [])
        self.calls = []
        self.clicks = []

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._t:
            return self._t.pop(0)
        return [_it(300, 640, '玩法目录')]

    def template_match(self, tpl, threshold=0.75, silent=False, **kw):
        self.calls.append(f'tpl:{tpl}')
        if tpl in self.hit_tpls:
            return (640, 640)
        return None

    def click_ratio(self, rx, ry, label=''):
        self.clicks.append(f'click_ratio:{label}')
        return True

    def press_esc(self, label=''):
        self.clicks.append(f'ESC:{label}')
        return None

    def _click_abs(self, x, y):
        self.clicks.append(f'_click_abs:({x},{y})')
        return True

    def click_at(self, x, y, label=''):
        self.clicks.append(label)
        return True

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
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
        'inst_id': 'menu#shortcut',
    })


def _on_daily(items):
    return any('每日任务' in it[2] for it in items)


F_FRIEND = [_it(300, 640, '收礼进度'), _it(200, 300, '一键赠礼')]
F_IDLE = [_it(862, 639, '已完成全部挑战'), _it(536, 287, '先锋关卡'),
          _it(494, 268, '赛季挂机关卡'), _it(980, 638, '英雄厅堂')]
F_PLAYBOOK = [_it(300, 640, '玩法目录'), _it(200, 300, '常规玩法')]
F_DAILY = [_it(200, 640, '每日任务'), _it(300, 640, '一键领取')]

print('══ 主菜单入口导航收敛（v2.1.15）══')
print()
print('── A. 【用户第 3 点】好友界面 → 返回上一层 → 点菜单项（两步走，不导航）──')
logs_a = []
# 帧序：好友界面 → 返回上层后的主菜单面板 → 点菜单项后仍是面板 → 每日任务页
ba = MockBot(texts_by_call=[F_FRIEND, F_PLAYBOOK, F_PLAYBOOK, F_DAILY],
             hit_tpls=['btn_quest.png'])
ta = _mk(ba, logger=lambda m: logs_a.append(m))
ok_a = MN.open_from_main_menu(ta, _on_daily, 'btn_quest.png', 0.75,
                              '任务', label='每日任务')
check('A1 到达目标页', ok_a is True, str(ok_a))
check('A2 【核心】未点主菜单按钮（子页只需返回上层）',
      not any('tpl:btn_menu_' in c for c in ba.calls),
      str([c for c in ba.calls if 'btn_menu' in c][:2]))
check('A3 【核心】未发生导航绕路',
      not any('全局导航' in m for m in logs_a),
      str([m for m in logs_a if '导航' in m][:1]))
check('A4 【核心】先「返回上一层」（ESC）——用户定案的第①步',
      any(c.startswith('ESC:') for c in ba.clicks),
      str(ba.clicks[:3]))
check('A5 点了菜单项（模板命中 → 点菜单面板按钮）',
      any(c.startswith('_click_abs') for c in ba.clicks),
      str(ba.clicks[:3]))
print()

print('── B. 未登记画面（2 层）→ 先试两款主菜单模板 → 命中即点 ──')
logs_b = []
bb = MockBot(texts_by_call=[F_IDLE, F_DAILY],
             hit_tpls=['btn_menu_l1.png', 'btn_quest.png'])
tb = _mk(bb, logger=lambda m: logs_b.append(m))
ok_b = MN.open_from_main_menu(tb, _on_daily, 'btn_quest.png', 0.75,
                              '任务', label='每日任务')
check('B1 到达目标页', ok_b is True, str(ok_b))
check('B2 试过主菜单模板（0/1 层两款）',
      any('tpl:btn_menu_l0.png' in c for c in bb.calls)
      and any('tpl:btn_menu_l1.png' in c for c in bb.calls),
      str([c for c in bb.calls if 'btn_menu' in c][:3]))
check('B3 未发生导航（模板命中即够）',
      not any('全局导航' in m for m in logs_b),
      str([m for m in logs_b if '导航' in m][:1]))
print()

print('── C. 未登记画面 + 两款模板都不命中 → 导航兜底（保留安全网）──')
logs_c = []
# 一直停在未登记画面（2 层），且主菜单模板全部不命中 → 每轮都走兜底
bc = MockBot(texts_by_call=[F_IDLE] * 4, hit_tpls=[])
tc = _mk(bc, logger=lambda m: logs_c.append(m))
# 用假导航替换真实 GlobalNavigator（项目 dryrun 测试惯例）
import tasks.global_nav as GN


class _FakeNav:
    def __init__(self, bot, **kw):
        self.bot = bot

    def navigate_to(self, target):
        return True


_old_nav = GN.GlobalNavigator
GN.GlobalNavigator = _FakeNav
try:
    ok_c = MN.open_from_main_menu(tc, _on_daily, 'btn_quest.png', 0.75,
                                  '任务', label='每日任务')
finally:
    GN.GlobalNavigator = _old_nav
check('C1 两款模板都不命中 → 走导航兜底（日志可见）',
      any('全局导航兜底' in m for m in logs_c),
      str([m for m in logs_c if '兜底' in m][:1]))
check('C2 始终到不了目标页 → 返回 False（不假装成功）', ok_c is False, str(ok_c))
print()

print('── D. 回归：登记画面用对应款式 ──')
logs_d = []
bd = MockBot(texts_by_call=[F_PLAYBOOK, F_DAILY],
             hit_tpls=['btn_menu_l1.png', 'btn_quest.png'])
td = _mk(bd, logger=lambda m: logs_d.append(m))
ok_d = MN.open_from_main_menu(td, _on_daily, 'btn_quest.png', 0.75,
                              '任务', label='每日任务')
check('D1 玩法目录 → 用 l1 款式且到达', ok_d is True, str(ok_d))
check('D2 未走导航', not any('全局导航' in m for m in logs_d), '')
print()

print('── E. 新注册画面：识别 / 层级 / 路径（v2.1.15）──')
check('E1 家园工坊内部识别', R.detect([_it(900, 640, '开始锻造')]) == 'home.workshop')
check('E2 竞技场主界面识别',
      R.detect([_it(600, 640, '开始挑战'),
                _it(300, 200, '防守阵容')]) == 'arena.main')
check('E3 竞技场选对手识别', R.detect([_it(200, 640, '选择对手')]) == 'arena.select')
for _k, _layer in (('home.workshop', 3), ('arena.main', 2), ('arena.select', 2)):
    check(f'E4 {_k} 层级={_layer}', _SCREEN_LAYER.get(_k) == _layer,
          str(_SCREEN_LAYER.get(_k)))
    check(f'E5 {_k} 中文名存在', bool(ZH_SCREEN.get(_k)), str(ZH_SCREEN.get(_k)))
    check(f'E6 {_k} → 玩法目录可达', plan_path(_k, 'playbook') is not None, '')
print()

print('═' * 56)
print(f'主菜单导航收敛：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

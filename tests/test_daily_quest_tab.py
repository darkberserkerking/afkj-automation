# -*- coding: utf-8 -*-
"""每日任务：两级标签检测与切换（v2.1.25，用户 2026-09-11 18:21/18:24）

需求原话：
  「任务界面有多种标签。一级标签：日常任务、翻翻乐、成长挑战、赛季任务、
    家园目标；日常任务下还有二级标签：每日任务、公会任务。进入任务界面后
    需要检测是否在日常任务_每日任务标签，不在的话需要点击日常任务、再点击
    每日任务。」
  「界面左上角有当前所在标签标题。」

两条判据（主 + 兜底）：
  ① **左上角标题**（主）——真机实测「每日任务」@ (207.5, 29.8) conf 0.99，
     切别的标签标题会变成「翻翻乐/成长挑战/…」；
  ② **二级标签上边框取色**（标题读不到时的兜底）——真机逐行 R−B：
     选中的「每日任务」 853→130 / 854→175 / 855→**187** / 856→169 / 857→124；
     未选中的「公会任务」全程 **16~19**。阈值 80 余量极大。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_daily_quest_tab.py
"""
import os
import sys
import threading

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.daily_quest import (  # noqa: E402
    DailyQuestTask, _SELECT_RB_MIN, _TAB2_DAILY_X, _TAB2_BORDER_Y,
    _TAB2_GUILD_X, _TAB2_DAILY, _TAB1_DAILY, _TAB_TITLE_DAILY,
    _TASK_PAGE_TABS)
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY  # noqa: E402

_PASS, _FAIL = [], []

_CW, _CH = 1280, 1020
_BASE = (245, 245, 242)          # 未选中标签底色（实测 238,233,221 同族）
_ORANGE = (230, 134, 43)         # 选中态橙色边框（实测峰值 230,134,43）
_DILUTED = (240, 201, 189)       # 边框边缘被白底稀释的行（实测 y=858：R−B=51）


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def make_screen(daily_orange: bool, guild_orange: bool):
    """按真机配色渲染一帧：在选中标签的上边框位置画橙色条。"""
    arr = np.full((_CH, _CW, 3), _BASE, dtype=np.uint8)
    cy = int(_TAB2_BORDER_Y * _CH)
    for rx, on in ((_TAB2_DAILY_X, daily_orange),
                   (_TAB2_GUILD_X, guild_orange)):
        if not on:
            continue
        cx = int(rx * _CW)
        arr[cy - 4:cy + 5, cx - 18:cx + 18] = _ORANGE
    return Image.fromarray(arr)


class MockBot:
    """title=None → 模拟「标题读不到」，用于验证取色兜底。"""

    def __init__(self, daily_orange=True, guild_orange=False, no_screen=False,
                 title=_TAB_TITLE_DAILY, title_after=None,
                 switch_after_click=True, subtabs=True):
        self.daily = daily_orange
        self.guild = guild_orange
        self.no_screen = no_screen
        self.title = title
        self.title_after = title_after if title_after is not None else title
        self.switch_after_click = switch_after_click
        self.subtabs = subtabs
        self.clicks = []

    def screen_image(self):
        if self.no_screen:
            raise RuntimeError('no screen')
        return (make_screen(self.daily, self.guild), 0, 0, _CW, _CH)

    def _ocr_screen(self, img=None, no_cache=False):
        items = []
        if self.title is not None:
            # 真机位置：标题「每日任务」@ 图像 (207.5, 29.8)
            items.append((30, 208, self.title, 0.99))
        if self.subtabs:
            # v2.1.26：二级标签行（「日常任务」下必然存在）——取色兜底的前提
            items += [(882, 414, '每日任务', 0.99, 0, 0),
                      (886, 924, '公会任务', 0.99, 0, 0)]
        return items

    def click_ratio(self, rx, ry, label=''):
        self.clicks.append(label)
        if '二级' in label:
            self.title = self.title_after          # 切换生效 → 标题跟随
            if self.switch_after_click:
                self.daily, self.guild = True, False

    def find_text(self, items, kw, min_conf=0.5):
        return None


def mk(bot, logs=None):
    se, pe = threading.Event(), threading.Event()
    pe.set()
    return DailyQuestTask({
        'bot': bot, 'logger': (logs.append if logs is not None
                               else (lambda m: None)),
        'stats': {}, 'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'daily_quest#tab',
    })


print('══ 每日任务两级标签检测（v2.1.25）══')
print()

# ── A. 主判据：左上角标题 ──
print('── A. 主判据：左上角标题（用户 18:24）──')
check('A1 标题=「每日任务」→ 判定已在目标标签',
      mk(MockBot(title=_TAB_TITLE_DAILY))._on_daily_quest_tab() is True, '')
check('A2 标题=「翻翻乐」（其它一级标签）→ 判定不在',
      mk(MockBot(title='翻翻乐'))._on_daily_quest_tab() is False, '')
check('A3 标题=「公会任务」（同页二级兄弟）→ 判定不在',
      mk(MockBot(title='公会任务'))._on_daily_quest_tab() is False, '')
check('A4 标题=「成长挑战」→ 判定不在',
      mk(MockBot(title='成长挑战'))._on_daily_quest_tab() is False, '')
_b = MockBot(title='翻翻乐')
check('A5 标题提取只认左上角区域（不会误取底部标签）',
      mk(_b)._daily_tab_title([(1006, 500, '日常任务', 0.99),
                               (30, 208, '翻翻乐', 0.99)]) == '翻翻乐',
      '')
print()

# ── B. 兜底判据：二级标签上边框取色（标题读不到时）──
print('── B. 兜底：取色（标题读不到）──')
check('B1 每日任务=橙、公会任务=灰 → 已在目标',
      mk(MockBot(daily_orange=True, guild_orange=False,
                 title=None))._on_daily_quest_tab() is True, '')
check('B2 每日任务=灰、公会任务=橙 → 不在目标',
      mk(MockBot(daily_orange=False, guild_orange=True,
                 title=None))._on_daily_quest_tab() is False, '')
check('B3 两个都灰（二级行不存在）→ 不在目标',
      mk(MockBot(daily_orange=False, guild_orange=False,
                 title=None))._on_daily_quest_tab() is False, '')
check(f'B4 阈值 {_SELECT_RB_MIN:.0f} 位于 实测选中 187 与 未选中 17 之间',
      17 < _SELECT_RB_MIN < 187, f'{_SELECT_RB_MIN}')

_arr = np.full((_CH, _CW, 3), _BASE, dtype=np.uint8)
_cy = int(_TAB2_BORDER_Y * _CH)
_cx = int(_TAB2_DAILY_X * _CW)
_arr[_cy - 4:_cy + 5, _cx - 18:_cx + 18] = _DILUTED


class _DilutedBot(MockBot):
    def screen_image(self):
        return (Image.fromarray(_arr), 0, 0, _CW, _CH)


check('B5 【证伪】只有被稀释的边缘色（R−B=51）→ 不算选中',
      mk(_DilutedBot(title=None))._tab_selected(_TAB2_DAILY_X) is False,
      '51 < 阈值 → 不误判')
print()

# ── C. 已在目标标签 → 零点击（检测的意义）──
print('── C. 已在「日常任务 → 每日任务」→ 零点击 ──')
logs_c = []
_bc = MockBot(title=_TAB_TITLE_DAILY)
_tc = mk(_bc, logs_c)
check('C1 返回 True 且**未发生任何点击**',
      _tc._ensure_daily_tab() is True and _bc.clicks == [],
      f'clicks={_bc.clicks}')
check('C2 日志含左上角标题', any('左上角标题=每日任务' in m for m in logs_c),
      str([m for m in logs_c if '标签' in m][:1]))
print()

# ── D. 不在目标标签 → 先点一级「日常任务」、再点二级「每日任务」──
print('── D. 不在目标标签 → 依次点两级 ──')
logs_d = []
_bd = MockBot(title='翻翻乐', title_after=_TAB_TITLE_DAILY)
_td = mk(_bd, logs_d)
_rd = _td._ensure_daily_tab()
check('D1 点击顺序 = 一级「日常任务」→ 二级「每日任务」',
      _bd.clicks == ['每日任务_一级_日常任务', '每日任务_二级_每日任务'],
      str(_bd.clicks))
check('D2 切换后标题变「每日任务」→ 返回 True', _rd is True, repr(_rd))
check('D3 日志记录了当前标题与"依次点"',
      any('依次点' in m and '翻翻乐' in m for m in logs_d),
      str([m for m in logs_d if '依次点' in m][:1]))

# 同页二级兄弟（公会任务）也要切
logs_d2 = []
_bd2 = MockBot(title='公会任务', title_after=_TAB_TITLE_DAILY)
_td2 = mk(_bd2, logs_d2)
check('D4 在「公会任务」（同页二级兄弟）→ 同样走两级切换并成功',
      _td2._ensure_daily_tab() is True
      and _bd2.clicks == ['每日任务_一级_日常任务', '每日任务_二级_每日任务'],
      str(_bd2.clicks))

# 标题读不到 + 取色显示未选中 → 也要切
logs_d3 = []
_bd3 = MockBot(title=None, daily_orange=False, guild_orange=True,
               title_after=_TAB_TITLE_DAILY)
_td3 = mk(_bd3, logs_d3)
check('D5 标题读不到但取色显示未选中 → 仍走两级切换',
      _td3._ensure_daily_tab() is True
      and _bd3.clicks == ['每日任务_一级_日常任务', '每日任务_二级_每日任务'],
      str(_bd3.clicks))

check('D6 点击点用比例坐标且都在屏幕内',
      (0 < _TAB1_DAILY[0] < 1) and (0 < _TAB1_DAILY[1] < 1)
      and (0 < _TAB2_DAILY[0] < 1) and (0 < _TAB2_DAILY[1] < 1),
      f'一级={_TAB1_DAILY} 二级={_TAB2_DAILY}')
print()

# ── E. 异常兜底：取不到截图 + 标题读不到 → 不崩、仍尝试切换 ──
print('── E. 取不到截图 + 标题读不到 ──')
logs_e = []
_be = MockBot(no_screen=True, title=None)
_te = mk(_be, logs_e)
_re = _te._ensure_daily_tab()
check('E1 不抛异常，返回 False', _re is False, repr(_re))
check('E2 仍尝试点了两级（不因判不出就放弃）',
      _be.clicks == ['每日任务_一级_日常任务', '每日任务_二级_每日任务'],
      str(_be.clicks))
check('E3 有警告日志说明标题/选中态都读不到',
      any('未读到' in m for m in logs_e),
      str([m for m in logs_e if '未读到' in m][:1]))
print()

# ── F. 静态：run() 里已接入 ──
print('── F. 静态检查 ──')
_src = open(os.path.join(ROOT, 'tasks', 'daily_quest.py'),
            encoding='utf-8').read()
check('F1 run() 在 _claim_flow 之前调用 _ensure_daily_tab',
      _src.index('self._ensure_daily_tab()')
      < _src.index('return self._claim_flow()'))
check('F2 标题区域限定在左上角（ry/rx 双约束）',
      '_TITLE_REGION_RY' in _src and '_TITLE_REGION_RX' in _src)
check('F3 二级标签行只在「日常任务」下出现的前提写进注释',
      '只在「日常任务」下出现' in _src)
print()

# ── G. 页面判据「任务界面」（v2.1.26：任意一级标签都认）──
# 真机依据：用户 8 张标签截图（公会任务/每日任务/翻翻乐/成长挑战/赛季任务×3/
# 家园目标）**全部**读到 5/5 一级标签 → 该判据与"选中哪个标签"无关。
print('── G. 页面判据：任务界面（任意标签），v2.1.26 ──')
_g = mk(MockBot())
_TABROW = [(1006, 500 + i * 93, t, 0.99, 0, 0)
           for i, t in enumerate(_TASK_PAGE_TABS)]
for _n in (5, 4, 3):
    check(f'G1 一级标签命中 {_n}/5 → 认定已在任务界面',
          _g._on_task_page(_TABROW[:_n]) is True, '')
check('G2 只命中 2/5 → 不认定（不误判成任务界面）',
      _g._on_task_page(_TABROW[:2]) is False, '')
check('G3 空帧 → False', _g._on_task_page([]) is False, '')
check('G4 【核心】`_on_daily_page` 已放宽：停在**任意**一级标签都算到达',
      _g._on_daily_page(_TABROW) is True
      and _g._on_daily_page(
          [(30, 208, '翻翻乐', 0.99, 0, 0)] + _TABROW) is True
      and _g._on_daily_page(
          # 赛季页标题是**当前赛季名**（用户 19:41：本次为「王都终幕」，
          # 下赛季会变）——故这里用任意非「每日任务」字样，不做固定样本
          [(30, 208, '某赛季页标题', 0.99, 0, 0)] + _TABROW) is True,
      '旧判据要求标题=每日任务 → 在翻翻乐/赛季任务下会判否')
check('G5 strip 容错：标签带尾/首空格仍命中',
      _g._on_task_page([(1006, 500 + i * 93, f' {t} ', 0.99, 0, 0)
                        for i, t in enumerate(_TASK_PAGE_TABS[:3])]) is True, '')
check('G6 注册表已登记 nav.task_page（此前完全未注册 → 判 unknown）',
      GLOBAL_SCREEN_REGISTRY.detect(_TABROW) == 'nav.task_page',
      f"detect={GLOBAL_SCREEN_REGISTRY.detect(_TABROW)}")
check('G7 注册表不误伤：只有 2 个标签 → 仍为 unknown',
      GLOBAL_SCREEN_REGISTRY.detect(_TABROW[:2]) == 'unknown', '')
print()

# ── H. 标题提取规则（v2.1.26：取最上方 + 排除副标题行）──
print('── H. 左上角标题提取规则 ──')
_h = mk(MockBot(title=None))
check('H1 取**最上方**（不是最左）：标题在上、副标题在下 → 取标题',
      _h._daily_tab_title([
          (60, 120, '刷新时间：2天12小时', 0.99, 0, 0),   # 副标题（更靠左更靠下）
          (30, 208, '每日任务', 0.99, 0, 0),
      ]) == '每日任务', '')
check('H2 排除「刷新时间/结算/小时」副标题行',
      _h._daily_tab_title([
          (30, 100, '刷新时间：2天12小时', 0.99, 0, 0),
          (60, 208, '翻翻乐', 0.99, 0, 0),
      ]) == '翻翻乐', '')
check('H3 右侧文字不算标题（rx>0.35）',
      _h._daily_tab_title([(30, 1070, '刷新时间：12:16:21', 0.99, 0, 0)]) is None, '')
check('H4 ry 上限 0.12（成长挑战/家园目标 标题更低，0.08 会漏）',
      _h._daily_tab_title([(120, 208, '家园目标', 0.99, 0, 0)]) == '家园目标',
      '120/1024=0.117 ≤ 0.12')
check('H6 【防线】标题读不到 + 二级标签行不存在 → 判否（哪怕该点取色是橙）',
      mk(MockBot(title=None, daily_orange=True))._on_daily_quest_tab(
          [(1006, 500, '日常任务', 0.99, 0, 0)]) is False,
      '真机 ⑤赛季任务页：该位置也有橙色子标签 → 不设防会误判成每日任务')
check('H7 标题读不到 + 二级标签行存在 + 该点橙色 → 仍能兜底判为「在」',
      mk(MockBot(title=None, daily_orange=True))._on_daily_quest_tab([
          (1006, 500, '日常任务', 0.99, 0, 0),
          (882, 414, '每日任务', 0.99, 0, 0),
          (886, 924, '公会任务', 0.99, 0, 0)]) is True, '')
check('H5 OCR 尾巴容错：标题读成「每日任务0」→ 仍判在目标标签',
      mk(MockBot(title='每日任务0'))._on_daily_quest_tab() is True, '')
print()

print('═' * 56)
print(f'每日任务两级标签：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

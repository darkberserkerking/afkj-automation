# -*- coding: utf-8 -*-
"""竞技场任务干跑测试（v1.9.31）：状态机全链路 Mock。

链路：导航→玩法目录点卡片→主界面(票3/1)→开始挑战→选择对手
（排除公会/好友列→点中间双剑）→备战(F)→战斗中→结算关闭→
回主界面(票0/1)→done
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time   # noqa: F401  虚拟时钟（sleep 零等待）
import tasks.global_nav as gnav_mod
from task_registry import TaskRegistry
from tasks.base import BaseTask
from tasks.catalog import ArenaTask  # noqa: F401  触发注册

_PASS = []
_FAIL = []


def check(name, cond, detail=''):
    if cond:
        _PASS.append(name)
        print(f'[PASS] {name}  {detail}'.rstrip())
    else:
        _FAIL.append(name)
        print(f'[FAIL] {name}  {detail}')


class _FakeNav:
    """全局导航桩：直接导航到玩法目录成功。"""

    def __init__(self, bot, logger=None, stop_check=None):
        self._log = logger or (lambda m: None)

    def navigate_to(self, target, arrived_cb=None):
        self._log('[信息] 全局导航 → 玩法目录')
        return True


class _ArenaBot:
    """帧序列桩：_ocr_screen 依序返回；template_match/find_hits 桩。"""

    def __init__(self, frames, stop_after, stop_ev, find_hits=None):
        self._frames = list(frames)
        self._fi = -1
        self._ocr_n = 0
        self._stop_after = stop_after
        self._stop_ev = stop_ev
        self.find_hits = find_hits or {}
        self.clicks = []
        self.calls = []
        self.keys = []

    def _ocr_screen(self, img=None, no_cache=False):
        self._ocr_n += 1
        if self._ocr_n >= self._stop_after:
            self._stop_ev.set()
        self._fi = min(self._fi + 1, len(self._frames) - 1)
        return self._frames[self._fi]

    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.calls.append(f'template_match:{name}')
        if 'arena_challenge_icon' in name:
            return (650, 650)   # 中间列双剑
        return None

    def find_text(self, items, kw, min_conf=0.6, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return self.find_hits.get(kw)

    def click_at(self, x, y, label=''):
        self.clicks.append(label)
        return True

    def _click_abs(self, x, y):
        self.clicks.append(f'_click_abs:({x},{y})')
        return True

    def press_f(self, label=''):
        self.keys.append(label)
        return True

    def screen_image(self):
        import numpy as np
        from PIL import Image
        return (Image.new('RGB', (1280, 1024), (30, 30, 30)),
                0, 0, 1280, 1024)

    def dismiss_blink_popup(self, items=None):
        # 结算页帧含「点击空白处关闭」→ 点空白关闭
        for it in (items or []):
            if len(it) >= 4 and '点击空白处关闭' in it[2]:
                self.clicks.append('结算_点空白关闭')
                return True
        return False


def _make_task(bot):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    logs = []
    t = ArenaTask({
        'bot': bot,
        'logger': lambda m: logs.append(m),
        'stats': {},
        'stop_event': threading.Event(),   # 永不外部停止
        'pause_event': pause_ev,
    })
    t._stop = stop_ev
    return t, logs, stop_ev


# ── 帧（items 元组 = (y, x, text, conf)）─────────────────────
f_playbook = [(300, 400, '常规玩法', 0.97), (500, 400, '活动', 0.95),
              (400, 540, '竞技场', 0.93)]
f_main3 = [(34, 77, '竞技场', 0.97),
           (948, 738, '开始挑战', 0.99),
           (864, 534, '殿堂斗士', 0.95),
           (858, 289, '防守阵容', 0.97),
           (978, 738, '3/1', 0.97)]
f_cand = [(81, 637, '选择对手', 0.99),
          (552, 450, '本公会成员', 0.91),
          (488, 824, '好友', 0.97),
          (580, 651, 'Gladiass', 0.95),
          (979, 868, '重置7/7', 0.98)]
f_prep = [(948, 692, '战斗', 0.99), (600, 1122, '编队', 0.97),
          (36, 650, '竞技场', 0.98)]
f_battle = [(300, 400, '160级', 0.9)]
f_result = [(196, 626, '战斗胜利', 0.97),
            (945, 639, '点击空白处关闭', 1.0),
            (900, 692, '44200', 0.98)]
f_main0 = [(34, 77, '竞技场', 0.97),
           (948, 738, '开始挑战', 0.99),
           (858, 289, '防守阵容', 0.97),
           (978, 738, '0/1', 0.97)]

frames = (
    [f_playbook                       # probe=clear → 导航后点卡片
     ] + [f_playbook] * 2
    + [f_main3]                       # 主界面 票3
    + [f_cand] * 2                    # 选择对手 → 点双剑
    + [f_prep, f_prep]                # 备战 → F
    + [f_battle, f_battle, f_battle]  # 战斗中
    + [f_result, f_result]            # 结算 → 关闭
    + [f_main0, f_main0]              # 主界面 票0 → done
)

print('── 竞技场干跑：全链路 ──')
_stop = threading.Event()
bot = _ArenaBot(frames, stop_after=60, stop_ev=_stop,
                find_hits={'开始挑战': (738, 948), '重置': (868, 979)})
gnav_mod.GlobalNavigator = _FakeNav
try:
    t, logs, _ = _make_task(bot)
    r = t.run()
finally:
    gnav_mod.GlobalNavigator = _FakeNav

check('1 全链路执行至票尽 done', r == 'done', f'result={r}')
check('2 点击竞技场卡片', '玩法目录_竞技场卡片' in bot.clicks, str(bot.clicks))
check('3 点击开始挑战', '竞技场_开始挑战' in bot.clicks, str(bot.clicks))
check('4 双剑模板匹配并点击中间列',
      'template_match:arena_challenge_icon.png' in bot.calls
      and '_click_abs:(650,650)' in bot.clicks,
      str([c for c in bot.calls if 'arena' in c][:1])
      + str([c for c in bot.clicks if '_click_abs' in c][:1]))
check('5 备战页输入 F', len(bot.keys) >= 1, str(bot.keys))
check('6 结算页点空白关闭',
      any('点空白' in c for c in bot.clicks), str(bot.clicks[-2:]))
check('7 票尽日志', any('票数耗尽' in m for m in logs),
      str([m for m in logs if '票' in m or '耗尽' in m][:2]))

def _make_task_bl(bot, options):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    logs = []
    t = ArenaTask({
        'bot': bot,
        'logger': lambda m: logs.append(m),
        'stats': {},
        'stop_event': threading.Event(),
        'pause_event': pause_ev,
        'options': options,
    })
    t._stop = stop_ev
    return t, logs, stop_ev


# ── 8 v1.9.32：挑战黑名单（中间列名字命中 → 全排除 → 重置）──
print('── 8. 挑战黑名单（v1.9.32）──')
_stop8 = threading.Event()
_bl_bot = _ArenaBot(
    [f_cand] * 10, stop_after=25, stop_ev=_stop8,
    find_hits={'重置': (868, 979)})
# 中间列对手名 Gladiass 在黑名单
gnav_mod.GlobalNavigator = _FakeNav
try:
    t8, logs8, _ = _make_task_bl(_bl_bot, {'blacklist': 'Gladiass;测试号'})
    r8 = t8.run()
finally:
    gnav_mod.GlobalNavigator = _FakeNav
check('8a 黑名单命中 → 全排除 → 点重置',
      r8 == 'done' and '竞技场_重置' in _bl_bot.clicks,
      f'result={r8} ' + str([c for c in _bl_bot.clicks if '重置' in c][:1]))
check('8b 日志显示排除列含 1（中间列）',
      any('排除=[0, 1, 2]' in m or '排除=[0, 1, 2]' in m for m in logs8),
      str([m for m in logs8 if '排除' in m][:1]))
check('8c 重置用尽（7 次）→ done',
      any('重置用尽' in m for m in logs8), str(logs8[-2:]))

print(f'══ 竞技场干跑测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f in _FAIL:
        print(f'  {f}')
sys.exit(0 if not _FAIL else 1)

# -*- coding: utf-8 -*-
"""迷梦之域任务干跑测试（v1.9.66）：卡片→挑战→F→结算→循环至次数耗尽。"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time  # noqa: F401  虚拟时钟
import tasks.global_nav as gnav_mod
from tasks.catalog import DreamRealmTask  # noqa: F401  触发注册

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


class _FakeNav:
    def __init__(self, bot, logger=None, stop_check=None):
        self._log = logger or (lambda m: None)

    def navigate_to(self, target, arrived_cb=None):
        self._log(f"[信息] 全局导航 → 目标「{target}」（1 层）")
        return True


def _it(y, x, text, conf=0.95):
    return (y, x, text, conf)


f_playbook = [_it(30, 200, '常规玩法'), _it(500, 300, '迷梦之域'),
              _it(500, 700, '异界迷宫')]
f_main10 = [_it(47, 206, '沼泽之主'), _it(944, 688, '挑战'),
            _it(974, 686, '次数10/10'), _it(120, 204, '阵容推荐')]
f_main1 = [_it(47, 206, '沼泽之主'), _it(944, 688, '挑战'),
           _it(974, 686, '次数1/10')]
f_main0 = [_it(47, 206, '沼泽之主'), _it(944, 688, '挑战'),
           _it(974, 686, '次数0/10')]
f_ready = [_it(975, 287, '阵容推荐'), _it(946, 740, '战斗'),
           _it(60, 647, '-精英挑战-')]
f_result = [_it(938, 640, '点击空白处关闭'), _it(196, 626, '战斗结束')]
f_battle = [_it(100, 300, '885级'), _it(900, 1200, '自动')]


class _DreamBot:
    def __init__(self, frames):
        self._frames = list(frames)
        self._i = -1
        self.clicks = []
        self.calls = []

    def _ocr_screen(self, img=None, no_cache=False):
        self._i = min(self._i + 1, len(self._frames) - 1)
        return self._frames[self._i]

    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.calls.append(f'template_match:{name}')
        if 'skip' in name.lower() and '887级' in str(self._frames[
                min(self._i + 1, len(self._frames) - 1)]):
            return (1100, 900)
        return None

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, x, y, label=''):
        self.clicks.append(label)
        return True

    def _click_abs(self, x, y):
        self.clicks.append(f'_click_abs:({x},{y})')
        return True

    def click_ratio(self, rx, ry, label=''):
        self.clicks.append(label)
        return True

    def drag_abs(self, *a, **kw):
        self.clicks.append('drag')
        return True

    def press_f(self, label=''):
        self.clicks.append(label)
        return True

    def dismiss_blink_popup(self, items=None):
        return False


def make_task(bot, options=None):
    logs = []
    paus = threading.Event()
    paus.set()
    t = DreamRealmTask({
        'bot': bot, 'logger': lambda m: logs.append(m), 'stats': {},
        'stop_event': threading.Event(), 'pause_event': paus,
        'max_time': None, 'options': options or {},
    })
    t._stop = threading.Event()
    return t, logs


def run_case(name, frames, checks, options=None):
    print(f'── {name} ──')
    gnav_mod.GlobalNavigator = _FakeNav
    bot = _DreamBot(frames)
    t, logs = make_task(bot, options)
    r = t.run()
    for cname, cond, detail in checks:
        check(cname, cond(r, bot, logs),
              detail(r, bot, logs) if callable(detail) else detail)
    return bot, logs


print('── 1. 玩法目录入口 → 主界面 → 挑战 → F → 结算 → 次数耗尽 ──')
frames1 = [f_playbook, f_playbook, f_main1, f_main1, f_ready, f_ready,
           f_battle, f_result, f_result, f_main0, f_main0]
run_case('1. 全链路（1 次挑战后耗尽）', frames1,
         [
             ('1a done', lambda r, b, l: r == 'done',
              lambda r, b, l: f'result={r}'),
             ('1b 点了卡片', lambda r, b, l: '迷梦_卡片' in b.clicks,
              lambda r, b, l: str(b.clicks[:4])),
             ('1c 点了挑战', lambda r, b, l: '迷梦_挑战' in b.clicks,
              lambda r, b, l: str(b.clicks[:6])),
             ('1d 按了 F 开战', lambda r, b, l: '迷梦_备战开战' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '备战' in c][:1])),
             ('1e 结算点空白关闭', lambda r, b, l: '迷梦_结算关闭' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '结算' in c][:1])),
             ('1f 未勾选 → 一轮后完成（v1.9.66）',
              lambda r, b, l: any('未勾选重复挑战' in m for m in l),
              lambda r, b, l: str([m for m in l if '未勾选' in m][:1])),
         ])

print('── 2. 启动即在主界面 0 次 → 直接 done ──')
run_case('2. 0 次直接完成', [f_main0, f_main0],
         [
             ('2a done', lambda r, b, l: r == 'done',
              lambda r, b, l: f'result={r}'),
             ('2b 未点挑战', lambda r, b, l: '迷梦_挑战' not in b.clicks,
              lambda r, b, l: str(b.clicks)),
         ])

print('── 3. 启动即在结算页 → 关闭后继续 ──')
run_case('3. 结算页启动', [f_result, f_result, f_main0, f_main0],
         [
             ('3a done', lambda r, b, l: r == 'done',
              lambda r, b, l: f'result={r}'),
             ('3b 先关结算', lambda r, b, l: '迷梦_结算关闭' in b.clicks,
              lambda r, b, l: str(b.clicks[:2])),
         ])

print('── 3bis. 备战页有「跳过战斗」→ 优先点它（v2.1.2，当天已挑战过）──')
f_ready_skip = [_it(975, 287, '阵容推荐'), _it(946, 740, '战斗'),
                _it(955, 430, '跳过战斗'), _it(60, 647, '-精英挑战-')]
run_case('3bis. 跳过战斗优先', [f_main1, f_main1, f_ready_skip, f_ready_skip,
                              f_result, f_result],
         [
             ('3bis-a done', lambda r, b, l: r == 'done',
              lambda r, b, l: f'result={r}'),
             ('3bis-b 点了「跳过战斗」', lambda r, b, l: '迷梦_跳过战斗' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '跳过' in c][:1])),
             ('3bis-c 未按 F 开战', lambda r, b, l: '迷梦_备战开战' not in b.clicks,
              lambda r, b, l: str(b.clicks)),
         ])

print('── 4. 勾选「重复挑战」→ 循环至次数耗尽（v1.9.66）──')
frames4 = [f_main10, f_main10, f_ready, f_ready, f_battle, f_result, f_result,
           f_main1, f_main1, f_ready, f_ready, f_battle, f_result, f_result,
           f_main0, f_main0]
run_case('4. 勾选循环 2 轮至耗尽', frames4, [
    ('4a done', lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
    ('4b 两次挑战（2 次 F）',
     lambda r, b, l: sum(1 for c in b.clicks if '备战开战' in c) == 2,
     lambda r, b, l: str(sum(1 for c in b.clicks if '备战开战' in c))),
    ('4c 日志含次数耗尽', lambda r, b, l: any('次数耗尽' in m for m in l),
     lambda r, b, l: str([m for m in l if '耗尽' in m][:1])),
], options={'loop': True})

print(f'══ 迷梦之域干跑：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
sys.exit(0 if not _FAIL else 1)

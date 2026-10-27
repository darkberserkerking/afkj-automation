# -*- coding: utf-8 -*-
"""赠送友情点任务干跑测试（v1.9.62）：主菜单→社交→一键赠礼并收取。"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time  # noqa: F401  虚拟时钟
import tasks.global_nav as gnav_mod
from tasks.catalog import FriendGiftTask  # noqa: F401  触发注册

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


f_home = [_it(1008, 521, '神秘屋'), _it(1008, 607, '玩法目录'),
          _it(1008, 680, '英雄厅堂'), _it(746, 44, '建设')]
f_friend = [_it(112, 264, '收礼进度3/10'), _it(1006, 530, '好友'),
            _it(1006, 640, '公会成员'), _it(880, 640, '一键赠礼并收取')]
f_friend_done = [_it(112, 264, '收礼进度10/10'), _it(1006, 530, '好友')]
# v1.9.63：完成帧 → 直接 done 不点击


class _GiftBot:
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
        if 'btn_menu_l0' in name:
            return (60, 1170)
        if 'btn_menu_l1' in name:
            return (60, 1180)
        if 'btn_social' in name:
            return (400, 700)
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

    def dismiss_blink_popup(self, items=None):
        return False


def make_task(bot):
    logs = []
    paus = threading.Event()
    paus.set()
    t = FriendGiftTask({
        'bot': bot, 'logger': lambda m: logs.append(m), 'stats': {},
        'stop_event': threading.Event(), 'pause_event': paus,
        'max_time': None,
    })
    t._stop = threading.Event()
    return t, logs


def run_case(name, frames, checks):
    print(f'── {name} ──')
    gnav_mod.GlobalNavigator = _FakeNav
    bot = _GiftBot(frames)
    t, logs = make_task(bot)
    r = t.run()
    for cname, cond, detail in checks:
        check(cname, cond(r, bot, logs),
              detail(r, bot, logs) if callable(detail) else detail)
    return bot, logs


print('── 1. 0 层启动 → 主菜单 → 社交 → 赠礼 ──')
run_case('1. 全链路',
         [f_home, f_home, f_home, f_friend, f_friend, f_friend, f_friend,
          f_friend_done, f_friend_done],
         [
             ('1a done', lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
             ('1b 点了主菜单按钮', lambda r, b, l: '_click_abs:(60,1170)' in b.clicks,
              lambda r, b, l: str(b.clicks[:4])),
             ('1c 点了「社交」', lambda r, b, l: '_click_abs:(400,700)' in b.clicks,
              lambda r, b, l: str(b.clicks[:6])),
             ('1d 点了「一键赠礼并收取」',
              lambda r, b, l: '友情点_一键赠礼并收取' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '赠礼' in c][:1])),
             ('1e 日志含完毕', lambda r, b, l: any('完毕' in m for m in l),
              lambda r, b, l: str([m for m in l if '完毕' in m][:1])),
         ])

print('── 2. 已在好友界面启动 → 直通 ──')
run_case('2. 直通',
         [f_friend, f_friend, f_friend_done, f_friend_done],
         [
             ('2a done', lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
             ('2b 无导航绕路',
              lambda r, b, l: not any('全局导航' in m for m in l),
              lambda r, b, l: str([m for m in l if '导航' in m][:1])),
         ])

print('── 3. 按钮常驻 → 2 次上限 ──')
run_case('3. 上限', [f_friend] * 30,
         [
             ('3a done', lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
             ('3b 恰好 2 次点击',
              lambda r, b, l: sum(1 for c in b.clicks if '赠礼' in c) == 2,
              lambda r, b, l: str(sum(1 for c in b.clicks if '赠礼' in c))),
         ])

print('── 4. 收礼进度 10/10 → 当日已完成直接 done（v1.9.63）──')
run_case('4. 已完成',
         [f_friend_done, f_friend_done],
         [
             ('4a done 且未点击赠礼',
              lambda r, b, l: r == 'done' and not any('赠礼' in c for c in b.clicks),
              lambda r, b, l: f'result={r} clicks={b.clicks}'),
             ('4b 日志含当日已完成',
              lambda r, b, l: any('当日已完成' in m for m in l),
              lambda r, b, l: str([m for m in l if '已完成' in m][:1])),
         ])

print(f'══ 赠送友情点干跑：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
sys.exit(0 if not _FAIL else 1)

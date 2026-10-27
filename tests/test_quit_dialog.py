# -*- coding: utf-8 -*-
"""退出确认弹窗（ESC 陷阱）最高优先级处理测试（v2.0.4）。"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time  # noqa: F401  虚拟时钟
from tasks.base import BaseTask

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _it(y, x, text, conf=0.95):
    return (y, x, text, conf)


class _Bot:
    def __init__(self, frames, tpl_pos=None):
        self._frames = list(frames)
        self._i = -1
        self._tpl_pos = tpl_pos
        self.clicks = []
        self.tpl_calls = []

    def _ocr_screen(self, img=None, no_cache=False):
        self._i = min(self._i + 1, len(self._frames) - 1)
        return self._frames[self._i]

    def template_match(self, tpl, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        self.tpl_calls.append(os.path.basename(str(tpl)))
        if self._tpl_pos and "btn_confirm_cancel" in str(tpl):
            return self._tpl_pos
        return None

    def _click_abs(self, x, y):
        self.clicks.append(f"_click_abs:({x},{y})")
        return True

    def click_at(self, x, y, label=""):
        self.clicks.append(label)
        return True

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None


class _Task(BaseTask):
    task_id = "t"
    label = "t"

    def run(self):
        return "done"


def make(bot):
    logs = []
    ev = threading.Event()
    ev.set()
    t = _Task({"bot": bot, "logger": lambda m: logs.append(m),
               "stats": {}, "stop_event": threading.Event(),
               "pause_event": ev, "max_time": None})
    return t, logs


f_quit = [_it(154, 638, "确定要退出游戏吗？")]
f_normal = [_it(1008, 521, "神秘屋"), _it(1008, 607, "玩法目录")]
f_quit_anchor = [_it(515, 638, "确定要退出游戏吗？")]

print("── 1. 模板命中 → 点 X（最高优先级）──")
b1 = _Bot([f_quit], tpl_pos=(600, 780))
t1, l1 = make(b1)
r1 = t1._dismiss_quit_dialog(f_quit)
b1._ocr_screen()
check("1a 返回 True（已处理）", r1 is True, str(r1))
check("1b 模板命中并点击 X", "_click_abs:(600,780)" in b1.clicks,
      str(b1.clicks))
check("1c 日志含「退出确认弹窗」", any("退出确认弹窗" in m for m in l1),
      str([m for m in l1 if "退出" in m][:1]))

print("── 2. 模板未命中 → 锚点兜底（文字左下方）──")
b2 = _Bot([f_quit_anchor])
t2, l2 = make(b2)
r2 = t2._dismiss_quit_dialog(f_quit_anchor)
check("2a 返回 True", r2 is True, str(r2))
check("2b 锚点兜底点击（x=文字x-338, y=文字y+171）", "退出确认_X" in b2.clicks,
      str(b2.clicks))

print("── 3. 无弹窗 → 不动作 ──")
b3 = _Bot([f_normal])
t3, l3 = make(b3)
r3 = t3._dismiss_quit_dialog(f_normal)
check("3a 返回 False", r3 is False, str(r3))
check("3b 无任何点击", b3.clicks == [], str(b3.clicks))

print("── 4. dismiss_blink_popup 内顺带优先处理（零侵入覆盖）──")
b4 = _Bot([f_quit], tpl_pos=(600, 780))
t4, l4 = make(b4)
r4 = t4.dismiss_blink_popup(f_quit)
check("4a 返回 True 且已点 X", r4 is True and any("_click_abs" in c for c in b4.clicks),
      str(b4.clicks))

print("── 5. 画面注册与优先级 ──")
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY, ZH_SCREEN
d = GLOBAL_SCREEN_REGISTRY.detect(f_quit)
check("5a 退出弹窗画面已注册（nav.quit_confirm）", d == "nav.quit_confirm", str(d))
check("5b 中文名已登记", ZH_SCREEN.get("nav.quit_confirm") == "退出确认弹窗",
      str(ZH_SCREEN.get("nav.quit_confirm")))
d2 = GLOBAL_SCREEN_REGISTRY.detect([_it(862, 60, "正在加载游戏。")])
check("5c 加载界面已注册（nav.loading）", d2 == "nav.loading", str(d2))
check("5d 加载界面中文名", ZH_SCREEN.get("nav.loading") == "加载界面",
      str(ZH_SCREEN.get("nav.loading")))

print(f"══ 退出确认弹窗测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══")
if _FAIL:
    print("失败项：")
    for f_ in _FAIL:
        print(" ", f_)
sys.exit(0 if not _FAIL else 1)
# -*- coding: utf-8 -*-
"""v2.4.4：启动任务冷启动句柄绑定回归（真机 2026-11-07 10:03 卡死修复）。

真机现象：源码版 + exe 版（v2.4.1）同样「仍在加载界面」刷满 120s 失败；
bug 报告现场 shot.png 画面完好（标题屏「点击开始游戏」清晰可见）。

根因：App 启动时游戏未运行 → ScreenCapture._hwnd=None（无人绑定）；
等待循环里 is_game_ready() 走标题判据 → True，而 _still_loading() →
ocr_screen → snap()：_hwnd None → activate_window 直接 return False →
bbox=(0,0,0,0) → 空图 → ocr_screen **静默返回 []** → detect([])=unknown。
其余 14 个任务都在 run() 开头 find_and_focus()，唯独启动任务没有。
v2.3.16 之前空 OCR 会被当「不是加载页」放行，坑一直被掩盖。

本测试用行为分叉锁死：
  A. 冷启动桩（_hwnd=None，OCR 在句柄绑定前静默返回空）→
     修复前 run() 必 failed（永远 loading），修复后必须 done 且绑定了句柄
  B. _still_loading 静默空帧 → 日志必须出现「句柄=未绑定」诊断
  C. 兼容：无 find_and_focus 的旧桩不崩（6a 干跑桩回归）

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_startup_bind_hwnd.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time
fast_time.install()  # 虚拟时钟：120s 等待瞬时跑完
import tasks.game_control as gc
from tasks.catalog import StartGameTask  # noqa: F401  注册

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def it(cy, cx, text, conf=0.96):
    return (cy, cx, text, conf, int(cx) - 40, int(cx) + 40)


# 真机标题屏帧（2026-11-07 截图 MaaOCR 实测：conf 0.97~0.99）
LOGIN_FRAME = [
    it(838, 641, '点击开始游戏', 0.99),
    it(413, 1242, '公告', 0.99),
    it(336, 1242, '网络诊断', 0.97),
    it(259, 1241, '账户', 0.97),
    it(105, 1242, '客服', 0.98),
]


def make_task(bot, logs):
    paus = threading.Event()
    paus.set()
    t = StartGameTask({
        'bot': bot, 'logger': lambda m: logs.append(m), 'stats': {},
        'stop_event': threading.Event(), 'pause_event': paus,
        'max_time': None, 'options': {},
    })
    t._stop = threading.Event()
    return t


class _SeqDrawer:
    """每次调用吐下一个值，耗尽后保持最后一个（每个用例独立实例！）。"""

    def __init__(self, seq):
        self._seq = list(seq)
        self._i = 0

    def __call__(self, bot=None, **kw):
        v = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return v


class _ColdStartBot:
    """冷启动真实复刻桩：
    - _hwnd 初始 None（App 启动时游戏未运行，无人绑定）
    - 句柄未绑定时 _ocr_screen **静默返回空**（复刻 ocr_screen 行为）
    - find_and_focus() 绑定句柄并计数
    """

    def __init__(self):
        self._hwnd = None
        self.bind_calls = 0
        self._ocr_calls_after_bind = 0

    def find_and_focus(self):
        self.bind_calls += 1
        self._hwnd = 6429022   # 真机 meta.json 里的句柄值
        return self._hwnd

    def _ocr_screen(self, img=None, no_cache=False):
        if self._hwnd is None:
            return []          # ← 根因复刻：未绑定 → 静默空
        # 绑定后前 2 帧仍返回空（模拟加载页：句柄已绑定但画面未就绪）
        self._ocr_calls_after_bind += 1
        if self._ocr_calls_after_bind <= 2:
            return []
        return list(LOGIN_FRAME)


class _LegacyNoBindBot:
    """旧干跑桩（无 find_and_focus / 无 _hwnd）→ 修复不得把它弄崩。"""

    def __init__(self, items):
        self._items = items

    def _ocr_screen(self, img=None, no_cache=False):
        return self._items


class _MalformedBot:
    """畸形 items 桩：诊断代码必须比主判据（detect 有 try 保护）不更脆。"""

    def _ocr_screen(self, img=None, no_cache=False):
        return [['不是元组'], (1, 2)]   # 非法元素混入


_orig = (gc.is_game_running, gc.is_game_ready, gc.launch_game,
         gc.resolve_launcher_path, gc.resolve_launch_args)

try:
    # ── A. 冷启动行为分叉（核心用例）────────────────────────
    print('── A. 冷启动：窗口出现 → 必须绑定句柄 → 就绪 ──')
    gc.is_game_running = _SeqDrawer([False, True])
    gc.is_game_ready = lambda bot=None, **kw: True
    gc.launch_game = lambda p, a: True
    gc.resolve_launcher_path = lambda: r'D:\Farlight Launcher\Launcher.exe'
    gc.resolve_launch_args = lambda: ['--envId=x']

    logs = []
    bot = _ColdStartBot()
    t = make_task(bot, logs)
    r = t.run()
    check('A1 冷启动 → done（修复前：unknown 刷满 120s 必 failed）',
          r == 'done', f'result={r!r}')
    check('A2 find_and_focus 被调用（窗口出现即绑定）',
          bot.bind_calls >= 1, f'bind_calls={bot.bind_calls}')
    check('A3 日志含「已绑定游戏窗口 hwnd=」',
          any('已绑定游戏窗口 hwnd=' in m for m in logs),
          str([m for m in logs if '绑定' in m][:2]))
    check('A4 done 前确实经历了「仍在加载」等待（绑定后加载页仍在）',
          any('仍在加载界面' in m for m in logs),
          str([m for m in logs if '加载' in m][:2]))
    check('A5 绑定后的加载等待有诊断（句柄=已绑定）',
          any('句柄=已绑定' in m and '画面未识别' in m for m in logs),
          str([m for m in logs if '诊断' in m][:2]))

    # ── B. 静默空帧诊断 ────────────────────────────────────
    print('── B. _still_loading 空帧诊断 ──')
    logs2 = []
    b2 = _ColdStartBot()          # _hwnd=None → OCR 静默空
    t2 = make_task(b2, logs2)
    r2 = t2._still_loading()
    check('B1 未绑定+空帧 → 仍判未就绪（不放过黑屏）', r2 is True, f'r={r2!r}')
    check('B2 日志出现「句柄=未绑定」诊断（不再静默刷屏）',
          any('句柄=未绑定' in m for m in logs2),
          str([m for m in logs2 if '诊断' in m][:2]))
    check('B3 诊断含 screen_id',
          any('unknown' in m or 'nav.loading' in m for m in logs2),
          str([m for m in logs2 if '诊断' in m][:1]))

    # 绑定后 + 标题屏 → 就绪且不诊断
    # （桩：绑定后前 2 帧 OCR 为空=加载页，先消耗掉，第 3 帧起是标题屏）
    logs3 = []
    b3 = _ColdStartBot()
    b3.find_and_focus()
    t3 = make_task(b3, logs3)
    _f1 = t3._still_loading()
    _f2 = t3._still_loading()
    _f3 = t3._still_loading()
    check('B4 绑定后标题屏 → 就绪（False）', _f1 is True and _f2 is True
          and _f3 is False,
          f'前两帧(加载页)={_f1},{_f2} 第三帧(标题屏)={_f3}')
    check('B5 加载页帧有诊断、标题屏帧不再打未识别诊断',
          any('画面未识别' in m for m in logs3)
          and logs3.count('diag') == 0
          and sum(1 for m in logs3 if '画面未识别' in m) <= 2,
          str([m for m in logs3 if '诊断' in m][:2]))

    # ── C. 旧桩兼容 ────────────────────────────────────────
    print('── C. 旧干跑桩兼容（无 find_and_focus 不崩）──')
    gc.is_game_running = _SeqDrawer([False, True])
    logs4 = []
    t4 = make_task(_LegacyNoBindBot([
        it(956, 622, '正在初始化游戏（5/14）。', 0.965),
    ]), logs4)
    r4 = t4.run()
    check('C1 旧桩全程加载页 → 不提前 done 且不崩',
          r4 != 'done', f'result={r4!r}')
    check('C2 旧桩日志含绑定失败警告（getattr 防御生效）',
          any('未能绑定游戏窗口' in m or '绑定游戏窗口异常' in m
              for m in logs4),
          str([m for m in logs4 if '绑定' in m][:2]))

    # C3 畸形 items：诊断代码不得比 detect 更脆（真 bug，测试抓出来的）
    logs5 = []
    t5 = make_task(_MalformedBot(), logs5)
    try:
        r5 = t5._still_loading()
        check('C3 畸形 items → 仍判未就绪且不崩', r5 is True, f'r={r5!r}')
    except Exception as _e:
        check('C3 畸形 items → 仍判未就绪且不崩', False, repr(_e))
finally:
    (gc.is_game_running, gc.is_game_ready, gc.launch_game,
     gc.resolve_launcher_path, gc.resolve_launch_args) = _orig

print('')
print(f'通过 {PASSED[0]} / {PASSED[0] + len(FAILED)}')
if FAILED:
    print('失败用例：')
    for n in FAILED:
        print(' -', n)
    sys.exit(1)
print('ALL PASS')

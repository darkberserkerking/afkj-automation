"""「挂机极限进度」确认弹窗处理 —— 定向验证（v2.2.32）。

真机截图（2026-11-04 用户提供）弹窗原文：
    「您已到达本日挂机极限进度，继续推关将不再提升本日的挂机产出速率，
      跨天后挂机速率将重新计算。是否确认继续挑战？」
    「本日极限进度：1100」
  右下角 ✕(取消) / √(继续挑战) → 本项目点 √

关键防回归：
  · 结算页是「**再次挑战**」→ **不能**被误判成这个弹窗（否则每次结算都点 √）
  · 弹窗文字 1 秒周期闪烁 → 单帧读不到时要隔 1s 再扫一帧
  · √ 模板没命中时 **不盲点**（√/✕ 相近，误点=取消挑战=白费体力）

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_afk_limit_confirm.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot.auto_farm.loop import (  # noqa: E402
    _AFK_LIMIT_CHECK_SCALE_RANGE, _AFK_LIMIT_CHECK_THRESHOLD,
    _AFK_LIMIT_CHECK_TPL, is_afk_limit_dialog)

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f'[{"PASS" if cond else "FAIL"}] {name}'
          + (f'  {detail}' if detail else ''))


# ── 真机弹窗的 OCR 文本（按截图逐行还原）──
DIALOG = [
    '您已到达本日挂机极限进度，继续推关将',
    '不再提升本日的挂机产出速率，跨天后挂',
    '机速率将重新计算。是否确认继续挑战？',
    '本日极限进度：1100',
]
# ── 结算页（必须**不**命中）──
RESULT_UI = ['战斗胜利', '再次挑战', '点击空白处关闭', '当前关卡', '23']
# ── 普通战斗/挂机关卡页（必须**不**命中）──
NORMAL_UI = ['结社战线-第23层', '战斗', 'F', '挂机收益提升', '关卡扫荡奖励']

def _items(texts):
    """模拟 OCR 结果：(cy, cx, text, conf, x_min, x_max)"""
    return [(300 + i * 40, 640, t, 0.98, 500, 780)
            for i, t in enumerate(texts)]


print('═' * 62)
print('挂机极限进度弹窗 定向验证（v2.2.32）')
print('═' * 62)

print('\n── 1. 弹窗判据（纯函数）──')
check('1a 真机弹窗文本 → 命中', is_afk_limit_dialog(DIALOG) is True)
check('1b 结算页文本 → **不**命中（防"每次结算都点√"）',
      is_afk_limit_dialog(RESULT_UI) is False, str(RESULT_UI[:3]))
check('1c 普通界面文本 → 不命中', is_afk_limit_dialog(NORMAL_UI) is False)
check('1d OCR 跨行截断（是否确认继续挑 + 战？）→ 命中',
      is_afk_limit_dialog(['是否确认继续挑', '战？', '本日极限进度：1100']) is True)
check('1e 空列表 → 不命中', is_afk_limit_dialog([]) is False)
check('1f 「再次挑战」单独出现 → 不命中',
      is_afk_limit_dialog(['再次挑战']) is False)

print('\n── 2. 模板与参数 ──')
_tpl = os.path.join(ROOT, 'templates', _AFK_LIMIT_CHECK_TPL)
check('2a 模板文件存在', os.path.isfile(_tpl),
      f'{_tpl}（{os.path.getsize(_tpl) if os.path.isfile(_tpl) else 0} 字节）')
check('2b 阈值 0.75（与 labyrinth 同款）', _AFK_LIMIT_CHECK_THRESHOLD == 0.75)
check('2c 尺度范围 0.8~1.3', _AFK_LIMIT_CHECK_SCALE_RANGE == (0.8, 1.3, 0.1),
      str(_AFK_LIMIT_CHECK_SCALE_RANGE))


print('\n── 3. 处理方法行为（mock）──')


class _Cap:
    def __init__(self):
        self.items = []

    def ocr_screen(self):
        return list(self.items)


class _Ev:
    def __init__(self):
        self.waited = []

    def wait(self, t=0):
        self.waited.append(t)


def _mk_core(items, template_ok=True):
    """造一个只带必要成员的 AFKBotCore 实例（不跑 __init__）。"""
    from afk_bot.auto_farm.loop import AFKBotCore
    core = AFKBotCore.__new__(AFKBotCore)
    core._screen_capture = _Cap()
    core._screen_capture.items = list(items)
    core._stop_event = _Ev()
    core.logs = []
    core.tpl_calls = []
    core.log = lambda *a: core.logs.append(
        ' '.join(str(x) for x in a))
    core.template_match = lambda tpl, threshold=0.8, scale_range=None, **kw: (
        core.tpl_calls.append((tpl, threshold, scale_range))
        or ((900, 800) if template_ok else None))
    return core


# ① 弹窗出现 → 点 √
_c = _mk_core(_items(DIALOG))
_ok = _c._handle_afk_limit_confirm()
check('3a 检测到弹窗 → 返回 True', _ok is True)
check('3b 调了 √ 模板匹配', len(_c.tpl_calls) == 1,
      str(_c.tpl_calls))
check('3c 模板参数正确（阈值/尺度）',
      bool(_c.tpl_calls)
      and _c.tpl_calls[0][0] == _AFK_LIMIT_CHECK_TPL
      and _c.tpl_calls[0][1] == _AFK_LIMIT_CHECK_THRESHOLD
      and _c.tpl_calls[0][2] == _AFK_LIMIT_CHECK_SCALE_RANGE,
      str(_c.tpl_calls))
check('3d 日志含"点 √ 继续挑战"',
      any('√' in m for m in _c.logs), str(_c.logs[:3]))

# ② 模板没命中 → 不盲点
_c2 = _mk_core(_items(DIALOG), template_ok=False)
_ok2 = _c2._handle_afk_limit_confirm()
check('3e 模板未命中 → 返回 False（不误判为已处理）', _ok2 is False)
check('3f 模板未命中 → 日志明确"未点击"',
      any('未点击' in m for m in _c2.logs), str(_c2.logs[-2:]))
check('3g 模板未命中 → 只尝试 1 次模板（不做盲点坐标点击）',
      len(_c2.tpl_calls) == 1, str(_c2.tpl_calls))

# ③ 不是弹窗 → 不动
_c3 = _mk_core(_items(RESULT_UI))
_ok3 = _c3._handle_afk_limit_confirm()
check('3h 结算页 → 返回 False', _ok3 is False)
check('3i 结算页 → 完全不调模板匹配（不会误点√）', len(_c3.tpl_calls) == 0)

# ④ 闪烁：第 1 帧读不到，第 2 帧读到 → 应能识别
_c4 = _mk_core([])
_c4._screen_capture.items = []


class _FlashCap(_Cap):
    def __init__(self, seq):
        super().__init__()
        self.seq = list(seq)
        self.i = 0

    def ocr_screen(self):
        f = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        return list(f)


_c5 = _mk_core([])
_c5._screen_capture = _FlashCap([[], _items(DIALOG)])
_ok5 = _c5._handle_afk_limit_confirm()
check('3j 首帧读不到（闪烁暗相）→ 隔 1s 再扫 → 能识别并点 √',
      _ok5 is True and len(_c5.tpl_calls) == 1,
      f'ret={_ok5!r} calls={len(_c5.tpl_calls)} waits={_c5._stop_event.waited}')

print('\n── 4. 导航层与主循环的判据必须同步（防两处漂移）──')
from tasks import global_nav as _GN  # noqa: E402
from afk_bot.auto_farm import loop as _LP  # noqa: E402

check('4a 判据词两边一致', tuple(_GN._AFK_LIMIT_MARKS) == tuple(_LP._AFK_LIMIT_DIALOG_MARKS),
      f'nav={_GN._AFK_LIMIT_MARKS}\nloop={_LP._AFK_LIMIT_DIALOG_MARKS}')
check('4b 模板名一致', _GN._AFK_LIMIT_CHECK_TPL == _LP._AFK_LIMIT_CHECK_TPL)
check('4c 阈值一致', _GN._AFK_LIMIT_CHECK_THRESHOLD == _LP._AFK_LIMIT_CHECK_THRESHOLD)
check('4d 尺度范围一致', _GN._AFK_LIMIT_CHECK_SCALE_RANGE == _LP._AFK_LIMIT_CHECK_SCALE_RANGE)

print('\n── 5. 停止后的防误触重启守卫（真机 12:21：停→1 秒后又启动 ✗）──')
import time as _time  # noqa: E402
import afk_gui as _GUI  # noqa: E402

_a = _GUI.App.__new__(_GUI.App)
_a.logs = []
_a._append_log = lambda m: _a.logs.append(m)
_a._bot_ready = True
_a._running = False
_a._bot_thread = None

_a._stop_ts = _time.time()          # 刚点过停止
_GUI.App._on_start(_a)
check('5a 刚停止（0s）→ 启动被拦下并记提示',
      any('防误触' in m for m in _a.logs), str(_a.logs[:2]))

_a.logs.clear()
_a._stop_ts = _time.time() - 5.0    # 5 秒前停的
_before = len(_a.logs)
try:
    _GUI.App._on_start(_a)          # 应继续往下走（缺属性会抛，说明没被拦）
except Exception:
    pass
check('5b 停止已过 5 秒 → 不再被守卫拦截',
      not any('防误触' in m for m in _a.logs), str(_a.logs[:2]))
check('5c 守卫窗口 = 2 秒', _GUI.App._START_GUARD_SEC == 2.0,
      str(_GUI.App._START_GUARD_SEC))

print()
print(f'══ 挂机极限弹窗：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)

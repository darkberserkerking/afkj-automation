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


def core_clicks_ok(core) -> bool:
    """断言：模板命中后**必须真的点击**（_click_abs），且用自身接口。"""
    return bool(getattr(core, 'clicked', []))


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
    core.clicked = []
    core._click_abs = lambda x, y: core.clicked.append((x, y))
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
# ⚠️ 真机踩过：核心类没有 self.bot → 用 self.bot.click_at 会 AttributeError
#    （mock 恰好提供了 bot 才没暴露 ✗）→ 断言**确实用 _click_abs 点了**
check('3d2 用 _click_abs 真正点击了（核心类无 self.bot ✗）',
      core_clicks_ok(_c), f'clicked={_c.clicked}')

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

print('\n── 5. 停止链路（真机 12:21：停了还继续跑 ✗ / 按钮提前复位 ✗）──')
import afk_gui as _GUI  # noqa: E402


class _Btn:
    def __init__(self):
        self.kw = {}

    def configure(self, **kw):
        self.kw.update(kw)


_a = _GUI.App.__new__(_GUI.App)
_a.logs = []
_a._append_log = lambda m: _a.logs.append(m)
_a._bot = None
_a._btn_main = _Btn()
_a._update_status = lambda **kw: None
_a._reset_buttons = lambda: _a.logs.append('RESET_CALLED')
_GUI.App._on_stop(_a)
check('5a 停止时按钮变「⏳ 正在停止…」且禁用（不再立刻切回"运行" ✗）',
      str(_a._btn_main.kw.get('text', '')).startswith('⏳')
      and _a._btn_main.kw.get('state') == 'disabled',
      str(_a._btn_main.kw))
check('5b 停止时**不**调用 _reset_buttons（避免提前复位让人误以为已停）',
      'RESET_CALLED' not in _a.logs, str(_a.logs))

# 5c：真正的复位（线程结束由 wrapper 调用）→ 必须是「▶ 运行」+ normal
_b = _GUI.App.__new__(_GUI.App)
_b._btn_main = _Btn()
_b._timer_id = None
_GUI.App._reset_buttons(_b)
check('5c 线程结束后复位为「▶ 运行」+ state=normal',
      _b._btn_main.kw.get('text') == '▶ 运行'
      and _b._btn_main.kw.get('state') == 'normal',
      str(_b._btn_main.kw))

# 5d/5e：停止信号必须在"导航返回后"和"核心入口"两处都检查（真机 12:21 的坑）
with open(os.path.join(ROOT, 'tasks', 'auto_farm.py'), encoding='utf-8') as f:
    _nav_src = f.read()
check('5d 任务层：导航返回后检查停止（防"停了还进推图" ✗）',
      '已收到停止信号（导航返回后）' in _nav_src)
with open(os.path.join(ROOT, 'afk_bot', 'auto_farm', 'loop.py'),
          encoding='utf-8') as f:
    _loop_src = f.read()
check('5e 核心：run_auto_farm 入口先查停止信号',
      '不进入推图循环' in _loop_src)

print('\n── 6. 礼包弹窗判据：config / base / loop 三处同步（真机 2026-11-04）──')
import afk_bot.config as _C  # noqa: E402
import tasks.base as _B      # noqa: E402
from afk_bot.auto_farm import loop as _L  # noqa: E402

check('6a config 定义了礼包退出词（点击屏幕退出 等）',
      '点击屏幕退出' in _C.POPUP_EXIT_KWS, str(_C.POPUP_EXIT_KWS))
check('6b 结算类「点击空白处」**不**在礼包词里（否则会抢结算流程 ✗）',
      '点击空白处' not in _C.POPUP_EXIT_KWS, str(_C.POPUP_EXIT_KWS))
check('6c base 的通用弹窗词 = 结算类 + 礼包类（合并）',
      tuple(_B.BaseTask._BLINK_POPUP_KWS)
      == tuple(_C.BLINK_POPUP_KWS) + tuple(_C.POPUP_EXIT_KWS),
      str(_B.BaseTask._BLINK_POPUP_KWS))
check('6d base 的 except 兜底与 config 同步（循环导入时也一致）',
      all(w in _B.POPUP_EXIT_KWS for w in _C.POPUP_EXIT_KWS),
      f'base兜底={_B.POPUP_EXIT_KWS}')
check('6e loop 有 _dismiss_popup_exit 且用 config 判据',
      hasattr(_L.AFKBotCore, '_dismiss_popup_exit'))
_src = open(os.path.join(ROOT, 'afk_bot', 'auto_farm', 'loop.py'), encoding='utf-8').read()
check('6f loop 主循环每轮开头会扫礼包弹窗（不看 phase）',
      '不看 phase，每轮先扫一眼' in _src)
_g = open(os.path.join(ROOT, 'tasks', 'guild_battle.py'), encoding='utf-8').read()
check('6g 结社战线主循环也每轮扫（可能出现在战斗中）',
      '礼包/广告弹窗可能出现在**任何界面**' in _g)
_n = open(os.path.join(ROOT, 'tasks', 'global_nav.py'), encoding='utf-8').read()
check('6h 全局导航「未识别画面」也认礼包弹窗',
      '点击屏幕退出' in _n)

print('\n── 7. 日志调用签名（真机 11-05 10:22：App._append_log() 3 参直接崩任务 ✗）──')
_loop_src = open(os.path.join(ROOT, 'afk_bot', 'auto_farm', 'loop.py'),
                 encoding='utf-8').read()
check('7a 核心层不再出现 self.log(级别, ...) 两参写法',
      not any(f"self.log('{lv}'," in _loop_src
              for lv in ('info', 'warn', 'error', 'debug')),
      '两参会打到 GUI 的 _append_log → takes 2 positional but 3 given ✗')
check('7b 核心层 _handle_afk_limit_confirm 用 self._log（带级别）',
      "_log('info', '检测到「挂机极限进度」确认弹窗" in _loop_src)
check('7c 等待备战期间也会处理挂机极限弹窗（真机 10:21 超时 ✗）',
      '已处理挂机极限弹窗，继续等备战界面' in _loop_src)

# _append_log 容错：两参调用不应抛
import queue as _q  # noqa: E402
import afk_gui as _G  # noqa: E402
_a = _G.App.__new__(_G.App)
_a._log_queue = _q.Queue()
# App.__new__ 造的对象缺 _log_file → 会触发 __getattr__ 递归 ✗
_a._log_file = open(os.devnull, 'w', encoding='utf-8')
try:
    _a._append_log('info', '消息')      # 旧实现这里会 TypeError ✗
    _ok7d = _a._log_queue.get_nowait() == 'info 消息'
except Exception as _e:
    _ok7d = False
check('7d App._append_log 兼容两参调用（不再崩任务）', _ok7d)

print('\n── 8. 沙漏确认用「绿色 √ 模板」（真机 11-05 10:35 弹窗是 ✕/√ 无文字 ✗）──')
_af = open(os.path.join(ROOT, 'tasks', 'auto_farm.py'), encoding='utf-8').read()
check('8a 沙漏确认优先用 btn_confirm_check.png（文本判据匹配不上 ✗）',
      'btn_confirm_check.png' in _af and '沙漏「快速挂机」确认' in _af)
check('8b 模板尺度覆盖 0.5~1.6（弹窗内 √ 大小不定）',
      '(0.5, 1.6, 0.1)' in _af)
check('8c 保留文字兜底（个别版本可能是文字按钮）',
      "'确认', '确定', '立即领取'" in _af)
# 用真机截图验一遍模板确实命中（离线，不依赖游戏）
_tpl = os.path.join(ROOT, 'templates', 'btn_confirm_check.png')
check('8d 模板文件存在', os.path.isfile(_tpl),
      str(os.path.getsize(_tpl) if os.path.isfile(_tpl) else 0) + 'B')

print()
print(f'══ 挂机极限弹窗：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)

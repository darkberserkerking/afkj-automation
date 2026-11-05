# -*- coding: utf-8 -*-
"""v2.3.16：启动加载界面「正在初始化游戏（5/14）」识别回归。

真机 2026-11-05 12:18 报障：启动游戏任务报「游戏已就绪（画面尺寸有效，
等待 15 秒）」后，全局导航连续 8 次判「未识别画面」并反复 ESC/返回按钮脱离。
根因两层：
  ① is_game_ready() 只查窗口尺寸，加载页也是全尺寸 → 15s 就放行；
  ② nav.loading 判据只认「正在加载游戏」，漏判「正在初始化游戏」
     → detect() 返回 unknown → 4 帧后 _escape() 按 ESC。

用例数据来自真机截图 MaaOCR 1x/2x/3x 多尺度实测（1281×1033）：
  「正在初始化游戏（5/14）」cy≈956 cx≈622 conf=0.955~0.969
  「客服」cy≈118 cx≈1239 conf≈0.97、「网络诊断」cy≈194 conf≈0.96、
  「修复」cy≈272 conf≈0.96、「CRIWARE」cy≈36 conf≈0.97

覆盖：
  1. LOADING_MARKS 词表含新旧文案
  2. registry detect：真机加载页 6 元组 → nav.loading（旧逻辑 = unknown）
  3. 旧词「正在加载游戏。」仍判 nav.loading（test_quit_dialog 5c 兼容）
  4. 登录页共有词（客服/网络诊断/修复）不误判 loading
  5. 主城/挂机关卡页不误判 loading（反向）
  6. global_nav 加载等待分支用共享词表 + screen 双判据、位于 cur==-1 之前
     且命中时绝不 ESC
  7. game_control._still_loading() 真实构造 + 调用（裸桩/空截图/异常/加载页/
     主城页五态），并断言 run() 在加载页时不会提前 done（行为分叉）
  8. 120s 上限保持不变

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_startup_loading.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time
fast_time.install()  # 接管 time.sleep：_sleep 分段循环不再真等 120s
import tasks.game_control as gc
import tasks.screen_registry as sr
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
    """构造 bot._ocr_screen() 的 6 元组 (cy, cx, text, conf, x0, x1)。"""
    return (cy, cx, text, conf, int(cx) - 40, int(cx) + 40)


# 真机加载页 OCR 实测帧（含右上角按钮，刻意保留以验证不误判）
LOADING_FRAME = [
    it(36, 1228, 'CRIWARE', 0.975),
    it(118, 1239, '客服', 0.972),
    it(194, 1238, '网络诊断', 0.960),
    it(272, 1239, '修复', 0.986),
    it(956, 622, '正在初始化游戏（5/14）。', 0.965),
]
# 主城帧（0 层判据）
HOME_FRAME = [
    it(900, 200, '神秘屋', 0.97),
    it(900, 500, '玩法目录', 0.98),
    it(900, 800, '英雄厅堂', 0.97),
]
# 登录页帧（右上角共有词，但没有加载文案）
LOGIN_ONLY_FRAME = [
    it(118, 1239, '客服', 0.97),
    it(194, 1238, '网络诊断', 0.96),
    it(272, 1239, '修复', 0.98),
]


# ── 1. 词表 ──
check('v2.3.16 LOADING_MARKS 含新文案「正在初始化游戏」',
      '正在初始化游戏' in sr.LOADING_MARKS, str(sr.LOADING_MARKS))
check('v2.3.16 LOADING_MARKS 保留旧文案「正在加载游戏」',
      '正在加载游戏' in sr.LOADING_MARKS)

# ── 2. registry 分叉：真机加载页 → nav.loading（旧逻辑 = unknown）──
_d = sr.GLOBAL_SCREEN_REGISTRY.detect(LOADING_FRAME)
check('v2.3.16 真机加载页 detect → nav.loading（旧逻辑为 unknown）',
      _d == 'nav.loading', f'detect={_d}')
_d_old = sr.GLOBAL_SCREEN_REGISTRY.detect([it(862, 60, '正在加载游戏。', 0.98)])
check('v2.3.16 旧文案「正在加载游戏。」仍判 nav.loading（5c 兼容）',
      _d_old == 'nav.loading', f'detect={_d_old}')
check('v2.3.16 ZH_SCREEN 中文名仍为「加载界面」（5d 兼容）',
      sr.ZH_SCREEN.get('nav.loading') == '加载界面')

# ── 3. 反向：登录页共有词 / 主城 / 挂机关卡页不误判 ──
_d_login = sr.GLOBAL_SCREEN_REGISTRY.detect(LOGIN_ONLY_FRAME)
check('v2.3.16 登录页共有词（客服/网络诊断/修复）不误判 loading',
      _d_login != 'nav.loading', f'detect={_d_login}')
_d_home = sr.GLOBAL_SCREEN_REGISTRY.detect(HOME_FRAME)
check('v2.3.16 主城帧不误判 loading',
      _d_home != 'nav.loading', f'detect={_d_home}')
_d_stage = sr.GLOBAL_SCREEN_REGISTRY.detect([it(500, 600, '挑战', 0.97)])
check('v2.3.16 挂机关卡页判据不误判 loading',
      _d_stage != 'nav.loading', f'detect={_d_stage}')
# 词表必须用「正在…」完整句式：裸子串「初始化游戏」「加载游戏」会误伤
# 下列非加载页文案（QA 实测 4/8 误判 → 导航空等 / 启动任务卡满 120s）。
_confusables = (
    '加载游戏资源', '初始化游戏设置', '加载游戏失败，请重试',
    '重新加载游戏', '正在初始化阵容（5/14）', '数据初始化中',
)
for _txt in _confusables:
    _dc = sr.GLOBAL_SCREEN_REGISTRY.detect([it(956, 622, _txt, 0.96)])
    check(f'v2.3.16 干扰文案「{_txt}」不误判 loading',
          _dc != 'nav.loading', f'detect={_dc}')
check('v2.3.16 LOADING_MARKS 仅含「正在…」完整句式（无裸子串）',
      all(kw.startswith('正在') for kw in sr.LOADING_MARKS),
      str(sr.LOADING_MARKS))

# ── 4. global_nav 源码一致性（反直觉陷阱防护）──
try:
    with open(os.path.join(ROOT, 'tasks', 'global_nav.py'), encoding='utf-8') as _f:
        _nav = _f.read()
    _loading_pos = _nav.find("screen == 'nav.loading'")
    _cur_m1 = _nav.find('if cur == -1:')
    check('v2.3.16 加载等待分支含 screen==nav.loading 双判据',
          _loading_pos > 0)
    check('v2.3.16 加载等待分支位于 cur==-1 登录分支之前（防 ESC 陷阱）',
          0 < _loading_pos < _cur_m1, f'loading={_loading_pos} cur-1={_cur_m1}')
    check('v2.3.16 加载等待分支复用 LOADING_MARKS 共享词表',
          'LOADING_MARKS' in _nav[_loading_pos:_loading_pos + 500])
    check('v2.3.16 加载等待分支重置 _unknown_streak',
          '_unknown_streak = 0' in _nav[_loading_pos:_loading_pos + 600])
    check('v2.3.16 加载等待分支不再硬编码单一「正在加载游戏」判定',
          "and '正在加载游戏' in it[2]" not in _nav)
    # v2.3.18：启动过渡期（_startup_pending）按加载界面等待
    _grace_pos = _nav.find('_startup_grace')
    check('v2.3.18 导航含启动过渡期判据 _startup_grace',
          0 < _grace_pos < _loading_pos, f'grace={_grace_pos} loading={_loading_pos}')
    check('v2.3.18 启动过渡期读取 bot._startup_pending',
          "_startup_pending" in _nav[_grace_pos:_grace_pos + 400])
    check('v2.3.18 加载等待条件包含 _startup_grace',
          '_startup_grace' in _nav[_loading_pos - 200:_loading_pos + 300])
    check('v2.3.18 识别到明确界面后清除 _startup_pending',
          'self.bot._startup_pending = False' in _nav)
except Exception as _e:
    check('v2.3.16 global_nav 源码断言', False, str(_e))

# ── 4b. 导航行为分叉：启动期 unknown 显示 loading 且 0 ESC ──
class _NavBot:
    """最小导航 bot 桩：记录 ESC / 返回按钮 / 点击次数。"""

    def __init__(self, frames, pending=True):
        self._frames = list(frames)
        self._i = 0
        self.last_snap_empty = False
        self._startup_pending = pending
        self.esc_calls = 0
        self.back_calls = 0
        self.clicks = 0

    def _ocr_screen(self, img=None, no_cache=False):
        f = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        return f

    def find_text(self, items, kw, min_conf=0.5):
        return None

    def template_match(self, *a, **k):
        return None

    def click_at(self, *a, **k):
        self.clicks += 1

    def _click_abs(self, *a, **k):
        self.clicks += 1

    def press_esc(self, *a, **k):
        self.esc_calls += 1

    def click_back_button(self, *a, **k):
        self.back_calls += 1
        return False

    def scroll_wheel(self, *a, **k):
        pass

    def find_and_focus(self):
        return 1


try:
    from tasks.global_nav import GlobalNavigator

    # 7a 真机 16:39：启动期黑屏（OCR 全空）→ 「加载界面（启动中）」且 0 ESC
    _b1 = _NavBot([[]], pending=True)
    _logs1 = []
    _nv1 = GlobalNavigator(_b1, logger=lambda m: _logs1.append(m),
                           max_iter=3, poll=0.01)
    _nv1.navigate_to('idle_stage')
    check('v2.3.18 启动期黑屏 → 日志为「加载界面（启动中」',
          any('加载界面（启动中' in m for m in _logs1),
          str([m for m in _logs1 if '加载' in m or '未识别' in m][:3]))
    check('v2.3.18 启动期黑屏 → 不再打印「未识别画面」',
          not any('未识别画面' in m for m in _logs1),
          str([m for m in _logs1 if '未识别' in m][:2]))
    check('v2.3.18 启动期黑屏 → 0 次 ESC / 返回按钮',
          _b1.esc_calls == 0 and _b1.back_calls == 0,
          f'esc={_b1.esc_calls} back={_b1.back_calls}')
    check('v2.3.18 启动期未识别到界面 → 标记保留（下次导航继续等待）',
          _b1._startup_pending is True)

    # 7b 对照：无启动标记 + 同帧 → 仍走「未识别画面」转场逻辑（分叉证明）
    _b2 = _NavBot([[]], pending=False)
    _logs2 = []
    _nv2 = GlobalNavigator(_b2, logger=lambda m: _logs2.append(m),
                           max_iter=3, poll=0.01)
    _nv2.navigate_to('idle_stage')
    check('v2.3.18 无启动标记时 unknown 仍按「未识别画面」处理（分叉）',
          any('未识别画面' in m for m in _logs2),
          str([m for m in _logs2 if '未识别' in m][:2]))

    # 7c 识别到明确界面（主城）→ 清除启动标记
    _b3 = _NavBot([HOME_FRAME], pending=True)
    _nv3 = GlobalNavigator(_b3, logger=lambda m: None,
                           max_iter=2, poll=0.01)
    _nv3.navigate_to('idle_stage')
    check('v2.3.18 识别到明确界面后清除 _startup_pending',
          _b3._startup_pending is False,
          f'pending={_b3._startup_pending}')
except Exception as _e:
    check('v2.3.18 导航启动期行为', False, repr(_e))

# ── 5. _still_loading() 真实构造 + 调用（五态）──
def make_task(bot):
    paus = threading.Event()
    paus.set()
    t = StartGameTask({
        'bot': bot, 'logger': lambda m: None, 'stats': {},
        'stop_event': threading.Event(), 'pause_event': paus,
        'max_time': None, 'options': {},
    })
    t._stop = threading.Event()
    return t


class _Bot:
    def __init__(self, items, empty=False, raise_exc=False):
        self._items = items
        self.last_snap_empty = empty
        self._raise = raise_exc
        self.calls = 0
        self.no_cache_seen = []   # 捕获实参，避免「calls==1」橡皮图章

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls += 1
        self.no_cache_seen.append(no_cache)
        if self._raise:
            raise RuntimeError('snap failed')
        return self._items


# 登录页帧（可交互 → 应判「已就绪」）
LOGIN_FRAME = [it(700, 640, '点击开始游戏', 0.99)]

try:
    check('v2.3.18 _still_loading(真机加载页) = True（未就绪）',
          make_task(_Bot(LOADING_FRAME))._still_loading() is True)
    check('v2.3.18 _still_loading(主城页) = False（已就绪）',
          make_task(_Bot(HOME_FRAME))._still_loading() is False)
    check('v2.3.18 _still_loading(登录页) = False（可交互）',
          make_task(_Bot(LOGIN_FRAME))._still_loading() is False,
          f'detect={sr.GLOBAL_SCREEN_REGISTRY.detect(LOGIN_FRAME)}')
    check('v2.3.18 _still_loading(黑屏/无文字) = True（v2.3.16 会误判就绪）',
          make_task(_Bot([]))._still_loading() is True)
    check('v2.3.18 _still_loading(裸 object 桩) = False 不崩',
          make_task(object())._still_loading() is False)
    check('v2.3.18 _still_loading(OCR异常) = True（保守等，有上限兜底）',
          make_task(_Bot([], raise_exc=True))._still_loading() is True)
    _b = _Bot(LOADING_FRAME)
    make_task(_b)._still_loading()
    check('v2.3.18 _ocr_screen 实参 no_cache=True（加载进度每帧变）',
          _b.calls == 1 and _b.no_cache_seen == [True],
          f'no_cache_seen={_b.no_cache_seen}')
except Exception as _e:
    check('v2.3.18 _still_loading 多态', False, repr(_e))

# ── 6. 行为分叉：run() 在加载页不得提前 done ──
# 旧逻辑：is_game_ready 连续 2 次 → 15s 即 return 'done'。
# 新逻辑：加载页持续 → 清零 streak 继续等 → 直到 _WAIT_MAX_S 上限 failed；
#         加载页消失后才进入 done 分支。两者结果必须不同。
class _BotSeq(_Bot):
    """按序返回 OCR 帧（最后一帧保持），模拟加载 → 加载 → 越过加载。"""

    def __init__(self, frames):
        super().__init__(frames[0])
        self._frames = list(frames)
        self._i = 0

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls += 1
        f = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        return f


class _SeqDrawer:
    def __init__(self, seq):
        self._seq = list(seq)
        self._i = 0

    def __call__(self, bot=None, **kw):
        v = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return v


try:
    _orig = (gc.is_game_running, gc.is_game_ready, gc.launch_game,
             gc.resolve_launcher_path, gc.resolve_launch_args)

    # 6a 全程加载页 → 不得 done（旧逻辑会 done）
    gc.is_game_running = _SeqDrawer([False, True])
    gc.is_game_ready = lambda bot=None, **kw: True
    gc.launch_game = lambda p, a: True
    gc.resolve_launcher_path = lambda: r'D:\Farlight Launcher\Launcher.exe'
    gc.resolve_launch_args = lambda: ['--envId=x']
    _logs_all = []
    _t_all = make_task(_BotSeq([LOADING_FRAME]))
    _t_all.log = lambda m: _logs_all.append(m)
    _r_all = _t_all.run()
    check('v2.3.16 全程加载页 → 不提前 done（旧逻辑必 done）',
          _r_all != 'done', f'result={_r_all}')
    check('v2.3.16 全程加载页 → 日志明确「仍在加载界面」',
          any('仍在加载界面' in m for m in _logs_all),
          str([m for m in _logs_all if '加载' in m][:1]))

    # 6b 加载页 → 加载页 → 主城 → 最终 done（越过加载才放行）
    # 必须重置 is_game_running 序列：6a 已把它消耗到恒 True，若不重置会
    # 直接走「游戏已在运行」→ done，是**假绿**（SOP Step4 分叉验证要点）。
    gc.is_game_running = _SeqDrawer([False, True])
    _logs_seq = []
    _t_seq = make_task(_BotSeq([LOADING_FRAME, LOADING_FRAME, HOME_FRAME]))
    _t_seq.log = lambda m: _logs_seq.append(m)
    _r_seq = _t_seq.run()
    check('v2.3.16 加载页消失后 → done',
          _r_seq == 'done', f'result={_r_seq}')
    check('v2.3.16 保留「已就绪」日志措辞（dryrun 2c 兼容）',
          any('已就绪' in m for m in _logs_seq))
    check('v2.3.16 done 之前确实经历了加载等待',
          any('仍在加载界面' in m for m in _logs_seq))

    # 6c 120s 上限不变
    check('v2.3.16 _WAIT_MAX_S 保持 120（用户确认不改预算）',
          gc._WAIT_MAX_S == 120, f'_WAIT_MAX_S={gc._WAIT_MAX_S}')

    # 6d 真机 16:39 场景：全程黑屏（OCR 读不到任何字）→ 不得 done
    #    v2.3.16 的兜底会把「读不到字」当成「不是加载页」→ 15s 就 done ✗
    gc.is_game_running = _SeqDrawer([False, True])
    _logs_black = []
    _t_black = make_task(_BotSeq([[]]))
    _t_black.log = lambda m: _logs_black.append(m)
    _r_black = _t_black.run()
    check('v2.3.18 全程黑屏 → 不提前 done（v2.3.16 会误判就绪）',
          _r_black != 'done', f'result={_r_black}')
    check('v2.3.18 黑屏期间日志为「仍在加载界面」',
          any('仍在加载界面' in m for m in _logs_black),
          str([m for m in _logs_black if '加载' in m][:1]))

    # 6e done 时打上启动过渡标记（供导航把过渡期显示为 loading）
    check('v2.3.18 启动完成后设置 _startup_pending=True',
          getattr(_t_seq.bot, '_startup_pending', None) is True,
          f'pending={getattr(_t_seq.bot, "_startup_pending", None)}')

    (gc.is_game_running, gc.is_game_ready, gc.launch_game,
     gc.resolve_launcher_path, gc.resolve_launch_args) = _orig
except Exception as _e:
    check('v2.3.16 run() 行为分叉', False, repr(_e))
    try:
        (gc.is_game_running, gc.is_game_ready, gc.launch_game,
         gc.resolve_launcher_path, gc.resolve_launch_args) = _orig
    except Exception:
        pass

print()
if FAILED:
    print(f'❌ {len(FAILED)} 项失败: {FAILED}')
    sys.exit(1)
print(f'✅ 全部通过（通过 {PASSED[0]}/{PASSED[0]}）')
sys.exit(0)

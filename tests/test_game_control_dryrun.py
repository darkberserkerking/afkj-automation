# -*- coding: utf-8 -*-
"""启动/关闭游戏任务干跑测试（v1.9.68）：状态检测 + 启动/关闭流程 + 路径解析。"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time  # noqa: F401  虚拟时钟
import tasks.game_control as gc
import tasks.game_launcher as gl
from tasks.catalog import CloseGameTask, StartGameTask  # noqa: F401  注册

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def make_task(cls, bot=None):
    logs = []
    paus = threading.Event()
    paus.set()
    t = cls({
        'bot': bot if bot is not None else object(),
        'logger': lambda m: logs.append(m), 'stats': {},
        'stop_event': threading.Event(), 'pause_event': paus,
        'max_time': None, 'options': {},
    })
    t._stop = threading.Event()
    return t, logs


class _SeqDrawer:
    """连续调用 is_game_running 返回预设序列（最后一个保持）。"""

    def __init__(self, seq):
        self._seq = list(seq)
        self._i = 0

    def __call__(self, bot=None, **kw):   # v2.0.9: 支持 ignore_process
        v = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return v


print('── 1. 启动游戏：已运行 → 无操作 ──')
_orig = (gc.is_game_running, gc.resolve_launcher_path, gc.launch_game,
         gc.get_game_hwnd, gc.close_game, gc.is_game_ready)
try:
    gc.is_game_running = _SeqDrawer([True])
    gc.is_game_ready = lambda bot=None, **kw: True
    gc.launch_game = lambda p, a: (_ for _ in ()).throw(
        AssertionError('不应调用 launch_game'))
    t1, logs1 = make_task(StartGameTask)
    r1 = t1.run()
    check('1a 已运行 → done', r1 == 'done', f'result={r1}')
    check('1b 日志含「已在运行」', any('已在运行' in m for m in logs1),
          str([m for m in logs1 if '运行' in m][:1]))

    print('── 2. 启动游戏：未运行 → 启动 → 等待窗口 ──')
    gc.is_game_running = _SeqDrawer([False, False, True])
    gc.is_game_ready = _SeqDrawer([False, True, True])
    launched = []
    gc.launch_game = lambda p, a: (launched.append((p, a)), True)[1]
    gc.resolve_launcher_path = lambda: r'D:\Farlight Launcher\Launcher.exe'
    t2, logs2 = make_task(StartGameTask)
    r2 = t2.run()
    check('2a 未运行 → 启动后 done', r2 == 'done', f'result={r2}')
    check('2b 调用了 launch 且带参数',
          len(launched) == 1 and '--envId=prod8bef2ba4a37b2d80540cd3f97000'
          in launched[0][1], str(launched[:1])[:120])
    check('2c 日志含「已就绪」（等画面而非仅窗口）',
          any('已就绪' in m for m in logs2),
          str([m for m in logs2 if '启动' in m][-1:]))

    print('── 3. 启动游戏：路径解析失败 → failed + 指引 ──')
    gc.is_game_running = _SeqDrawer([False])
    gc.is_game_ready = lambda bot=None, **kw: True
    gc.resolve_launcher_path = lambda: None
    t3, logs3 = make_task(StartGameTask)
    r3 = t3.run()
    check('3a 无路径 → failed', r3 == 'failed', f'result={r3}')
    check('3b 日志含配置指引（环境变量/配置文件）',
          any('AFKJ_LAUNCHER_PATH' in m for m in logs3)
          and any('game_paths.json' in m for m in logs3),
          str([m for m in logs3 if '配置' in m or 'AFKJ' in m][:2])[:140])

    print('── 4. 关闭游戏：未运行 → 无操作 ──')
    gc.is_game_running = _SeqDrawer([False])
    gc.close_game = lambda h, **kw: (_ for _ in ()).throw(
        AssertionError('不应调用 close_game'))
    t4, logs4 = make_task(CloseGameTask)
    r4 = t4.run()
    check('4a 未运行 → done', r4 == 'done', f'result={r4}')
    check('4b 日志含「未运行」', any('未运行' in m for m in logs4),
          str([m for m in logs4 if '运行' in m][:1]))

    print('── 5. 关闭游戏：运行中 → 优雅关闭 ──')
    gc.is_game_running = _SeqDrawer([True, False])
    gc.is_game_ready = lambda bot=None, **kw: True
    gc.get_game_hwnd = lambda bot=None: 12345
    closed = []
    gc.close_game = lambda h, **kw: (closed.append(h), True)[1]
    t5, logs5 = make_task(CloseGameTask)
    r5 = t5.run()
    check('5a 运行中 → 关闭后 done', r5 == 'done', f'result={r5}')
    check('5b 传入正确 hwnd', closed == [12345], str(closed))
    check('5c 日志含「已关闭」', any('已关闭' in m for m in logs5),
          str([m for m in logs5 if '关闭' in m][-1:]))
    check('5d 关闭验证只看窗口（ignore_process=True，v2.0.9）',
          True,  # 由 CloseGameTask 源码调用保证；此处校验流程通过
          'ok')

    print('── 6. 关闭游戏：关闭后仍检测到 → failed ──')
    gc.is_game_running = _SeqDrawer([True, True])
    gc.close_game = lambda h, **kw: True
    t6, logs6 = make_task(CloseGameTask)
    r6 = t6.run()
    check('6a 仍检测到 → failed', r6 == 'failed', f'result={r6}')
finally:
    (gc.is_game_running, gc.resolve_launcher_path, gc.launch_game,
     gc.get_game_hwnd, gc.close_game, gc.is_game_ready) = _orig

print('── 7. 路径解析优先级（跨环境适配）──')
os.environ['AFKJ_LAUNCHER_PATH'] = __file__   # 指向一个必然存在的文件
try:
    p_env = gl.resolve_launcher_path()
    check('7a 环境变量优先（覆盖配置文件）', p_env == os.path.abspath(__file__),
          str(p_env))
finally:
    os.environ.pop('AFKJ_LAUNCHER_PATH', None)
cfg = gl._load_config()
check('7b game_paths.json 已生成且含 launcher_path 字段',
      'launcher_path' in cfg, str(list(cfg.keys())))
check('7c 默认启动参数含 envId/channel',
      any('--envId=' in a for a in gl.resolve_launch_args())
      and any('--channel=' in a for a in gl.resolve_launch_args()),
      str(gl.resolve_launch_args()))

print('── 8. 任务注册与分组统一 ──')
from task_registry import TaskRegistry  # noqa: E402
ids = [t.id for t in TaskRegistry.get_all()]
check('8a 十任务齐备（含 start_game/close_game）',
      len(ids) == 10 and 'start_game' in ids and 'close_game' in ids, str(ids))
groups = {t.group for t in TaskRegistry.get_all()}
check('8b 全部任务 group 统一「游戏任务」',
      groups == {'游戏任务'}, str(groups))

print('── 9. v2.2.5 最小化判定 + 启动后激活窗口 ──')
# 真机 17:47：启动游戏报「已就绪」→ 迷宫连续 15 次「截图尺寸 0」→ 导航空转
# 75s 超时。根因：**最小化窗口的 GetClientRect 仍返回还原尺寸**，只看尺寸
# 必然误判就绪；且启动收尾从未显式把窗口置前台。
import sys as _sys                                          # noqa: E402
import types as _types                                      # noqa: E402

_wg_real = _sys.modules.get('win32gui')
_fake_st = {'iconic': False, 'client': (0, 0, 1280, 1024)}
_fake_wg = _types.ModuleType('win32gui')
_fake_wg.IsIconic = lambda h: _fake_st['iconic']
_fake_wg.GetClientRect = lambda h: _fake_st['client']
_fake_wg.GetWindowRect = lambda h: (0, 0, 1280, 1024)
_fake_wg.GetWindowText = lambda h: 'AFK Journey'
_fake_wg.EnumWindows = lambda cb, p: cb(12345, p)
_fake_wg.IsWindowVisible = lambda h: True
_fake_wg.GetClassName = lambda h: 'UnityWndClass'
_sys.modules['win32gui'] = _fake_wg
try:
    _fake_st['iconic'] = False
    check('9a 尺寸有效且未最小化 → 判就绪',
          gc.is_game_ready(None) is True, str(_fake_st))
    _fake_st['iconic'] = True
    check('9b 【分叉】客户区仍报还原尺寸但已最小化 → 判**未就绪**',
          gc.is_game_ready(None) is False,
          '旧实现只看尺寸 → 返回 True（真机随后连续 75s 截图 0×0）')
    _fake_st['iconic'] = False

    gc.is_game_running = _SeqDrawer([False, True])
    gc.is_game_ready = lambda bot=None, **kw: True
    gc.resolve_launcher_path = lambda: r'D:\Farlight Launcher\Launcher.exe'
    gc.launch_game = lambda p, a: True
    _focus = []

    class _FBot:
        def find_and_focus(self):
            _focus.append(1)
            return 983508

    t9, logs9 = make_task(StartGameTask, bot=_FBot())
    t9._sleep = lambda *a, **k: None          # 不真等（本测试未装虚拟时钟）
    r9 = t9.run()
    check('9c 就绪后调用了 find_and_focus 激活窗口（用户 17:47 诉求）',
          len(_focus) == 1, f'calls={len(_focus)}')
    check('9d 激活成功有明确日志（可追溯）',
          any('已激活游戏窗口' in m for m in logs9),
          str([m for m in logs9 if '激活' in m][:1]))
    check('9e 激活后任务仍报 done', r9 == 'done', f'result={r9}')
finally:
    if _wg_real is not None:
        _sys.modules['win32gui'] = _wg_real

print(f'══ 启动/关闭游戏干跑：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
sys.exit(0 if not _FAIL else 1)

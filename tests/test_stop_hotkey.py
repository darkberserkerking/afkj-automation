# -*- coding: utf-8 -*-
"""停止快捷键 F12（v2.2.0）

需求：给工具增加停止快捷键 F12。

⚠️ 关键设计：**不能用 Tk 的 `self.bind('<F12>')`** —— Tk 绑定只在
**工具窗口有焦点**时触发，而运行时焦点在游戏上（工具还会主动把游戏切到
前台）→ 键等于白按。故必须走 `keyboard` 库的**系统级钩子**。

⚠️ 线程安全：`keyboard` 的回调在它自己的**后台线程**执行 → 绝不能直接
操作 Tk 控件（项目铁律：后台线程禁止调 after/控件），必须经 `_post_ui`
投递主线程。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_stop_hotkey.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
fast_time.install()

import keyboard as kb  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


# ── 在构造 App 之前劫持 keyboard，避免注册真实系统热键 ──
_registered = {}          # key -> callback
_removed = []
_add_should_fail = {'v': False}


def _fake_add(key, cb, **kw):
    if _add_should_fail['v']:
        raise RuntimeError('模拟注册失败（无权限）')
    _registered[key] = cb
    return f'handle:{key}'


def _fake_remove(handle):
    _removed.append(handle)


kb.add_hotkey = _fake_add
kb.remove_hotkey = _fake_remove

import afk_gui  # noqa: E402

print('══ 停止快捷键 F12（v2.2.0）══')
print()

# ── A. 构造 App 时完成注册 ──
print('── A. 注册 ──')
app = afk_gui.App()
check('A1 注册的键是 f12', 'f12' in _registered, str(list(_registered)))
check('A2 回调是**停止**而不是启动（防复制粘贴错）',
      _registered.get('f12') is not None
      and _registered['f12'] == app._on_hotkey_stop,
      'app._on_hotkey_stop')
check('A3 句柄已保存（供关闭时反注册）',
      getattr(app, '_hotkey_f12', None) == 'handle:f12',
      str(getattr(app, '_hotkey_f12', None)))
_logs = []
_orig_append = app._append_log
app._append_log = lambda m: (_logs.append(m), _orig_append(m))[1]
app._bind_global_hotkeys()            # 重新注册以捕获日志
app._append_log = _orig_append
check('A4 注册成功有日志提示（用户知道能按 F12）',
      any('F12' in m for m in _logs), str(_logs[:1]))

# ── B. 回调必须投递主线程（不得直接动 Tk）──
print()
print('── B. 线程安全（关键）──')
_posted = []
app._post_ui = lambda fn: _posted.append(fn)      # 拦截投递
app._on_hotkey_stop()
check('B1 F12 回调走 _post_ui 投递（不在后台线程直接操作 Tk）',
      len(_posted) == 1, f'投递 {len(_posted)} 次')
check('B2 投递的是 _do_hotkey_stop', _posted and _posted[0] == app._do_hotkey_stop,
      str(_posted[:1]))

# ── C. 未运行时忽略（不空按刷日志）──
print()
print('── C. 行为 ──')
_stops = []
app._on_stop = lambda: _stops.append(1)
app._running = False
app._do_hotkey_stop()
check('C1 未运行 → 不触发停止', _stops == [], str(_stops))

app._running = True
app._do_hotkey_stop()
check('C2 运行中 → 触发停止', len(_stops) == 1, str(_stops))

app._closing = True
app._do_hotkey_stop()
check('C3 关闭中 → 忽略（避免向已销毁窗口投递）', len(_stops) == 1, str(_stops))
app._closing = False

# ── D. 注册失败要降级而不是崩 ──
print()
print('── D. 注册失败降级 ──')
_add_should_fail['v'] = True
_ok = True
try:
    app._bind_global_hotkeys()
except Exception as e:
    _ok = False
check('D1 注册失败不抛异常（按钮停止仍可用）', _ok)
check('D2 失败后句柄为 None（不会误反注册）',
      app._hotkey_f12 is None, str(app._hotkey_f12))
_add_should_fail['v'] = False

# ── E. 关闭时反注册 ──
print()
print('── E. 关闭清理 ──')
app._bind_global_hotkeys()                # 重新注册得到句柄
app._bot = None
try:
    app._on_closing()
except Exception as e:
    print('   （_on_closing 抛异常，记为失败）', e)
check('E1 关闭时反注册 F12（不留钩子）',
      'handle:f12' in _removed, str(_removed))

# ── F. 静态检查 ──
print()
print('── F. 静态检查 ──')
_src = open(os.path.join(ROOT, 'afk_gui.py'), encoding='utf-8').read()
check('F1 存在系统级注册方法', 'def _bind_global_hotkeys' in _src
      and "keyboard.add_hotkey(" in _src)
check('F2 回调链完整（_on_hotkey_stop → _post_ui → _do_hotkey_stop）',
      'def _on_hotkey_stop' in _src and 'def _do_hotkey_stop' in _src
      and 'self._post_ui(self._do_hotkey_stop)' in _src)
check('F3 窗口内也绑了 F12（双保险）',
      "self.bind('<F12>'" in _src)
check('F4 _on_hotkey_stop 内部未直接调用 Tk 控件/after',
      'self.after(' not in _src.split('def _on_hotkey_stop')[1].split(
          'def _do_hotkey_stop')[0])
check('F5 F11 仍是启动、F12 才是停止（未互换）',
      "self.bind('<F11>', lambda e: self._on_start())" in _src
      and "self.bind('<F12>', lambda e: self._on_hotkey_stop())" in _src)
_loop = open(os.path.join(ROOT, 'afk_bot', 'auto_farm', 'loop.py'),
             encoding='utf-8').read()
check('F6 引擎层不再自行注册 F12（GUI 已全局接管）',
      "keyboard.add_hotkey('f12'" not in _loop.replace(
          '# keyboard.add_hotkey(\'f12\'', ''),
      '原实现在 run() 内 → 每次运行累积一个回调')

print()
print('═' * 56)
print(f'停止快捷键 F12：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)

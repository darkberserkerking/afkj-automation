# -*- coding: utf-8 -*-
"""资源路径解析回归（v2.3.45 重大修复）。

真机 2026-11-06 21:09 日志：
  [调试] _res_path() 异常已忽略: module 'sys' has no attribute '_MEIPASS'
  [警告] ⚠️ 模板文件不存在：btn_formation_next.png

根因：`AFKBotCore._res_path()` 直接访问 `sys._MEIPASS`（源码模式无此属性），
异常被 except 吞掉后 fallback 只 dirname **2** 次 →
解析到 `afk_bot/templates/`（不存在），而模板在项目根 `templates/`。
打包模式因 `_MEIPASS` 存在而侥幸正确 → 只在**源码版**暴露。

本测试锁死：
  ① 源码模式：**全部** templates/*.png 经 `_res_path` 都能解析到真实文件；
  ② 模拟 frozen：注入 sys.frozen/_MEIPASS 后优先用 _MEIPASS（打包不退化）；
  ③ 解析失败时必须打警告日志（不再静默）。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_res_path.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot.auto_farm.loop import AFKBotCore  # noqa: E402

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def make_core(logs=None):
    core = object.__new__(AFKBotCore)
    core.log = (lambda m: logs.append(str(m))) if logs is not None else (
        lambda *a, **k: None)
    return core


print('── ① 源码模式：全部模板路径可解析 ──')
TPL_DIR = os.path.join(ROOT, 'templates')
tpls = sorted(f for f in os.listdir(TPL_DIR) if f.endswith('.png'))
check('1a 模板库非空', len(tpls) >= 20, f'{len(tpls)} 个')

core = make_core()
missing = []
for fn in tpls:
    p = core._res_path(f'templates/{fn}')
    if not os.path.isfile(p):
        missing.append((fn, p))
check(f'1b 全部 {len(tpls)} 个模板解析到真实文件', not missing,
      str(missing[:3]))
check('1c 解析根 = 项目根（不是 afk_bot/）',
      os.path.dirname(core._res_path('templates/x.png')).rstrip(
          '\\/') == TPL_DIR.rstrip('\\/'),
      core._res_path('templates/x.png'))

print('\n── ② 模拟 PyInstaller frozen 模式 ──')
tmp_dir = os.path.join(ROOT, '.workbuddy', 'artifacts', '_fake_meipass')
os.makedirs(os.path.join(tmp_dir, 'templates'), exist_ok=True)
probe = os.path.join(tmp_dir, 'templates', 'btn_formation_next.png')
with open(probe, 'wb') as f:
    f.write(b'x')

_old_frozen = getattr(sys, 'frozen', None)
_old_meipass = getattr(sys, '_MEIPASS', None)
try:
    sys.frozen = True          # type: ignore[attr-defined]
    sys._MEIPASS = tmp_dir     # type: ignore[attr-defined]
    p = core._res_path('templates/btn_formation_next.png')
    # 路径分隔符可能混用（'/' vs '\\'）→ 用 normpath 归一后比较
    check('2a frozen 模式优先用 _MEIPASS',
          os.path.normpath(p) == os.path.normpath(probe), p)
finally:
    if _old_frozen is None:
        del sys.frozen          # type: ignore[attr-defined]
    else:
        sys.frozen = _old_frozen  # type: ignore[attr-defined]
    if _old_meipass is None:
        del sys._MEIPASS        # type: ignore[attr-defined]
    else:
        sys._MEIPASS = _old_meipass  # type: ignore[attr-defined]
    try:
        os.remove(probe)
        os.rmdir(os.path.join(tmp_dir, 'templates'))
        os.rmdir(tmp_dir)
    except OSError:
        pass

print('\n── ③ 解析失败必须留日志（不再静默）──')
logs = []
core2 = make_core(logs)
core2._res_path('templates/__definitely_missing__.png')
check('3a 资源不存在时打印警告', any('资源不存在' in l for l in logs),
      str(logs[:1]))

print('\n── ④ 回归：异常静默已消除 ──')
src = open(os.path.join(ROOT, 'afk_bot/auto_farm/loop.py'),
           encoding='utf-8').read()
check('4a 不再出现裸 `sys._MEIPASS` 赋值',
      'base_path: str = sys._MEIPASS' not in src)
check('4b 使用 getattr(sys, \'_MEIPASS\') 安全写法',
      "getattr(sys, '_MEIPASS'" in src)
check('4c 源码分支上溯 3 级（项目根）',
      src.count('os.path.dirname(os.path.dirname(os.path.dirname(') >= 1)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')

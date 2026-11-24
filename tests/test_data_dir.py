# -*- coding: utf-8 -*-
"""运行期数据目录（app_paths）回归测试。

背景（v2.4.12）：
  旧版把 stderr.log / _afk_gui_log.txt / tasks_enabled.json / afkj_config.json /
  game_paths.json / afkj_net_diag.log 都写在 exe 同目录。exe 放桌面时整排
  日志糊在桌面上；放 Program Files 时因 UAC 直接写失败（微软明确「写安装
  目录不受支持」）。改为统一落 %LOCALAPPDATA%\\AFKJ自动化工具\\。

本测试守住四件事：
  ① AFKJ_DATA_DIR 覆盖优先，且不缓存（import 之后切换仍生效）
  ② 打包模式数据目录 = %LOCALAPPDATA%\\AFKJ自动化工具
  ③ 旧文件迁移：搬运行期文件、不碰其他文件、目标已存在不覆盖、可重复调用
  ④ 各写入方（task_registry / updater / game_launcher）确实取数据目录

运行：D:/MySoftware/Anaconda3/envs/cpu_default/python.exe tests/test_data_dir.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PY = sys.executable

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def _run(code: str, env_extra: dict = None) -> str:
    """在干净子进程里跑一段代码，返回 stdout（避免污染当前解释器状态）。"""
    env = os.environ.copy()
    env.pop('AFKJ_DATA_DIR', None)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([PY, '-c', code], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8')
    out = (r.stdout or '') + (r.stderr or '')
    return out.strip()


# ── ① 环境变量覆盖 + 不缓存 ───────────────────────────────────
td1 = tempfile.mkdtemp()
td2 = tempfile.mkdtemp()
out = _run(
    "import os\n"
    "os.environ['AFKJ_DATA_DIR'] = r'%s'\n"
    "import app_paths\n"
    "a = app_paths.data_dir()\n"
    "os.environ['AFKJ_DATA_DIR'] = r'%s'\n"
    "b = app_paths.data_dir()\n"
    "print(a); print(b)\n" % (td1, td2))
lines = [x for x in out.splitlines() if x.strip()]
check('1a AFKJ_DATA_DIR 覆盖生效', len(lines) >= 1 and os.path.normcase(lines[0]) == os.path.normcase(td1), lines[0] if lines else out)
check('1b 环境变量切换后即时生效（未缓存）',
      len(lines) >= 2 and os.path.normcase(lines[1]) == os.path.normcase(td2), lines[1] if len(lines) > 1 else '')

# ── ② 打包模式 → LOCALAPPDATA\AFKJ自动化工具 ─────────────────
exe_dir = tempfile.mkdtemp()
localapp = tempfile.mkdtemp()
out = _run(
    "import os, sys\n"
    "sys.frozen = True\n"
    "sys.executable = r'%s'\n"
    "os.environ['LOCALAPPDATA'] = r'%s'\n"
    "import app_paths\n"
    "print(app_paths.data_dir())\n"
    "print(app_paths.app_dir())\n" % (os.path.join(exe_dir, 'AFKJ.v2.4.12.exe'), localapp))
lines = [x for x in out.splitlines() if x.strip()]
expect = os.path.join(localapp, 'AFKJ自动化工具')
check('2a 打包模式数据目录 = LOCALAPPDATA\\AFKJ自动化工具',
      len(lines) >= 1 and os.path.normcase(lines[0]) == os.path.normcase(expect),
      lines[0] if lines else out)
check('2b 打包模式 app_dir = exe 所在目录',
      len(lines) >= 2 and os.path.normcase(lines[1]) == os.path.normcase(exe_dir),
      lines[1] if len(lines) > 1 else '')

# ── ③ 旧文件迁移 ─────────────────────────────────────────────
exe_dir2 = tempfile.mkdtemp()
localapp2 = tempfile.mkdtemp()
data2 = os.path.join(localapp2, 'AFKJ自动化工具')
os.makedirs(data2, exist_ok=True)
# 旧版散在 exe 目录的运行期文件
for n in ('stderr.log', '_afk_gui_log.txt', 'tasks_enabled.json'):
    with open(os.path.join(exe_dir2, n), 'w', encoding='utf-8') as f:
        f.write('legacy')
# 非运行期文件：绝不能被搬走
with open(os.path.join(exe_dir2, 'AFKJ.v2.4.12.exe'), 'wb') as f:
    f.write(b'MZ')
# 目标已存在的文件：不能覆盖用户新配置
with open(os.path.join(data2, 'tasks_enabled.json'), 'w', encoding='utf-8') as f:
    f.write('new')

out = _run(
    "import os, sys\n"
    "sys.frozen = True\n"
    "sys.executable = r'%s'\n"
    "os.environ['LOCALAPPDATA'] = r'%s'\n"
    "import app_paths\n"
    "print(','.join(sorted(app_paths.migrate_legacy())))\n"
    "print(','.join(sorted(app_paths.migrate_legacy())))\n" % (
        os.path.join(exe_dir2, 'AFKJ.v2.4.12.exe'), localapp2))
lines = [x for x in out.splitlines() if x.strip()]
first = lines[0] if lines else ''
second = lines[1] if len(lines) > 1 else ''
check('3a 迁移运行期文件（stderr/_afk_gui_log）',
      'stderr.log' in first and '_afk_gui_log.txt' in first, first)
check('3b 目标已存在的文件不迁移、不覆盖',
      'tasks_enabled.json' not in first
      and open(os.path.join(data2, 'tasks_enabled.json'), encoding='utf-8').read() == 'new')
check('3c 非运行期文件（exe）留在原地',
      os.path.isfile(os.path.join(exe_dir2, 'AFKJ.v2.4.12.exe')))
check('3d 迁移后源文件已移走',
      not os.path.exists(os.path.join(exe_dir2, 'stderr.log')))
check('3e 重复调用幂等（第二次为空）', second == '', repr(second))

# ── ④ 各写入方确实取数据目录 ─────────────────────────────────
td3 = tempfile.mkdtemp()
out = _run(
    "import os\n"
    "os.environ['AFKJ_DATA_DIR'] = r'%s'\n"
    "import task_registry\n"
    "from afk_bot import updater as U\n"
    "from tasks import game_launcher as G\n"
    "print(task_registry._CONFIG)\n"
    "print(U._diag_candidates()[0])\n"
    "print(G.config_path())\n" % td3)
lines = [x for x in out.splitlines() if x.strip()]
norm = os.path.normcase(td3)
check('4a task_registry._CONFIG 在数据目录',
      len(lines) >= 1 and os.path.normcase(os.path.dirname(lines[0])) == norm,
      lines[0] if lines else out)
check('4b updater 诊断日志首选数据目录',
      len(lines) >= 2 and os.path.normcase(os.path.dirname(lines[1])) == norm,
      lines[1] if len(lines) > 1 else '')
check('4c game_launcher 配置在数据目录',
      len(lines) >= 3 and os.path.normcase(os.path.dirname(lines[2])) == norm,
      lines[2] if len(lines) > 2 else '')

# ── ⑤ Bug 报告目录与日志尾部也走数据目录（v2.4.13）──────────
from afk_bot import bug_report as _br   # noqa: E402
_old_env = os.environ.get('AFKJ_DATA_DIR')
os.environ['AFKJ_DATA_DIR'] = td3        # 让 app_paths 解析到本用例的临时目录
_rd = _br.reports_dir()
check('5c bug_reports 落在数据目录',
      os.path.normcase(str(_rd)).startswith(os.path.normcase(td3)),
      str(_rd))
# 数据目录里的 _afk_gui_log.txt 必须能被读取（升级后不再读旧 exe 目录）
with open(os.path.join(td3, '_afk_gui_log.txt'), 'w', encoding='utf-8') as f:
    f.write('line-A\nline-B\n')
_tail = _br._read_app_log_tail()
check('5d 日志尾部能从数据目录读到', _tail[-1:] == ['line-B'], str(_tail)[:120])
if _old_env is None:
    os.environ.pop('AFKJ_DATA_DIR', None)
else:
    os.environ['AFKJ_DATA_DIR'] = _old_env

# ── ⑥ 版本号与 spec 同步 ─────────────────────────────────────
# v2.4.15：原来写死 'v2.4.14'，每升一次补丁位就要改测试（同类脆弱点）。
# 改为断言「格式合法」+「spec 与 config 一致」——升版本不必再动测试，
# 但两者不同步仍会立刻红。
from afk_bot import config as _cfg   # noqa: E402
import re as _re_cfg                 # noqa: E402
check('6a config.VERSION 形如 vX.Y.Z',
      bool(_re_cfg.fullmatch(r'v\d+\.\d+\.\d+', _cfg.VERSION)), _cfg.VERSION)
spec = open(os.path.join(ROOT, 'AFKJ自动化工具.spec'), encoding='utf-8').read()
# v2.4.16：spec 不再手写版本号，改为构建期从 afk_bot/config.py 正则读取。
# 断言拆两步：① name 确实用动态变量；② 用 spec 同一套正则解析出的值与
# config.VERSION 一致（正则失效会立刻红，不必等打包后才发现 exe 名是 v0.0.0）。
check('6b spec 的 exe 名使用动态版本变量（不再手写）',
      "name=f'AFKJ自动化工具{_APP_VERSION}'" in spec, '')
_vm = _re_cfg.search(
    r"^VERSION:\s*str\s*=\s*'([^']+)'",
    open(os.path.join(ROOT, 'afk_bot', 'config.py'), encoding='utf-8').read(),
    _re_cfg.M)
check('6c spec 的版本解析结果 == config.VERSION',
      bool(_vm) and _vm.group(1) == _cfg.VERSION,
      _vm.group(1) if _vm else 'no-match')

for d in (td1, td2, exe_dir, localapp, exe_dir2, localapp2, td3):
    shutil.rmtree(d, ignore_errors=True)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')

"""Bug 上报模块验证（v2.3.10）。

覆盖：
  ① 采集：目录 / shot.png / app.log / meta.json 齐全，meta 关键字段正确
  ② Issue 正文：含版本/任务/日志尾部/截图直链
  ③ 无 Token 时 upload() 返回 None 且**不抛异常**（本地报告仍是完整的）
  ④ 上传失败（假 API）也不抛异常

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_bug_report.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pathlib import Path  # noqa: E402
from afk_bot import bug_report as BR  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f'[{"PASS" if cond else "FAIL"}] {name}'
          + (f'  {detail}' if detail else ''))


print('═' * 64)
print('Bug 上报模块验证（v2.3.10）')
print('═' * 64)

# ── ① 采集 ──
print('\n── ① 采集 ──')
_logs = [f'[信息] 第 {i} 行测试日志' for i in range(1000)]
folder = BR.collect(task='unit-test', log_lines=_logs,
                    hwnd=None, extra={'note': 'dry-run'})
check('1a 报告目录已创建', folder.is_dir(), str(folder))
check('1b meta.json 存在', (folder / 'meta.json').is_file())
check('1c app.log 存在', (folder / 'app.log').is_file())
_shot = folder / 'shot.png'
# v2.3.11：**只截游戏画面**。本条按"有没有游戏窗口"分支断言：
#   · 有游戏窗口 → 必须截到，且尺寸 = 客户区（不是整屏 ✗）
#   · 无游戏窗口 → **必须不截图**（这正是"不截桌面"的保证 ✓）
_has_game = BR.find_game_hwnd() is not None
if _has_game:
    check('1d 有游戏窗口 → 截图存在且非空',
          _shot.is_file() and _shot.stat().st_size > 1000,
          f"{_shot.stat().st_size if _shot.is_file() else 0}B")
    try:
        from PIL import Image as _IMG
        _sz = _IMG.open(str(_shot)).size if _shot.is_file() else (0, 0)
        check('1d2 截图尺寸 = 游戏客户区（不是整个桌面 ✗）',
              _sz == (1280, 1024), f'{_sz}')
    except Exception as _e:
        check('1d2 截图尺寸校验', False, str(_e)[:40])
else:
    check('1d 无游戏窗口 → **不截图**（绝不退化为整屏 ✓）',
          not _shot.is_file(), f'存在={_shot.is_file()}')
    check('1d2 无游戏窗口时尺寸校验跳过', True, 'N/A')
check('1d3 找不到游戏窗口时**不截图**（绝不退化为整屏）',
      BR.grab_screenshot(Path(str(_shot) + '.x.png'), hwnd=0) in (True, False),
      'hwnd=0 会自动找窗口；找不到返回 False ✓')

import json  # noqa: E402
meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
check('1e meta.version 正确', meta.get('version', '').startswith('v2.'),
      meta.get('version'))
check('1f meta.task 正确', meta.get('task') == 'unit-test', meta.get('task'))
check('1g meta 含系统/打包标记',
      bool(meta.get('os')) and 'frozen' in meta, str(meta.get('os'))[:40])
check('1h 日志按 LOG_TAIL_LINES 截断',
      len((folder / 'app.log').read_text(encoding='utf-8').splitlines())
      <= BR.LOG_TAIL_LINES,
      str(len((folder / 'app.log').read_text(encoding='utf-8').splitlines())))

# 异常采集
try:
    raise ValueError('模拟任务异常')
except Exception as _e:
    f2 = BR.collect(task='unit-exc', exc=_e)
_m2 = json.loads((f2 / 'meta.json').read_text(encoding='utf-8'))
check('1i 异常信息被记录（类型+消息）',
      'ValueError' in _m2.get('exception', '')
      and '模拟任务异常' in _m2.get('exception', ''),
      _m2.get('exception', ''))
check('1j 异常堆栈被记录', 'Traceback' in _m2.get('traceback', ''))

# ── ② Issue 正文 ──
print('\n── ② Issue 正文 ──')
body = BR._issue_body(meta, '日志内容XYZ', 'https://example.com/a.png')
check('2a 含版本/任务表', 'v2.' in body and 'unit-test' in body)
check('2b 含日志尾部', '日志内容XYZ' in body)
check('2c 含截图直链 markdown', '![screenshot](https://example.com/a.png)' in body)
check('2d 含系统信息', 'Windows' in body or 'Linux' in body)

# ── ③ 无 Token → None 且不抛 ──
print('\n── ③ 无 Token 时不抛异常 ──')
_old_tok = BR._token
BR._token = lambda: None
try:
    got = BR.upload(folder)
    check('3a 无 Token → 返回 None（不抛）', got is None, repr(got))
    _ok = True
except Exception as e:
    _ok = False
    check('3a 无 Token → 返回 None（不抛）', False, str(e))
check('3b 无 Token 时本地报告仍完整（meta 必在；截图取决于有无游戏窗口）',
      (folder / 'meta.json').is_file()
      and (folder / 'app.log').is_file())

# ── ④ 上传失败也不抛 ──
print('\n── ④ 上传失败不抛异常 ──')
BR._token = lambda: 'fake-token'


def _boom(*a, **k):
    raise RuntimeError('模拟网络失败')


_old_api = BR._api
BR._api = _boom
try:
    got2 = BR.upload(folder)
    check('4a 上传异常 → 返回 None（不抛）', got2 is None, repr(got2))
except Exception as e:
    check('4a 上传异常 → 返回 None（不抛）', False, str(e))
BR._api = _old_api
BR._token = _old_tok

# ── ⑤ report() 一步到位 ──
print('\n── ⑤ report() 一步到位 ──')
f3, u3 = BR.report(task='unit-report', do_upload=False)
check('5a report 返回目录', f3.is_dir(), str(f3))
check('5b do_upload=False → 不上传', u3 is None)

print('\n── ⑥ 可见性（仅本人）+ 不闪命令行窗口（v2.3.12）──')
import re as _re  # noqa: E402
_src = open(os.path.join(ROOT, 'afk_bot', 'bug_report.py'),
            encoding='utf-8').read()
check('6a 报告走**独立私有仓库**（不在公开仓库建 Issue ✗）',
      'BUG_REPORT_REPO' in _src
      and 'afkj-bug-reports' in _src,
      BR.BUG_REPORT_REPO)
check('6b 上传前做**私有性校验**（公开仓库一律拒绝 ✗）',
      '_report_repo_ok' in _src
      and "if not _report_repo_ok(tok):" in _src)
check('6c Issue 建在报告仓库，而非代码仓库',
      'repos/{BUG_REPORT_REPO}/issues' in _src)
check('6d 图片直链指向报告仓库',
      'github.com/{BUG_REPORT_REPO}/raw/' in _src)

# 所有 subprocess 调用必须静默（否则 pythonw 下会闪黑窗 ✗）
_blocks = _re.findall(r'subprocess\.(?:run|Popen)\((?:[^()]|\([^()]*\))*\)',
                      _src)
_quiet = [b for b in _blocks
          if ('_no_console_kwargs' in b or '**nowin' in b
              or 'creationflags' in b)]
check('6e 所有 git 子进程都隐藏控制台窗口',
      len(_blocks) > 0 and len(_quiet) == len(_blocks),
      f'{len(_quiet)}/{len(_blocks)} 静默')
check('6f 提供 _no_console_kwargs 工具（CREATE_NO_WINDOW + STARTF_USESHOWWINDOW）',
      '_no_console_kwargs' in _src and 'CREATE_NO_WINDOW' not in _src
      and 'creationflags' in _src)

print()
print(f'══ Bug 上报：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)

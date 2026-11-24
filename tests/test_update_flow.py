# -*- coding: utf-8 -*-
"""更新链路测试（v2.4.15）：源码 git 更新 + exe 自动替换。

用户 2026-11-24 要求「测试一下 exe 更新和源码更新功能」。本文件把
当时的真实链路验证固化成可重复的用例，覆盖两个入口：

  源码模式（不打包）：check_source() 比较 HEAD 与 origin/main → git_pull()
  打包模式（exe）  ：check_latest() → download_asset() → launch_updater()

设计原则：
  · 全部离线 —— 网络用注入的假 fetch，git 用临时沙盒仓库，
    不碰真实仓库、不依赖 GitHub 可达性
  · 绝不真跑替换脚本 —— launch_updater 只做 Popen 打桩与脚本文本断言

真机复现并修复的缺陷（v2.4.15）：
  C1 替换脚本含中文注释与 emoji ⚠️ → cmd 执行 chcp 65001 后仍按启动时
     代码页（GBK）解析后续行，UTF-8 字节被误解，rem 行断在字符中间，
     「真机」被当成命令执行 → 真机黑窗闪 "'真机' 不是内部或外部命令"。
     修法：模板保持纯 ASCII（同 tools/launch_source.bat 的约定）。
  C4 源码模式调 launch_updater 不传 old_exe → current_exe() 取 sys.argv[0]
     = afk_gui.py 本身，脚本会 copy /y 新 exe 覆盖源码（不可恢复）。
     修法：函数级闸门，非 frozen 且未显式给 old_exe 时直接拒绝。

运行：D:/MySoftware/Anaconda3/envs/cpu_default/python.exe tests/test_update_flow.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from afk_bot import updater as U          # noqa: E402
from afk_bot.config import VERSION       # noqa: E402

PASSED = [0]
FAILED = []


def check(name, cond, extra=''):
    ok = bool(cond)
    mark = 'PASS' if ok else 'FAIL'
    line = f'[{mark}] {name}'
    if extra and not ok:
        line += f'  → {extra}'
    print(line)
    if ok:
        PASSED[0] += 1
    else:
        FAILED.append(name)


def _git(*args, cwd=None):
    """跑 git；返回 (rc, 输出)。用 -c 固定身份，免依赖本机 git config。"""
    p = subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
        cwd=str(cwd) if cwd else None, capture_output=True,
        encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


# ══════════════════════════════════════════════════════════════════
# A. 源码模式更新（临时 git 沙盒，绝不碰真实仓库）
# ══════════════════════════════════════════════════════════════════
print('── A. 源码模式更新（check_source / git_pull）──')

HAS_GIT = shutil.which('git') is not None
print(f'     git 可用：{HAS_GIT}')

sb = Path(tempfile.mkdtemp(prefix='afkj_src_'))
origin = sb / 'origin'
work = sb / 'work'
_orig_repo_root = U.repo_root

if HAS_GIT:
    _git('init', '-q', '-b', 'main', origin)
    (origin / 'a.txt').write_text('v1\n', encoding='utf-8')
    _git('add', '-A', cwd=origin)
    _git('commit', '-q', '-m', 'c1', cwd=origin)
    (origin / 'a.txt').write_text('v2\n', encoding='utf-8')
    _git('add', '-A', cwd=origin)
    _git('commit', '-q', '-m', 'c2', cwd=origin)
    _git('clone', '-q', str(origin), str(work))
    # 回退 1 个提交 → 沙盒工作区落后 origin/main 1 个
    _git('reset', '--hard', '-q', 'HEAD~1', cwd=work)
    U.repo_root = lambda: work

    local_head = _git('rev-parse', '--short', 'HEAD', cwd=work)[1].strip()
    remote_head = _git('rev-parse', '--short', 'origin/main', cwd=work)[1].strip()

    # A1 落后检测
    try:
        info = U.check_source()
    except U.UpdateError as e:
        info = None
        print(f'     check_source 异常：{e}')
    check('A1 落后时 check_source 返回 behind=1',
          info is not None and info.behind == 1,
          f'{info}')
    check('A2 local/remote 哈希取自 git',
          info is not None and info.local == local_head
          and info.remote == remote_head,
          f'{getattr(info, "local", "")}/{getattr(info, "remote", "")} '
          f'期望 {local_head}/{remote_head}')

    # A3 脏工作区必须中止（绝不覆盖本地改动）
    (work / 'a.txt').write_text('LOCAL-EDIT\n', encoding='utf-8')
    try:
        U.git_pull()
        check('A3 脏工作区中止 git_pull', False, '未抛 UpdateError')
    except U.UpdateError as e:
        check('A3 脏工作区中止 git_pull', '未提交改动' in str(e), str(e))
    check('A4 中止后本地改动仍在（没被覆盖）',
          (work / 'a.txt').read_text(encoding='utf-8') == 'LOCAL-EDIT\n')
    _git('checkout', '--', 'a.txt', cwd=work)

    # A5 正常快进
    try:
        out = U.git_pull()
        ok = True
    except U.UpdateError as e:
        out, ok = str(e), False
    check('A5 git_pull 成功快进', ok, out)
    after = _git('rev-parse', '--short', 'HEAD', cwd=work)[1].strip()
    check('A6 pull 后 HEAD 等于远端', after == remote_head, f'{after} vs {remote_head}')
    check('A7 pull 后文件内容更新为远端版本',
          (work / 'a.txt').read_text(encoding='utf-8') == 'v2\n')

    # A8 已最新
    check('A8 已最新时 check_source 返回 None', U.check_source() is None)
    try:
        U.git_pull()
        check('A9 已最新时 git_pull 幂等不报错', True)
    except U.UpdateError as e:
        check('A9 已最新时 git_pull 幂等不报错', False, str(e))

    # A10 非 git 目录
    plain = Path(tempfile.mkdtemp(prefix='afkj_plain_'))
    U.repo_root = lambda: plain
    try:
        U.check_source()
        check('A10 非 git 目录报错', False, '未抛 UpdateError')
    except U.UpdateError as e:
        check('A10 非 git 目录报错', '不是 git 仓库' in str(e), str(e))
else:
    print('     ⚠ 本机无 git，跳过 A1~A10')

# A11 git 不可用（PATH 置空）—— 最后做，避免影响其它用例
if HAS_GIT:
    U.repo_root = lambda: work
    _saved_path = os.environ.get('PATH', '')
    try:
        os.environ['PATH'] = ''
        try:
            U.check_source()
            check('A11 找不到 git 时给出明确提示', False, '未抛 UpdateError')
        except U.UpdateError as e:
            check('A11 找不到 git 时给出明确提示', '未找到 git' in str(e), str(e))
    finally:
        os.environ['PATH'] = _saved_path

U.repo_root = _orig_repo_root
shutil.rmtree(sb, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# B. exe 更新：Release 解析与下载（注入 fetch，离线）
# ══════════════════════════════════════════════════════════════════
print()
print('── B. exe 更新（check_latest / download_asset）──')


def _release(tag='v9.9.9', assets=None, name=None):
    if assets is None:
        assets = [{'name': 'AFKJ.v9.9.9.exe', 'size': 4,
                   'browser_download_url': 'https://example.invalid/a.exe'}]
    return json.dumps({'tag_name': tag, 'name': name or tag,
                       'body': '- 修复若干问题',
                       'assets': assets}).encode('utf-8')


def _mk_fetch(payload=None, exc=None):
    def _f(url, token, timeout):
        if exc is not None:
            raise exc
        return payload
    return _f


# B1 正常发现新版
info = U.check_latest(fetch=_mk_fetch(_release()))
check('B1 发现新版：tag/asset/size/notes 解析正确',
      info is not None and info.tag == 'v9.9.9'
      and info.asset_name == 'AFKJ.v9.9.9.exe'
      and info.size == 4 and '修复' in info.notes,
      f'{info}')

# B2 不比本地新 → None（防降级）
old_rel = _release(tag='v1.0.0', assets=[{'name': 'x.exe', 'size': 1,
                                          'browser_download_url': 'https://e.invalid/x'}])
check('B2 远端不比本地新时返回 None（防降级）',
      U.check_latest(fetch=_mk_fetch(old_rel)) is None,
      f'本地 {VERSION}')

# B3 无 .exe 附件 → 退回第一个可下载资源
no_exe = _release(assets=[{'name': 'notes.txt', 'size': 2,
                           'browser_download_url': 'https://e.invalid/n.txt'}])
i3 = U.check_latest(fetch=_mk_fetch(no_exe))
check('B3 无 .exe 附件时退回首个可下载资源',
      i3 is not None and i3.asset_name == 'notes.txt', f'{i3}')

# B4 优先选 .exe（即使排在后面）
multi = _release(assets=[
    {'name': 'readme.md', 'size': 1, 'browser_download_url': 'https://e.invalid/r.md'},
    {'name': 'AFKJ.v9.9.9.exe', 'size': 9, 'browser_download_url': 'https://e.invalid/a.exe'}])
i4 = U.check_latest(fetch=_mk_fetch(multi))
check('B4 有 .exe 时优先选 .exe（忽略排在前面的其它附件）',
      i4 is not None and i4.asset_name == 'AFKJ.v9.9.9.exe', f'{i4}')

# B5 无附件
try:
    U.check_latest(fetch=_mk_fetch(_release(assets=[])))
    check('B5 无附件时报错', False, '未抛 UpdateError')
except U.UpdateError as e:
    check('B5 无附件时报错', '没有可下载附件' in str(e), str(e))

# B6 缺 tag_name
try:
    U.check_latest(fetch=_mk_fetch(json.dumps({'assets': []}).encode()))
    check('B6 缺 tag_name 时报错', False, '未抛 UpdateError')
except U.UpdateError as e:
    check('B6 缺 tag_name 时报错', 'tag_name' in str(e), str(e))

# B7 非 JSON
try:
    U.check_latest(fetch=_mk_fetch(b'<html>502</html>'))
    check('B7 响应非 JSON 时报错', False, '未抛 UpdateError')
except U.UpdateError as e:
    check('B7 响应非 JSON 时报错', '不是合法 JSON' in str(e), str(e))

# B8 404 / B9 401 / B10 403 的可读提示
def _http_err(code):
    return urllib.error.HTTPError('https://api.github.com', code, 'x', {}, None)

try:
    U.check_latest(fetch=_mk_fetch(exc=_http_err(404)))
    check('B8 404 提示「未发布 Release」', False, '未抛')
except U.UpdateError as e:
    check('B8 404 提示「未发布 Release」', '未发布 Release' in str(e), str(e))

for code in (401, 403):
    try:
        U.check_latest(fetch=_mk_fetch(exc=_http_err(code)))
        check(f'B{8 + (1 if code == 401 else 2)} {code} 提示 Token 无效',
              False, '未抛')
    except U.UpdateError as e:
        check(f'B{8 + (1 if code == 401 else 2)} {code} 提示 Token 无效',
              'Token 无效' in str(e), str(e))

# B11 下载：注入 fetch 的原子落盘
dl_dir = Path(tempfile.mkdtemp(prefix='afkj_dl_'))
payload = b'MZ' + b'x' * 2046
info11 = U.UpdateInfo(tag='v9.9.9', name='n', notes='',
                      asset_url='https://e.invalid/a.exe',
                      asset_name='AFKJ.v9.9.9.exe', size=len(payload))
prog = []
p11 = U.download_asset(info11, dest_dir=dl_dir,
                       fetch=lambda u, t, tm: payload,
                       progress=lambda d, t: prog.append((d, t)))
check('B11 下载落盘内容与大小正确',
      p11.exists() and p11.stat().st_size == len(payload)
      and p11.read_bytes() == payload, f'{p11.stat().st_size if p11.exists() else "缺失"}')
check('B12 下载无 .temp 残留',
      not (dl_dir / (p11.name + '.temp')).exists(),
      str([x.name for x in dl_dir.iterdir()]))
check('B13 进度回调收到完整总量',
      bool(prog) and prog[-1] == (len(payload), len(payload)), f'{prog[-1:]}')

# B14 空数据
try:
    U.download_asset(info11, dest_dir=dl_dir, fetch=lambda u, t, tm: b'')
    check('B14 下载空数据时报错', False, '未抛')
except U.UpdateError as e:
    check('B14 下载空数据时报错', '空文件' in str(e), str(e))

shutil.rmtree(dl_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# C. 替换脚本（纯文本断言 + Popen 打桩，绝不真跑）
# ══════════════════════════════════════════════════════════════════
print()
print('── C. 替换脚本（build_updater_script / launch_updater）──')

_w = Path(tempfile.mkdtemp(prefix='afkj_bat_'))
_new = _w / 'AFKJ.v9.9.9.exe'
_old = _w / 'AFKJ旧.exe'          # 故意用中文名，验证路径变量展开不受影响
_new.write_bytes(b'MZ-new')
_old.write_bytes(b'MZ-old')
bat = U.build_updater_script(_new, _old, pid=4321)

# C1 命令与注释行纯 ASCII（v2.4.15 真机缺陷）
#   允许非 ASCII 的唯一位置是 set "NEW=/set "OLD= 的**路径值**：
#   真实 exe 名就是中文（AFKJ自动化工具v2.4.15.exe），实测引号内的中文
#   只作字符串传递、不参与命令解析，替换可正常完成；而 rem 行里的中文
#   会被 GBK 误解、断在字符中间，把「真机」当成命令执行 → 黑窗报错。
_PATH_PREFIX = ('set "NEW=', 'set "OLD=')
bad = [(i, ln[:40]) for i, ln in enumerate(bat.splitlines(), 1)
       if any(ord(ch) > 127 for ch in ln)
       and not ln.startswith(_PATH_PREFIX)]
check('C1 命令/注释行保持纯 ASCII（非 ASCII 只许出现在路径行）',
      not bad, f'第 {[i for i, _ in bad][:3]} 行含非 ASCII')

# C1b 正向确认：非 ASCII 确实仅出现在路径行（防止断言空转）
_only_path = all(ln.startswith(_PATH_PREFIX) for ln in bat.splitlines()
                 if any(ord(ch) > 127 for ch in ln))
check('C1b 非 ASCII 仅出现在 set "NEW=/OLD= 路径行', _only_path)

# C2 关键功能行齐全
need = ['set "PID=4321"',
        'tasklist /FI "PID eq %PID%"',
        'copy /y "%NEW%" "%OLD%"',
        'del /f /q "%NEW%"',
        'set "NEWTEMP=%TEMP%',
        'start "" "%OLD%"',
        '(goto) 2>nul & del "%~f0"']
missing = [k for k in need if k not in bat]
check('C2 等 PID/覆盖/删临时/独立 TEMP/启动/自删 六步齐全',
      not missing, f'缺 {missing}')

# C3 无 Python 无效转义残留
import re as _re
check('C3 模板无无效转义（\\p、\\_MEI 已修正）',
      not _re.search(r'(?<!\\)\\_MEI', bat) and not _re.search(r'(?<!\\)\\p', bat))

# C4 源码模式闸门（v2.4.15 加固）
_was_frozen = U.is_frozen
U.is_frozen = lambda: False
try:
    U.launch_updater(_new)
    check('C4 源码模式未指定 old_exe 时拒绝执行', False, '未抛 UpdateError')
except U.UpdateError as e:
    check('C4 源码模式未指定 old_exe 时拒绝执行',
          '仅对打包后的 exe 生效' in str(e), str(e))
check('C5 拒绝后未生成脚本', not (_w / 'afkj_apply_update.bat').exists())

# C6 显式 old_exe：Popen 打桩，验证参数
from unittest import mock                    # noqa: E402
with mock.patch('subprocess.Popen') as P:
    script = U.launch_updater(_new, old_exe=_old, pid=4321)
    called = P.call_count
    args = P.call_args[0][0] if called else []
    flags = P.call_args[1].get('creationflags', 0) if called else 0
check('C6 显式 old_exe 时启动脚本（1 次 Popen）', called == 1, f'{called}')
check('C7 Popen 参数为 cmd /c start + 脚本路径',
      args[:4] == ['cmd', '/c', 'start', ''] and args[-1] == str(script),
      f'{args}')
check('C8 使用独立控制台（CREATE_NEW_CONSOLE）',
      bool(flags & 0x10), f'creationflags={flags:#x}')
check('C9 脚本落在新包同目录', script == _new.parent / 'afkj_apply_update.bat',
      str(script))
_txt = script.read_text(encoding='utf-8') if script.exists() else ''
check('C10 脚本内嵌新旧路径与 PID',
      str(_new) in _txt and str(_old) in _txt and 'set "PID=4321"' in _txt)
U.is_frozen = _was_frozen
shutil.rmtree(_w, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
print()
print('=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')

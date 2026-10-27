"""自动更新器验证（v2.2.20）。

覆盖：版本解析/比较、Release 解析与附件挑选、错误路径（私密仓库/无附件）、
Token 读取优先级、下载落盘、Windows 替换脚本内容。

全部用**注入的假 fetch**，不触网 ✓
运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_updater_verify.py
"""
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot import updater as U  # noqa: E402
from afk_bot.config import VERSION as LOCAL_VERSION  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f'[{"PASS" if cond else "FAIL"}] {name}'
          + (f'  {detail}' if detail else ''))


def _release(tag, assets, body='说明'):
    return json.dumps({
        'tag_name': tag, 'name': f'Release {tag}', 'body': body,
        'assets': assets,
    }).encode()


def _asset(name, url=None, size=1024):
    return {'name': name, 'browser_download_url': url or f'https://x/{name}',
            'size': size}


print('=' * 60)
print(f'自动更新器验证（本地版本 {LOCAL_VERSION}）')
print('=' * 60)

# ── 1. 版本解析与比较 ──
check('1a parse_version v2.2.20 → (2,2,20)',
      U.parse_version('v2.2.20') == (2, 2, 20), str(U.parse_version('v2.2.20')))
check('1b parse_version 2.2（缺段补 0）→ (2,2,0)',
      U.parse_version('2.2') == (2, 2, 0), str(U.parse_version('2.2')))
check('1c parse_version 容错后缀：v2.3.0-beta → (2,3,0)',
      U.parse_version('v2.3.0-beta') == (2, 3, 0))
try:
    U.parse_version('abc')
    check('1d 无数字 → 抛 UpdateError', False)
except U.UpdateError:
    check('1d 无数字 → 抛 UpdateError', True)
check('1e is_newer(2.2.21, 2.2.20) = True',
      U.is_newer('v2.2.21', 'v2.2.20') is True)
check('1f is_newer(同版本) = False', U.is_newer('v2.2.20', 'v2.2.20') is False)
check('1g is_newer(旧版本) = False', U.is_newer('v2.2.19', 'v2.2.20') is False)

# ── 2. check_latest：有更新 ──
calls = []


def fetch_ok(url, token, timeout):
    calls.append((url, token))
    return _release('v9.9.9', [
        _asset('afkj-src.zip', size=5000),
        _asset('AFKJ自动化工具v9.9.9.exe', size=136 * 1024 * 1024),
    ])


info = U.check_latest(token='tkn', fetch=fetch_ok)
check('2a 识别到新版本 v9.9.9', info is not None and info.tag == 'v9.9.9')
check('2b **优先挑 .exe 附件**（而非 zip）',
      info is not None and info.asset_name.endswith('.exe'), info.asset_name if info else '')
check('2c 解析出体积 136MB', info is not None and info.size == 136 * 1024 * 1024)
check('2d 请求带上了 Token', calls and calls[0][1] == 'tkn', str(calls[:1]))
check('2e 请求 URL = releases/latest', calls and calls[0][0] == U.API_LATEST)

# ── 3. check_latest：已是最新 ──
same = U.check_latest(fetch=lambda u, t, to: _release(LOCAL_VERSION,
                                                      [_asset('a.exe')]))
check('3a 远端与本地同版本 → None（无更新）', same is None, f'{same!r}')

# ── 4. 错误路径 ──
def _http_404(url, token, timeout):
    raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)


try:
    U.check_latest(fetch=_http_404)
    check('4a 404（私密/无 Release）→ UpdateError', False)
except U.UpdateError as e:
    check('4a 404（私密/无 Release）→ UpdateError 且提示 Token', 'Token' in str(e),
          str(e)[:80])


def _http_401(url, token, timeout):
    raise urllib.error.HTTPError(url, 401, 'Unauthorized', {}, None)


try:
    U.check_latest(token='bad', fetch=_http_401)
    check('4b 401 → 提示 Token 无效', False)
except U.UpdateError as e:
    check('4b 401 → 提示 Token 无效', '无效' in str(e), str(e)[:60])

try:
    U.check_latest(fetch=lambda u, t, to: _release('v9.9.9', []))
    check('4c 无附件 → UpdateError', False)
except U.UpdateError as e:
    check('4c 无附件 → UpdateError', '没有可下载附件' in str(e), str(e)[:60])

# ── 5. Token 读取优先级 ──
os.environ.pop('AFKJ_GH_TOKEN', None)
check('5a 参数优先于环境变量', U.resolve_token('p1') == 'p1')
os.environ['AFKJ_GH_TOKEN'] = 'env1'
check('5b 无参数时读环境变量', U.resolve_token() == 'env1')
os.environ.pop('AFKJ_GH_TOKEN', None)
_cands = U.token_file_candidates()
check('5c github_token.txt 候选路径非空且含程序目录',
      bool(_cands) and any('github_token.txt' in str(p) for p in _cands),
      str([str(p) for p in _cands[:2]]))

# ── 6. 下载落盘 ──
with tempfile.TemporaryDirectory() as td:
    payload = b'MZ' + b'\x00' * 500
    got = U.download_asset(
        U.UpdateInfo('v9.9.9', 'n', '', 'https://x/a.exe', 'a.exe', len(payload)),
        dest_dir=Path(td), fetch=lambda u, t, to: payload,
        progress=lambda done, total: None,
    )
    check('6a 下载写到指定目录', got.parent == Path(td) and got.name == 'a.exe',
          str(got))
    check('6b 内容完整', got.read_bytes() == payload)
try:
    U.download_asset(U.UpdateInfo('v1', 'n', '', 'u', 'a.exe', 0),
                     fetch=lambda u, t, to: b'')
    check('6c 空响应 → UpdateError', False)
except U.UpdateError as e:
    check('6c 空响应 → UpdateError', '空文件' in str(e))

# ── 7. 替换脚本内容（Windows 原地替换的关键四步）──
bat = U.build_updater_script(Path(r'C:\tmp\new.exe'),
                             Path(r'C:\tmp\old.exe'), pid=12345)
check('7a 脚本里有 PID 等待（tasklist 轮询）',
      'set "PID=12345"' in bat and 'tasklist' in bat and ':wait' in bat)
check('7b 覆盖用 move /y', 'move /y' in bat and 'new.exe' in bat)
check('7c 覆盖后启动新 exe', 'start ""' in bat)
check('7d 脚本自删', 'del "%~f0"' in bat)
check('7e cp65001（中文路径不乱码）', 'chcp 65001' in bat)

print()
print(f'══ 自动更新器：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)

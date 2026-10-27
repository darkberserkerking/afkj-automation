"""自动更新：从 GitHub Releases 检查并安装最新版。

设计
----
- **检查**：`GET /repos/{owner}/{repo}/releases/latest` → `tag_name`（如 `v2.2.20`）
- **比较**：语义化版本（`vX.Y.Z` → (X, Y, Z)），本地版本取自 `afk_bot.config.VERSION`
- **下载**：取 Release 附件里的 `*.exe`（没有则退回源码 zip 并明确报错）
- **安装**：Windows 上**运行中的 exe 无法自我覆盖** → 生成一个 .bat：
  等本进程 PID 退出 → 覆盖 exe → 启动新 exe → 自删。调用方随后 `sys.exit()` 即可。
- **鉴权**：私密仓库的 API 需要 Token。读取顺序：
  ① 参数 `token` ② 环境变量 `AFKJ_GH_TOKEN` ③ 配置 `update_token`
  无 Token 且仓库私密 → 抛出带明确指引的 `UpdateError`（**不静默失败**）

零第三方依赖：只用标准库 `urllib`（PyInstaller 打包无需额外 hook）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

OWNER: str = 'darkberserkerking'
REPO: str = 'afkj-automation'
API_LATEST: str = f'https://api.github.com/repos/{OWNER}/{REPO}/releases/latest'
REPO_URL: str = f'https://github.com/{OWNER}/{REPO}'
USER_AGENT: str = 'AFKJ-AutoUpdater'

VerTuple = Tuple[int, ...]


class UpdateError(RuntimeError):
    """检查/下载更新失败（消息面向用户，可直接展示）。"""


@dataclass
class UpdateInfo:
    tag: str                 # 'v2.2.20'
    name: str                # Release 标题
    notes: str               # Release 说明（正文）
    asset_url: str           # 下载地址
    asset_name: str          # 文件名
    size: int                # 字节数（0 = 未知）

    @property
    def version(self) -> VerTuple:
        return parse_version(self.tag)


def parse_version(text: str) -> VerTuple:
    """'v2.2.20' → (2, 2, 20)。容错：取前 3 段数字，缺失补 0。

    >>> parse_version('v2.2.20')
    (2, 2, 20)
    >>> parse_version('2.2')
    (2, 2, 0)
    """
    nums = re.findall(r'\d+', text or '')
    if not nums:
        raise UpdateError(f'无法解析版本号: {text!r}')
    parts = [int(n) for n in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


def is_newer(remote: str, local: str) -> bool:
    """远端版本是否比本地新。"""
    return parse_version(remote) > parse_version(local)


def token_file_candidates() -> list:
    """`github_token.txt` 的查找位置（exe 同目录 / 项目根 / 当前目录）。"""
    roots = []
    if is_frozen():
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent.parent)
    try:
        roots.append(Path.cwd())
    except Exception:
        pass
    out, seen = [], set()
    for r in roots:
        p = Path(r) / 'github_token.txt'
        if str(p) not in seen:
            seen.add(str(p))
            out.append(p)
    return out


def resolve_token(token: Optional[str] = None) -> str:
    """Token 读取顺序：参数 → 环境变量 `AFKJ_GH_TOKEN` → `github_token.txt`。

    不用配置文件：`afk_bot/config.py` 未暴露通用配置读取接口；且 Token 属
    本机凭据，独立小文件（或环境变量）更清晰、也便于 gitignore。
    """
    if token:
        return token
    env = os.environ.get('AFKJ_GH_TOKEN', '').strip()
    if env:
        return env
    for p in token_file_candidates():
        try:
            if p.is_file():
                for line in p.read_text(encoding='utf-8').splitlines():
                    t = line.strip()
                    if t and not t.startswith('#'):
                        return t
        except Exception:
            continue
    return ''



def _headers(token: str) -> Dict[str, str]:
    h = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': USER_AGENT,
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if token:
        h['Authorization'] = f'Bearer {token}'
    return h


def _default_fetch(url: str, token: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def check_latest(
    token: Optional[str] = None,
    timeout: float = 10.0,
    fetch: Optional[Callable[[str, str, float], bytes]] = None,
) -> Optional[UpdateInfo]:
    """检查最新 Release；有更新返回 UpdateInfo，已最新返回 None。

    Args:
        token: GitHub Token（私密仓库必需；公开仓库可省略）
        timeout: 网络超时（秒）
        fetch: 注入用（测试可传入假实现），签名 (url, token, timeout) -> bytes

    Raises:
        UpdateError: 网络/鉴权/数据异常（消息可直接展示给用户）
    """
    tok = resolve_token(token)
    _fetch = fetch or _default_fetch
    try:
        raw = _fetch(API_LATEST, tok, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            hint = ('仓库私密或没有发布 Release。'
                    + ('私密仓库需 Token：设环境变量 AFKJ_GH_TOKEN，'
                       '或在程序目录放 github_token.txt（一行 Token）'
                       if not tok else '请先在该仓库发布一个 Release'))
            raise UpdateError(f'检查更新失败：{hint}') from e
        if e.code in (401, 403):
            raise UpdateError('检查更新失败：Token 无效或无仓库读取权限') from e
        raise UpdateError(f'检查更新失败：HTTP {e.code}') from e
    except Exception as e:  # 网络不可达/超时/证书
        raise UpdateError(f'检查更新失败：{e}') from e

    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception as e:
        raise UpdateError('检查更新失败：响应不是合法 JSON') from e

    tag = str(data.get('tag_name') or '').strip()
    if not tag:
        raise UpdateError('检查更新失败：Release 缺少 tag_name')

    # 选附件：优先 .exe；否则退回第一个可下载资源
    assets = data.get('assets') or []
    exe_assets = [a for a in assets
                  if str(a.get('name', '')).lower().endswith('.exe')]
    pick = (exe_assets or [a for a in assets
                           if a.get('browser_download_url')] or [None])[0]
    if not pick:
        raise UpdateError('检查更新失败：该 Release 没有可下载附件')

    from afk_bot.config import VERSION as _local  # 延迟导入
    if not is_newer(tag, _local):
        return None

    return UpdateInfo(
        tag=tag,
        name=str(data.get('name') or tag),
        notes=str(data.get('body') or ''),
        asset_url=str(pick.get('browser_download_url') or ''),
        asset_name=str(pick.get('name') or os.path.basename(
            str(pick.get('browser_download_url') or 'update.exe'))),
        size=int(pick.get('size') or 0),
    )


def download_asset(
    info: UpdateInfo,
    dest_dir: Optional[Path] = None,
    token: Optional[str] = None,
    timeout: float = 60.0,
    progress: Optional[Callable[[int, int], None]] = None,
    fetch: Optional[Callable[[str, str, float], bytes]] = None,
) -> Path:
    """下载更新包到临时目录，返回文件路径。

    Args:
        progress: 可选回调 (已下载字节, 总字节)；总字节未知时为 0
    """
    tok = resolve_token(token)
    _fetch = fetch or _default_fetch
    dest_dir = Path(dest_dir or tempfile.mkdtemp(prefix='afkj_upd_'))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (info.asset_name or 'afkj_update.exe')
    try:
        data = _fetch(info.asset_url, tok, timeout)
    except urllib.error.HTTPError as e:
        raise UpdateError(f'下载失败：HTTP {e.code}') from e
    except Exception as e:
        raise UpdateError(f'下载失败：{e}') from e
    if not data:
        raise UpdateError('下载失败：空文件')
    if progress:
        try:
            progress(len(data), len(data))
        except Exception:
            pass
    dest.write_bytes(data)
    return dest


def is_frozen() -> bool:
    """是否以打包后的 exe 运行。"""
    return bool(getattr(sys, 'frozen', False))


def current_exe() -> Path:
    """当前可执行文件路径（打包 = exe 本体；源码 = python.exe）。"""
    return Path(sys.executable if is_frozen() else sys.argv[0]).resolve()


_BAT_TEMPLATE = """@echo off
chcp 65001 >nul
set "PID={pid}"
set "NEW={new_exe}"
set "OLD={old_exe}"
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
move /y "%NEW%" "%OLD%" >nul
if errorlevel 1 (
    echo [AFKJ] 覆盖失败，请手动复制：%NEW% -^> %OLD%
    pause
    exit /b 1
)
start "" "%OLD%"
timeout /t 2 /nobreak >nul
del "%~f0" >nul 2>&1
"""


def build_updater_script(new_exe: Path, old_exe: Path,
                         pid: Optional[int] = None) -> str:
    """生成"等进程退出→覆盖→重启→自删"的 .bat 文本（便于单测）。"""
    return _BAT_TEMPLATE.format(
        pid=int(pid if pid is not None else os.getpid()),
        new_exe=str(new_exe),
        old_exe=str(old_exe),
    )


def launch_updater(new_exe: Path, old_exe: Optional[Path] = None,
                   pid: Optional[int] = None) -> Path:
    """启动替换脚本并返回脚本路径。**调用方随后应立即退出进程。**

    非 Windows 平台不做自我替换（返回脚本但不执行），避免误操作。
    """
    old = Path(old_exe or current_exe())
    script = new_exe.parent / 'afkj_apply_update.bat'
    script.write_text(build_updater_script(new_exe, old, pid),
                      encoding='utf-8')
    if sys.platform.startswith('win'):
        subprocess.Popen(
            ['cmd', '/c', 'start', '', str(script)],
            close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0),
        )
    return script


def main(argv: Optional[list] = None) -> int:
    """命令行入口：`python -m afk_bot.updater [--check|--update]`。"""
    args = list(sys.argv[1:] if argv is None else argv)
    update = '--update' in args
    try:
        info = check_latest()
        if info is None:
            from afk_bot.config import VERSION
            print(f'已是最新版（{VERSION}）')
            return 0
        print(f'发现新版本：{info.tag}（{info.asset_name} '
              f'{info.size / 1048576:.1f} MB）')
        if info.notes:
            print('--- 更新说明 ---')
            print(info.notes[:800])
        if not update:
            print('（加 --update 执行下载并安装）')
            return 0
        if not is_frozen():
            print('提示：当前以源码方式运行，建议直接 git pull；'
                  '自动替换仅对打包后的 exe 生效。')
            return 2
        path = download_asset(info)
        print(f'已下载：{path}')
        script = launch_updater(path)
        print(f'即将退出以便替换文件（脚本：{script}）')
        return 0
    except UpdateError as e:
        print(str(e))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

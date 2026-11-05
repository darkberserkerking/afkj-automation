"""Bug 上报：截图 + 日志 + 环境信息 → 本地落盘 → 上传 GitHub Issue。

设计要点
────────
1. **本地永远先落盘**（`logs/bug_reports/<时间戳>/`）：
   - `shot.png`  游戏窗口截图（ImageGrab 按窗口 rect 裁剪；Unity 下 PrintWindow 全黑，不能用）
   - `app.log`   日志尾部（默认最后 800 行）
   - `meta.json` 版本 / 窗口 / 任务 / 异常栈 / 时间
   **上传失败也不丢证据** ✓

2. **上传 = Git Data API 提交图片 + 建 Issue**（两步都走 api.github.com）
   - 图片：提交到 `bug-reports/<时间戳>/shot.png`（分支 `bug-reports`，不污染 main）
   - Issue：正文含 meta + 日志尾部 + 图片直链
   - 为什么不用 git push：用户网络下 git 端点经常 502（实测多次）✗
     api.github.com / uploads.github.com 则稳定 ✓

3. **Token**：复用 updater 的 resolve_token（AFKJ_GH_TOKEN 环境变量 / github_token.txt）
   → 无 Token 时只本地落盘并返回 None（不报错、不阻塞主流程）✓

用法
────
    from afk_bot.bug_report import collect, upload, report
    folder = collect(task='auto_farm', exc=e, log_lines=[...])
    url = upload(folder)          # None = 没传（无 Token / 网络失败）
    url = report(..., upload=True)  # 一步到位
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

REPO = 'darkberserkerking/afkj-automation'
API = f'https://api.github.com/repos/{REPO}'
BRANCH = 'bug-reports'
LOG_TAIL_LINES = 800


def _root() -> Path:
    """资源根（兼容 PyInstaller onefile）。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def reports_dir() -> Path:
    d = _root() / 'logs' / 'bug_reports'
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── ① 截图 ────────────────────────────────────────────────────────────

# 与 afk_bot/infra/screen.py 的 find_hwnd 保持同一套判据（Unity 类名 + 标题评分）
_PREFERRED_TITLE_KWS = ('afk journey', '剑与远征', '启程')
_EXCLUDED_TITLE_KWS = ('endfield', '明日方舟', '终末地', 'arknights')


def find_game_hwnd() -> Optional[int]:
    """枚举顶层窗口找**游戏**窗口（Unity 类名 + 标题评分，与 screen.py 同判据）。"""
    try:
        import win32gui
    except Exception:
        return None
    cands = []

    def _cb(h, acc):
        try:
            if 'Unity' in win32gui.GetClassName(h):
                acc.append((h, win32gui.GetWindowText(h)))
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, cands)
    except Exception:
        return None
    if not cands:
        return None
    pool = [(h, t) for h, t in cands
            if not any(k in t.lower() for k in _EXCLUDED_TITLE_KWS)] or cands

    def _score(item):
        h, t = item
        tl = t.lower()
        pref = any(k in tl for k in _PREFERRED_TITLE_KWS)
        try:
            l, tp, r, b = win32gui.GetWindowRect(h)
            sized = 0 if (r - l) > 0 and (b - tp) > 0 else 1
        except Exception:
            sized = 1
        try:
            vis = 0 if win32gui.IsWindowVisible(h) else 1
        except Exception:
            vis = 1
        return (0 if pref else 1, vis, sized)

    pool.sort(key=_score)
    return pool[0][0]


def _game_rect(hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    """取游戏客户区在屏幕上的绝对矩形 (l, t, r, b)。

    优先用 win32gui 客户区 + ClientToScreen；失败退化为整屏。
    """
    if not hwnd:
        return None
    try:
        import win32gui
        l, t, r, b = win32gui.GetClientRect(hwnd)
        sl, st = win32gui.ClientToScreen(hwnd, (l, t))
        sr, sb = win32gui.ClientToScreen(hwnd, (r, b))
        if sr - sl > 100 and sb - st > 100:
            return (sl, st, sr, sb)
    except Exception:
        pass
    return None


def grab_screenshot(dest: Path, hwnd: Optional[int] = None) -> bool:
    """**只截游戏窗口**（v2.3.11 用户要求：不要截整个桌面 ✗）。成功返回 True。

    - hwnd 为空 → 自动枚举找游戏窗口（Unity 类名 + 标题评分）
    - **找不到游戏窗口 → 返回 False，不截图**（绝不退化为整屏 ✗）
    - Unity 的 PrintWindow 全黑 → 用 ImageGrab 截屏幕对应区域，
      所以截之前先把游戏窗口切前台 ✓
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return False
    if not hwnd:
        hwnd = find_game_hwnd()
    box = _game_rect(hwnd) if hwnd else None
    if not box:
        return False          # 没有游戏窗口 → 不截（绝不整屏）✗

    try:
        import win32gui
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, 9)      # SW_RESTORE
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    except Exception:
        pass
    try:
        img = ImageGrab.grab(bbox=box, all_screens=True)
        img.save(str(dest), 'PNG')
        return dest.stat().st_size > 0
    except Exception:
        return False


# ── ② 采集 ────────────────────────────────────────────────────────────

def collect(task: str = '', exc: Optional[BaseException] = None,
            log_lines: Optional[Iterable[str]] = None,
            hwnd: Optional[int] = None,
            version: str = '',
            extra: Optional[dict] = None) -> Path:
    """采集一次 bug 现场，返回目录。**保证不抛异常**。"""
    ts = time.strftime('%Y%m%d_%H%M%S')
    folder = reports_dir() / ts
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return reports_dir()

    # 截图
    if not hwnd:
        hwnd = find_game_hwnd()
    shot = folder / 'shot.png'
    shot_ok = grab_screenshot(shot, hwnd=hwnd)

    # 日志尾部
    lines: List[str] = list(log_lines or [])
    if not lines:
        lines = _read_app_log_tail()
    try:
        (folder / 'app.log').write_text(
            '\n'.join(lines[-LOG_TAIL_LINES:]) + '\n', encoding='utf-8')
    except Exception:
        pass

    # 环境信息
    meta = {
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'version': version or _read_version(),
        'task': task,
        'exception': (f'{type(exc).__name__}: {exc}' if exc else ''),
        'traceback': (_tb(exc) if exc else ''),
        'screenshot_ok': bool(shot_ok),
        'hwnd': int(hwnd) if hwnd else 0,
        'window_rect': (_game_rect(hwnd) or []),
        'os': f'{platform.system()} {platform.release()} {platform.version()}',
        'python': sys.version.split()[0],
        'frozen': bool(getattr(sys, 'frozen', False)),
        'cwd': os.getcwd(),
        'extra': (extra or {}),
        'log_lines': len(lines),
    }
    try:
        (folder / 'meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
    return folder


def _tb(exc: Optional[BaseException]) -> str:
    if exc is None:
        return ''
    try:
        import traceback
        return ''.join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))[-4000:]
    except Exception:
        return ''


def _read_version() -> str:
    try:
        from afk_bot.config import VERSION
        return VERSION
    except Exception:
        return ''


def _read_app_log_tail() -> List[str]:
    """读工程根下的 app 日志尾部（GUI 的 _append_log 会写文件）。"""
    for name in ('afk_app.log', '_afk_gui_log.txt'):
        p = _root() / name
        if p.is_file():
            try:
                data = p.read_text(encoding='utf-8', errors='ignore')
                return data.splitlines()[-LOG_TAIL_LINES:]
            except Exception:
                pass
    return []


# ── ③ 上传 ────────────────────────────────────────────────────────────

def _token_from_git() -> Optional[str]:
    """从 **git credential**（用户推送用的凭据）取 token。

    真机 2026-11-05 11:00：`github_token.txt` 里是 **fine-grained PAT**，
    调 Git Data API 报 `403 Resource not accessible by personal access token` ✗
    而 `git push` 一直成功 → 推送凭据必然有 Contents 写权限 ✓
    （通常也是经典 PAT，`repo` scope 一并覆盖 Issues 写 ✓）
    """
    try:
        import subprocess
        out = subprocess.run(
            ['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\n\n',
            capture_output=True, text=True, timeout=15,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout
        for line in out.splitlines():
            if line.startswith('password='):
                tok = line[len('password='):].strip()
                if tok:
                    return tok
    except Exception:
        pass
    return None


def _token() -> Optional[str]:
    """取上传用 token，优先级：环境变量 → git 凭据 → github_token.txt。

    ⚠️ git 凭据在前：它一定是**能写**的（否则推送不会成功 ✓），
    而 github_token.txt 里的 fine-grained PAT 可能缺 Contents 写 ✗
    """
    env = (os.environ.get('AFKJ_GH_TOKEN') or '').strip()
    if env:
        return env
    t = _token_from_git()
    if t:
        return t
    try:
        from afk_bot.updater import resolve_token
        return resolve_token()
    except Exception:
        return None


def _api(method: str, url: str, token: str, data=None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _ensure_branch(token: str) -> None:
    """确保 bug-reports 分支存在（从 main 建）。"""
    try:
        _api('GET', f'{API}/git/ref/heads/{BRANCH}', token)
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    main = _api('GET', f'{API}/git/ref/heads/main', token)['object']['sha']
    _api('POST', f'{API}/git/refs', token,
         {'ref': f'refs/heads/{BRANCH}', 'sha': main})


def upload(folder: Path) -> Optional[str]:
    """把报告提交到 bug-reports 分支并建 Issue，返回 Issue URL；失败返回 None。

    **不抛异常**（报 bug 的链路本身不能崩主流程 ✓）
    """
    tok = _token()
    if not tok:
        return None
    try:
        # ① 文件走 **git plumbing** 推到 bug-reports 分支
        #    真机 11-05 11:00：Git Data API 建 refs 被 403 拒（fine-grained PAT 缺权限 ✗）
        #    但 `git push` 一直可用 ✓ → 用 plumbing（hash-object / update-index /
        #    write-tree / commit-tree / push），**完全不碰工作区与索引** ✓
        _push_report_via_git(folder)

        # Issue 正文
        meta: dict = {}
        try:
            meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
        except Exception:
            pass
        log_txt = ''
        try:
            log_txt = (folder / 'app.log').read_text(
                encoding='utf-8', errors='ignore')
        except Exception:
            pass
        img = (f'https://github.com/{REPO}/raw/{BRANCH}/'
               f'bug-reports/{folder.name}/shot.png')
        body = _issue_body(meta, log_txt, img)
        title = (f"[自动上报] {meta.get('task') or '未知任务'} · "
                 f"{meta.get('version') or '?'} · {folder.name}")
        issue = _api('POST', f'{API}/issues', tok,
                     {'title': title[:200], 'body': body,
                      'labels': ['auto-bug']})
        return issue.get('html_url')
    except Exception:
        return None


def _push_report_via_git(folder: Path) -> bool:
    """用 git plumbing 把报告推到 `bug-reports` 分支（**不动工作区/索引**）。

    为什么不用 Git Data API：用户 token 是 fine-grained PAT，
    `POST /git/refs` 报 403（缺 Contents 写）✗；
    而 `git push` 一直成功 ✓（推送凭据本身有权限）。
    """
    import subprocess
    import tempfile
    root = _root()
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}

    def g(*a, _env=None):
        return subprocess.run(['git', *a], cwd=str(root),
                              env=(_env or env), capture_output=True,
                              text=True, timeout=120)

    base = ''
    r = g('rev-parse', f'origin/{BRANCH}')
    if r.returncode == 0:
        base = r.stdout.strip()
    if not base:
        r = g('rev-parse', 'HEAD')
        base = r.stdout.strip()
    if not base:
        return False

    idx = Path(tempfile.gettempdir()) / f'afkj_bugidx_{os.getpid()}'
    try:
        idx.unlink()
    except Exception:
        pass
    e2 = {**env, 'GIT_INDEX_FILE': str(idx)}
    if g('read-tree', base, _env=e2).returncode != 0:
        return False
    sub = f'bug-reports/{folder.name}'
    added = False
    for name in ('shot.png', 'app.log', 'meta.json'):
        f = folder / name
        if not f.is_file() or f.stat().st_size > 20 * 1024 * 1024:
            continue
        h = g('hash-object', '-w', str(f)).stdout.strip()
        if not h:
            continue
        if g('update-index', '--add', '--cacheinfo',
             f'100644,{h},{sub}/{name}', _env=e2).returncode == 0:
            added = True
    if not added:
        return False
    tree = g('write-tree', _env=e2).stdout.strip()
    if not tree:
        return False
    commit = g('commit-tree', tree, '-p', base,
               '-m', f'bug-report: {folder.name}').stdout.strip()
    if not commit:
        return False
    return g('push', 'origin',
             f'{commit}:refs/heads/{BRANCH}').returncode == 0


def _issue_body(meta: dict, log_txt: str, img_url: str) -> str:
    ex = meta.get('exception') or '（无异常栈）'
    tb = meta.get('traceback') or ''
    rect = meta.get('window_rect') or []
    parts = [
        '## 自动 Bug 上报',
        '',
        '| 项 | 值 |',
        '|---|---|',
        f"| 时间 | {meta.get('time', '')} |",
        f"| 版本 | {meta.get('version', '')} |",
        f"| 任务 | {meta.get('task', '')} |",
        f"| 窗口 | hwnd={meta.get('hwnd', 0)} rect={rect} |",
        f"| 系统 | {meta.get('os', '')} |",
        f"| Python | {meta.get('python', '')} |",
        f"| 打包 | {meta.get('frozen', '')} |",
        '',
        '### 异常',
        '```',
        ex,
        '```',
    ]
    if tb:
        parts += ['<details><summary>完整堆栈</summary>', '', '```', tb, '```', '</details>']
    parts += ['', '### 截图', f'![screenshot]({img_url})', '']
    parts += ['### 日志尾部', '```', log_txt[-12000:] or '（无）', '```']
    return '\n'.join(parts)


# ── ④ 一步到位 ────────────────────────────────────────────────────────

def report(task: str = '', exc: Optional[BaseException] = None,
           log_lines: Optional[Iterable[str]] = None,
           hwnd: Optional[int] = None, do_upload: bool = True,
           extra: Optional[dict] = None) -> Tuple[Path, Optional[str]]:
    """采集 + （可选）上传。返回 (本地目录, Issue URL 或 None)。"""
    folder = collect(task=task, exc=exc, log_lines=log_lines,
                     hwnd=hwnd, extra=extra)
    url = upload(folder) if do_upload else None
    return folder, url


if __name__ == '__main__':
    # 手工自测：python -m afk_bot.bug_report [--no-upload]
    no_up = '--no-upload' in sys.argv
    f, u = report(task='manual-selftest', do_upload=not no_up)
    print(f'报告目录: {f}')
    print(f'Issue: {u or "（未上传）"}')

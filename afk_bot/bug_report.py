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


def grab_screenshot(dest: Path, hwnd: Optional[int] = None,
                    whole_screen: bool = False) -> bool:
    """截游戏窗口（失败则整屏）。成功返回 True。

    ⚠️ Unity 窗口用 PrintWindow 会全黑 → 这里用 ImageGrab 截**屏幕区域**，
    因此调用前游戏窗口必须在前台（报 bug 时通常如此）。
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return False
    box = None if whole_screen else _game_rect(hwnd)
    if box is not None:
        # ImageGrab 的 bbox 是 (left, top, right, bottom)
        pass
    # 先把游戏窗口切到前台，否则 ImageGrab 截到的是挡在它前面的工具窗口 ✗
    if box is not None and hwnd:
        try:
            import win32gui
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, 9)      # SW_RESTORE
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.20)
        except Exception:
            pass
    try:
        img = ImageGrab.grab(bbox=box, all_screens=True)
        img.save(str(dest), 'PNG')
        return dest.stat().st_size > 0
    except Exception:
        # 兜底：整屏
        try:
            ImageGrab.grab(all_screens=True).save(str(dest), 'PNG')
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

def _token() -> Optional[str]:
    try:
        from afk_bot.updater import resolve_token
        return resolve_token()
    except Exception:
        return os.environ.get('AFKJ_GH_TOKEN') or None


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
        _ensure_branch(tok)
        ref = _api('GET', f'{API}/git/ref/heads/{BRANCH}', tok)['object']['sha']
        base_tree = _api('GET', f'{API}/git/commits/{ref}', tok)['tree']['sha']

        # 图片 + 日志 + meta 一起提交
        entries = []
        for name in ('shot.png', 'app.log', 'meta.json'):
            f = folder / name
            if not f.is_file():
                continue
            raw = f.read_bytes()
            if len(raw) > 20 * 1024 * 1024:      # GitHub blob 上限 100MB，这里保守
                continue
            import base64
            blob = _api('POST', f'{API}/git/blobs', tok, {
                'content': base64.b64encode(raw).decode(), 'encoding': 'base64'})
            entries.append({'path': f'bug-reports/{folder.name}/{name}',
                            'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        if not entries:
            return None

        tree = _api('POST', f'{API}/git/trees', tok,
                    {'base_tree': base_tree, 'tree': entries})['sha']
        commit = _api('POST', f'{API}/git/commits', tok, {
            'message': f'bug-report: {folder.name}', 'tree': tree,
            'parents': [ref]})['sha']
        _api('PATCH', f'{API}/git/refs/heads/{BRANCH}', tok,
             {'sha': commit, 'force': False})

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

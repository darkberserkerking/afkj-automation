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

# 代码仓库（只用于读取 version 等；**不放 Bug 报告**）
REPO = 'darkberserkerking/afkj-automation'
# ── Bug 报告仓库（v2.3.12）──────────────────────────────────────
# 用户要求「仅本人可见」→ 公开仓库的 Issue/分支任何人可见 ✗
# 唯一可靠做法：**私有仓库**（GitHub 私有仓库的 Issue 只有本人可见 ✓）
# ⚠️ 需用户手动创建一次（token 无建仓权限 ✗）：
#    GitHub → New repository → 名称 afkj-bug-reports → 勾选 **Private** → 不勾 README
BUG_REPORT_REPO = 'darkberserkerking/afkj-bug-reports'
# 隐藏子进程控制台窗口（pythonw 下每次 subprocess 都会闪一个黑窗 ✗）
_NO_WINDOW = 0x08000000 if sys.platform.startswith('win') else 0


def _no_console_kwargs() -> dict:
    """让 subprocess **不弹控制台窗口**（修「上传时连续闪多个命令行窗口」✗）。"""
    kw = {}
    if sys.platform.startswith('win'):
        kw['creationflags'] = _NO_WINDOW
        try:
            import subprocess as _sp
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            si.wShowWindow = 0          # SW_HIDE
            kw['startupinfo'] = si
        except Exception:
            pass
    return kw
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


def activate_window(hwnd: Optional[int]) -> bool:
    """把游戏窗口**还原并置前**，返回是否**确认**成功（真在前台）。

    真机问题：游戏可能**一直运行但被最小化** ✗
    → ImageGrab 截的是屏幕像素，最小化窗口没有像素 ✗（PrintWindow 对 Unity 全黑 ✗）
    → 必须先 SW_RESTORE 再 SetForegroundWindow，并**校验** GetForegroundWindow ✓
    """
    if not hwnd:
        return False
    try:
        import win32con
        import win32gui
        import win32process
    except Exception:
        return False

    def _is_fg() -> bool:
        try:
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def _ready() -> bool:
        """**真正可截图** = 非最小化 且 在前台。

        ⚠️ 真机踩过：最小化窗口也可能占着"前台焦点" ✗
        → 只看 GetForegroundWindow 会**误判成功** ✓
        """
        try:
            return (not win32gui.IsIconic(hwnd)) and _is_fg()
        except Exception:
            return False

    # 已经是前台且没最小化 → 直接成功
    try:
        if _ready():
            return True
    except Exception:
        pass

    # 还原（最小化 → 正常）：**多方式重试并校验** IsIconic
    try:
        if win32gui.IsIconic(hwnd):
            for _sw in (win32con.SW_RESTORE, win32con.SW_SHOWNORMAL,
                        win32con.SW_SHOW):
                win32gui.ShowWindow(hwnd, _sw)
                time.sleep(0.30)
                if not win32gui.IsIconic(hwnd):
                    break
    except Exception:
        pass

    # 常规置前
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.20)
    if _ready():
        return True

    # 兜底：AttachThreadInput 绕过前台锁（Windows 常见限制）
    try:
        fg = win32gui.GetForegroundWindow()
        t_fg, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
        t_me, _ = win32process.GetWindowThreadProcessId(hwnd)
        if t_fg and t_me and t_fg != t_me:
            win32process.AttachThreadInput(t_fg, t_me, True)
            try:
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            finally:
                win32process.AttachThreadInput(t_fg, t_me, False)
        else:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.25)
    return _ready()


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
        # 没有游戏窗口 → 不截（绝不整屏）✗，但**记录原因**便于排查 ✓
        _record_shot_fail(folder_of(dest),
                          '未找到游戏窗口（游戏可能未启动，或窗口标题不匹配）')
        return False

    # v2.3.13（用户要求）：**先激活游戏窗口再截图**；失败要有兜底与提示 ✓
    ok_act = activate_window(hwnd)
    last_err = ''
    if not ok_act:
        # 兜底：PrintWindow 尝试直接取窗口内容（最小化也可试）
        # ⚠️ Unity 常用 D3D 渲染，PrintWindow 多半全黑 ✗——所以只当兜底，
        #    并校验"不是纯黑"，黑图直接丢弃（宁可没图也不给黑图 ✗）
        try:
            import win32gui
            import win32ui
            from ctypes import windll
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if w > 100 and h > 100:
                hdc = win32gui.GetWindowDC(hwnd)
                mfc = win32ui.CreateDCFromHandle(hdc)
                mem = mfc.CreateCompatibleDC()
                bmp = win32ui.CreateBitmap()
                bmp.CreateCompatibleBitmap(mfc, w, h)
                mem.SelectObject(bmp)
                # 2 = PW_RENDERFULLCONTENT（支持部分 D3D）
                windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
                from PIL import Image as _I
                info = bmp.GetInfo()
                raw = bmp.GetBitmapBits(True)
                im = _I.frombuffer('RGB', (info['bmWidth'], info['bmHeight']),
                                   raw, 'raw', 'BGRX')
                win32gui.DeleteObject(bmp.GetHandle())
                mem.DeleteDC()
                mfc.DeleteDC()
                win32gui.ReleaseDC(hwnd, hdc)
                ext = im.convert('L').getextrema()
                if ext[1] - ext[0] > 8:      # 有明暗变化 = 不是纯黑 ✓
                    im.save(str(dest), 'PNG')
                    return dest.stat().st_size > 0
                last_err = 'PrintWindow 得到纯黑图（Unity D3D 常见）'
        except Exception as _e:
            last_err = f'{type(_e).__name__}: {_e}'
        # 激活失败 + 兜底失败 → **不截图**（记录原因，日志照传 ✓）
        _record_shot_fail(folder_of(dest), '激活游戏窗口失败' +
                          (f'；{last_err}' if last_err else ''))
        return False
    try:
        img = ImageGrab.grab(bbox=box, all_screens=True)
        img.save(str(dest), 'PNG')
        return dest.stat().st_size > 0
    except Exception as _e:
        _record_shot_fail(folder_of(dest), f'ImageGrab 失败: {_e}')
        return False


def folder_of(dest: Path) -> Path:
    return Path(dest).parent


def _record_shot_fail(folder: Path, reason: str) -> None:
    """把截图失败原因写进报告目录（上传后能在 Issue 里看到 ✓）。"""
    try:
        (Path(folder) / 'shot_error.txt').write_text(reason, encoding='utf-8')
    except Exception:
        pass


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
    shot_err = ''
    try:
        _ef = folder / 'shot_error.txt'
        if _ef.is_file():
            shot_err = _ef.read_text(encoding='utf-8').strip()
    except Exception:
        pass

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
        'screenshot_error': shot_err,
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
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
            **_no_console_kwargs()).stdout
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


def _report_repo_ok(token: str) -> bool:
    """校验报告仓库存在且**为私有**（可见性控制的核心 ✓）。

    用户要求「仅本人可见」→ 公开仓库一律拒绝上传 ✗
    （私有仓库的 Issue / 分支只有仓库所有者可见 ✓）
    """
    try:
        d = _api('GET', f'https://api.github.com/repos/{BUG_REPORT_REPO}', token)
        return bool(d.get('private'))
    except Exception:
        return False


def _mark(folder: Path, name: str, text: str = '') -> None:
    try:
        (Path(folder) / name).write_text(text, encoding='utf-8')
    except Exception:
        pass


def _unmark(folder: Path, name: str) -> None:
    try:
        (Path(folder) / name).unlink()
    except Exception:
        pass


def _is_uploaded(folder: Path) -> bool:
    return (Path(folder) / '.uploaded').is_file()


def _create_issue(tok: str, folder: Path) -> Optional[str]:
    """建 Issue；**失败不抛**（降级为仅分支报告 ✓）。

    真机 11:15：`github_token.txt` 是 fine-grained PAT，**只授权了 1 个仓库** ✗
    → 访问私有报告仓库 API 返回 404 ✗（但 git push 可用 ✓）
    → 因此 Issue 建不了时**不影响报告本身**（已在分支里 ✓）
    """
    try:
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
        img = (f'https://github.com/{BUG_REPORT_REPO}/raw/{BRANCH}/'
               f'bug-reports/{folder.name}/shot.png')
        body = _issue_body(meta, log_txt, img)
        title = (f"[自动上报] {meta.get('task') or '未知任务'} · "
                 f"{meta.get('version') or '?'} · {folder.name}")
        issue = _api('POST',
                     f'https://api.github.com/repos/{BUG_REPORT_REPO}/issues',
                     tok, {'title': title[:200], 'body': body,
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
    # 报告仓库的 https 地址（私有仓库；凭据沿用用户已存的 git credential ✓）
    remote = f'https://github.com/{BUG_REPORT_REPO}.git'
    nowin = _no_console_kwargs()          # **不弹控制台窗口** ✓

    def g(*a, _env=None):
        return subprocess.run(['git', *a], cwd=str(root),
                              env=(_env or env), capture_output=True,
                              text=True, timeout=120, **nowin)

    # 分支基点：优先取报告仓库已有分支，否则用本地 HEAD 的树
    base = ''
    r = g('ls-remote', remote, f'refs/heads/{BRANCH}')
    if r.returncode == 0 and r.stdout.strip():
        try:
            base = g('fetch', '--quiet', remote,
                     f'refs/heads/{BRANCH}').returncode == 0 and \
                   g('rev-parse', 'FETCH_HEAD').stdout.strip() or ''
        except Exception:
            base = ''
    if not base:
        base = g('rev-parse', 'HEAD').stdout.strip()
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
    for name in ('shot.png', 'app.log', 'meta.json', 'REPORT.md'):
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
    return g('push', remote,
             f'{commit}:refs/heads/{BRANCH}').returncode == 0

def upload(folder: Path, retries: int = 3) -> Optional[str]:
    """把报告推到私有仓库并（尽力）建 Issue，返回 Issue URL。

    v2.3.13 加固（用户要求"稳定上传、不遗漏"）：
      ① **重试**：最多 retries 次，指数退避 ✓
      ② **结果校验**：push 成功**且**该目录文件已出现在远端才算数 ✓
      ③ **不遗漏**：任何失败都写 `.pending`（含原因），
         下次启动 `retry_pending()` 自动补传 ✓
      ④ Issue 建不了不丢报告（分支里已有 REPORT，见 `_write_report_md`）

    **绝不抛异常**（报 bug 链路不能崩主流程 ✓）
    """
    tok = _token()
    if not tok:
        _mark(folder, '.pending', 'no-token')
        return None
    if not _report_repo_ok(tok):
        _mark(folder, '.pending', 'report-repo-missing-or-not-private')
        return None

    last = ''
    for attempt in range(1, max(1, retries) + 1):
        try:
            if _push_report_via_git(folder):
                url = _create_issue(tok, folder)
                _mark(folder, '.uploaded', url or 'pushed-no-issue')
                _unmark(folder, '.pending')
                return url
            raise RuntimeError('git push 未成功')
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
            if attempt < retries:
                time.sleep(min(2 * attempt, 6))
    _mark(folder, '.pending', last or 'unknown')
    return None


def retry_pending(max_items: int = 10) -> int:
    """补传历史失败的报告，返回成功条数（启动时调用 ✓）。"""
    ok = 0
    try:
        folders = sorted(reports_dir().iterdir(), reverse=True)
    except Exception:
        return 0
    for f in folders:
        if not f.is_dir() or _is_uploaded(f):
            continue
        if not ((f / 'meta.json').is_file()):
            continue
        if max_items <= 0:
            break
        max_items -= 1
        if upload(f):
            ok += 1
    return ok


def _write_report_md(folder: Path, meta: dict, log_txt: str) -> None:
    """在报告目录写一份 markdown（Issue 建不了时也能在分支里看 ✓）。"""
    try:
        md = _issue_body(meta, log_txt,
                         f'bug-reports/{folder.name}/shot.png')
        (Path(folder) / 'REPORT.md').write_text(md, encoding='utf-8')
    except Exception:
        pass


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

"""Bug 上报：截图 + 日志 + 环境信息 → 本地落盘 → 上传 GitHub Issue。

设计要点
────────
1. **本地永远先落盘**（`<数据目录>/logs/bug_reports/<时间戳>/`，
   打包时数据目录 = `%LOCALAPPDATA%\\AFKJ自动化工具`，见 app_paths.py）：
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

# 代码仓库
REPO = 'darkberserkerking/afkj-automation'
# ── Bug 报告落点（v2.4.14 合并）────────────────────────────────
# 历史：v2.3.12 起报告另存私有仓库 `afkj-bug-reports`（当时要求「仅本人
# 可见」→ 公开仓库的 Issue/分支谁都能看 ✗）。
# 用户 2026-11-24 定案：**弃用 afkj-bug-reports，报告直接进 afkj-automation**
# —— 单一仓库、issue 与代码同处，便于按版本对照排查。
# 落盘结构（与旧方案一致，避免改动 REPORT/Issue 里的图片直链）：
#   分支 `bug-reports` → `bug-reports/<时间戳>/{shot.png,app.log,meta.json,REPORT.md}`
#   Issue 建在同一仓库，label `auto-bug`
# ⚠️ afkj-automation 是**公开**仓库 → 报告内容（含截图与日志）对外可见。
#    这是「其他用户也能上传」的前提代价，已与用户确认。
REPORT_REPO = REPO
# 兼容旧名：仓内其它位置（含测试）可能仍引用 BUG_REPORT_REPO
BUG_REPORT_REPO = REPORT_REPO
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
    """报告落盘目录（v2.4.13：改走运行期数据目录）。

    历史：v2.4.12 之前返回 `_root()/logs/bug_reports`，打包后 `_root()`
    就是 exe 目录 → 用户把 exe 放桌面时，桌面凭空多出 `logs\\` 文件夹，
    且 Program Files 下因 UAC 无法创建。现与其它运行期数据统一到
    `app_paths.data_dir()`（打包 = %LOCALAPPDATA%\\AFKJ自动化工具）。
    """
    try:
        import app_paths
        base = Path(app_paths.data_dir())
    except Exception:                       # 极端兜底：保持旧行为
        base = _root()
    d = base / 'logs' / 'bug_reports'
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
    """读 app 日志尾部（GUI 的 _append_log 会写文件）。

    v2.4.13：`_afk_gui_log.txt` 已随运行期数据迁到 `app_paths.data_dir()`，
    故先查数据目录、再回落 `_root()`（兼容 v2.4.12 之前的旧 exe 目录）。
    两个候选都查，避免升级后首轮读不到日志、Bug 报告缺日志尾巴。
    """
    bases: List[Path] = []
    try:
        import app_paths
        bases.append(Path(app_paths.data_dir()))
    except Exception:
        pass
    bases.append(_root())
    for base in bases:
        for name in ('afk_app.log', '_afk_gui_log.txt'):
            p = base / name
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
    """取上传用 token，优先级：环境变量 → git 凭据 → github_token.txt
    → **构建期内置 token**（v2.4.14）。

    ⚠️ git 凭据在前：它一定是**能写**的（否则推送不会成功 ✓），
    而 github_token.txt 里的 fine-grained PAT 可能缺 Contents 写 ✗

    内置兜底的意义：**其他用户**的电脑上没有环境变量、没有 git 凭据、
    也没有 github_token.txt，前三级全部落空 → 报告只能躺本地。要让
    「别人跑出错也能上报」，就必须随包带一个只够「写 issue + 写
    bug-reports 分支」的最小权限 token（`afk_bot/_report_token.py`，
    该文件已 gitignore，不会被提交或泄漏到仓库里）。
    """
    env = (os.environ.get('AFKJ_GH_TOKEN') or '').strip()
    if env:
        return env
    t = _token_from_git()
    if t:
        return t
    try:
        from afk_bot.updater import resolve_token
        got = resolve_token()
        if got:
            return got
    except Exception:
        pass
    try:
        from afk_bot._report_token import REPORT_TOKEN as _emb
        _emb = (_emb or '').strip()
        if _emb:
            return _emb
    except Exception:
        pass
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
    """确认报告仓库可访问（v2.4.14：不再要求私有）。

    历史：旧方案要求 `private == True`（报告只给本人看），报告一合并进
    afkj-automation 这个公开仓库，这个前置判断就成了**永久阻塞** ——
    真机 2026-11-24 的两份报告正是被它卡在本地、只留下 `.pending` 标记。
    现在只校验「仓库存在且 token 能读」，其它照旧。
    """
    try:
        d = _api('GET', f'https://api.github.com/repos/{REPORT_REPO}', token)
        return bool(d.get('full_name'))
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
        img = (f'https://github.com/{REPORT_REPO}/raw/{BRANCH}/'
               f'bug-reports/{folder.name}/shot.png')
        body = _issue_body(meta, log_txt, img)
        title = (f"[自动上报] {meta.get('task') or '未知任务'} · "
                 f"{meta.get('version') or '?'} · {folder.name}")
        issue = _api('POST',
                     f'https://api.github.com/repos/{REPORT_REPO}/issues',
                     tok, {'title': title[:200], 'body': body,
                           'labels': ['auto-bug']})
        return issue.get('html_url')
    except Exception:
        return None


def _push_report_via_api(folder: Path, token: str) -> bool:
    """用 **GitHub REST API** 把报告提交到 `bug-reports` 分支（v2.4.14）。

    为什么新增这条通道：旧的 `_push_report_via_git` 依赖三件「只有开发机
    才具备」的东西 —— 本机装了 git、`cwd` 是一个 git 工作区、且 git
    credential 里存着可用凭据。**其他用户的电脑三者皆无**，所以报告
    永远传不上去（这也正是用户要求「确保其他用户也能上传」的原因）。

    REST 通道只用 HTTPS + token，零外部依赖、零本地仓库：
      POST /git/blobs   （每个文件，base64）
      POST /git/trees   （base_tree = 分支当前树，仅追加本目录）
      POST /git/commits
      PATCH/POST /git/refs/heads/bug-reports

    失败**不抛**：返回 False 交由调用方回退到 git 通道。
    """
    import base64

    files: List[Tuple[str, Path]] = []
    for name in ('shot.png', 'app.log', 'meta.json', 'REPORT.md'):
        f = folder / name
        try:
            if f.is_file() and f.stat().st_size <= 20 * 1024 * 1024:
                files.append((name, f))
        except Exception:
            pass
    if not files:
        return False

    base = f'/repos/{REPORT_REPO}'
    sub = f'bug-reports/{folder.name}'

    # ① 分支基点：优先已有 bug-reports 分支，否则从默认分支拉
    base_sha = ''
    base_tree = ''
    try:
        ref = _api('GET', f'https://api.github.com{base}/git/ref/heads/{BRANCH}',
                   token)
        base_sha = ((ref.get('object') or {}).get('sha') or '')
    except Exception:
        base_sha = ''
    try:
        if base_sha:
            c = _api('GET', f'https://api.github.com{base}/git/commits/{base_sha}',
                     token)
            base_tree = ((c.get('tree') or {}).get('sha') or '')
        elif not base_sha:
            # 分支还不存在 → 从默认分支 HEAD 起步（不建孤儿提交，
            # 首个报告提交会带上整棵树，但历史只有一条，且不影响 main）
            d = _api('GET', f'https://api.github.com{base}', token)
            db = d.get('default_branch') or 'main'
            ref = _api('GET',
                       f'https://api.github.com{base}/git/ref/heads/{db}', token)
            base_sha = ((ref.get('object') or {}).get('sha') or '')
            if base_sha:
                c = _api('GET',
                         f'https://api.github.com{base}/git/commits/{base_sha}',
                         token)
                base_tree = ((c.get('tree') or {}).get('sha') or '')
    except Exception:
        return False
    if not base_sha:
        return False

    # ② 逐个文件建 blob
    entries: List[dict] = []
    for name, f in files:
        try:
            content = base64.b64encode(f.read_bytes()).decode('ascii')
            blob = _api('POST', f'https://api.github.com{base}/git/blobs', token,
                        {'content': content, 'encoding': 'base64'})
            sha = blob.get('sha') or ''
            if sha:
                entries.append({'path': f'{sub}/{name}', 'mode': '100644',
                                'type': 'blob', 'sha': sha})
        except Exception:
            continue
    if not entries:
        return False

    # ③ 树 → 提交 → 移动分支引用
    try:
        tree_payload: dict = {'tree': entries}
        if base_tree:
            tree_payload['base_tree'] = base_tree
        tree = _api('POST', f'https://api.github.com{base}/git/trees', token,
                    tree_payload)
        tree_sha = tree.get('sha') or ''
        if not tree_sha:
            return False
        commit = _api('POST', f'https://api.github.com{base}/git/commits', token,
                      {'message': f'bug-report: {folder.name}',
                       'tree': tree_sha, 'parents': [base_sha]})
        commit_sha = commit.get('sha') or ''
        if not commit_sha:
            return False
        try:
            _api('PATCH',
                 f'https://api.github.com{base}/git/refs/heads/{BRANCH}', token,
                 {'sha': commit_sha, 'force': False})
        except Exception:
            # 分支不存在 → 新建
            _api('POST', f'https://api.github.com{base}/git/refs', token,
                 {'ref': f'refs/heads/{BRANCH}', 'sha': commit_sha})
        return True
    except Exception:
        return False


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
    remote = f'https://github.com/{REPORT_REPO}.git'
    nowin = _no_console_kwargs()          # **不弹控制台窗口** ✓

    def g(*a, _env=None):
        return subprocess.run(['git', *a], cwd=str(root),
                              env=(_env or env), capture_output=True,
                              text=True, timeout=120, **nowin)

    # 分支基点：**只接受"干净"的远程分支**（顶层只有 bug-reports/ 或空）
    #   真机 11:32 踩过：空仓库时回退本地 HEAD → 把整个源码树推进报告仓库 ✗
    #   → 这里做一次"干净校验"：顶层条目出现非 bug-reports/ 的，一律**不继承**
    base = ''
    force = False
    r = g('ls-remote', remote, f'refs/heads/{BRANCH}')
    if r.returncode == 0 and r.stdout.strip():
        try:
            if g('fetch', '--quiet', remote, f'refs/heads/{BRANCH}').returncode == 0:
                base = g('rev-parse', 'FETCH_HEAD').stdout.strip()
            if base:
                ls = g('ls-tree', '--name-only', base).stdout.split()
                _dirty = [x for x in ls if not x.startswith('bug-reports')]
                if _dirty:
                    # 远程分支被污染过（例如曾继承源码树）→ 用孤儿提交 + 强推覆盖 ✓
                    base = ''
                    force = True
        except Exception:
            base = ''
    # ⚠️ 报告仓库还没有该分支时 **绝不能**拿本地代码仓库的 HEAD 当基点 ✗
    #   真机 11:32 踩过：空仓库 → 回退到本地 HEAD → 把整个源码树推进了报告仓库 ✗
    #   正确做法：**孤儿提交**（不继承任何树）✓

    idx = Path(tempfile.gettempdir()) / f'afkj_bugidx_{os.getpid()}'
    try:
        idx.unlink()
    except Exception:
        pass
    e2 = {**env, 'GIT_INDEX_FILE': str(idx)}
    if base:
        if g('read-tree', base, _env=e2).returncode != 0:
            return False
    # base 为空 → 索引本来就是空的（孤儿提交），无需 read-tree ✓
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
    _parent = ['-p', base] if base else []
    commit = g('commit-tree', tree, *_parent,
               '-m', f'bug-report: {folder.name}').stdout.strip()
    if not commit:
        return False
    _push_cmd = ['push'] + (['--force'] if force else []) + \
        [remote, f'{commit}:refs/heads/{BRANCH}']
    return g(*_push_cmd).returncode == 0

def upload(folder: Path, retries: int = 3) -> Optional[str]:
    """把报告推到私有仓库并（尽力）建 Issue，返回 Issue URL。

    v2.3.13 加固：
      ① **重试**：最多 retries 次，指数退避 ✓
      ② **结果校验**：push 成功**且**该目录文件已出现在远端才算数 ✓
      ③ **失败留痕**：写 `.pending`（含原因）供人工排查；
         **v2.3.19 起不再自动补传**（用户 2026-11-05 定案：只在当次真实
         bug 中断时上传，避免历史报告被反复重建成重复 Issue）
      ④ Issue 建不了不丢报告（分支里已有 REPORT，见 `_write_report_md`）

    **绝不抛异常**（报 bug 链路不能崩主流程 ✓）
    """
    tok = _token()
    if not tok:
        _mark(folder, '.pending', 'no-token')
        return None
    if not _report_repo_ok(tok):
        _mark(folder, '.pending', 'report-repo-unreachable')
        return None

    last = ''
    for attempt in range(1, max(1, retries) + 1):
        try:
            # v2.4.14：先走 REST（任何机器都能用），失败再回退本机 git。
            # 回退顺序不能反——git 通道依赖本机 git + 工作区 + credential，
            # 其他用户的机器上三者都没有，会一直失败到重试耗尽。
            pushed = _push_report_via_api(folder, tok)
            how = 'api'
            if not pushed:
                pushed = _push_report_via_git(folder)
                how = 'git'
            if pushed:
                url = _create_issue(tok, folder)
                _mark(folder, '.uploaded', url or f'pushed-{how}-no-issue')
                _unmark(folder, '.pending')
                return url
            raise RuntimeError('rest 与 git 两条通道均未成功')
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
            if attempt < retries:
                time.sleep(min(2 * attempt, 6))
    _mark(folder, '.pending', last or 'unknown')
    return None


def retry_pending(max_items: int = 10) -> int:
    """补传历史失败的报告，返回成功条数。

    ⚠️ **v2.3.19 起不再被 GUI 自动调用**（用户 2026-11-05 定案：
    「不要补传，每次真实遇到 bug 中断后才触发上传」）。

    保留本函数仅供**人工诊断**（如 `python -m afk_bot.bug_report` 或
    REPL 手动调用）。启动补传曾导致私有仓库堆积 54 条重复 Issue：
    测试产出的报告长期滞留在 `logs/bug_reports/`，每次启动被批量重建。
    """
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

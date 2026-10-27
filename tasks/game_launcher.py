"""游戏启动/关闭底层工具（v1.9.68，用户 18:01 需求）。

────────────────────────────────────────────
【跨环境路径适配方案】（三级解析，无硬编码）
  优先级 1：环境变量 `AFKJ_LAUNCHER_PATH`（部署/CI 用，最高优先）
  优先级 2：配置文件 `game_paths.json`（项目根，用户可编辑，随包分发
            一份默认模板；字段 launcher_path / launcher_args）
  优先级 3：常见安装目录自动探测（各盘符 × Farlight Launcher 目录）
  优先级 4：开始菜单快捷方式解析（.lnk → 目标路径，pywin32 COM）
  全部失败 → 返回 None（任务日志给出明确配置指引，不猜测）
  启动参数默认值来自模块常量，可在 game_paths.json 覆盖。

【状态检测方式】
  主检测：枚举顶层窗口（复用 afk_bot.infra.screen.find_hwnd 的 Unity
          类名 + 偏好/排除标题规则）→ 找到 Unity 游戏窗口 = 已启动；
          未找到 = 未启动。
  兜底检测：进程列表匹配（Launcher 进程名关键字）——窗口枚举异常时用。

【关闭流程】
  ① PostMessage(hwnd, WM_CLOSE) 优雅关闭（等同点右上角 ×）
  ② 轮询窗口消失（≤ 15s）
  ③ 仍存在 → 取窗口 PID，taskkill /PID /F 兜底强杀
────────────────────────────────────────────
"""

import json
import os
import sys
import subprocess
import time
from typing import Callable, List, Optional

from afk_bot.infra.timing import sleep_interruptible

# ── 默认值（可被 game_paths.json / 环境变量覆盖）────────────────
DEFAULT_LAUNCHER_NAME: str = 'Launcher.exe'
DEFAULT_LAUNCHER_DIR: str = r'D:\Farlight Launcher'
DEFAULT_LAUNCH_ARGS: List[str] = [
    '--envId=prod8bef2ba4a37b2d80540cd3f97000',
    '--channel=FarlightOfficial',
]
ENV_LAUNCHER_PATH: str = 'AFKJ_LAUNCHER_PATH'
CONFIG_FILENAME: str = 'game_paths.json'
# 常见安装目录探测（盘符 × 目录名；用户可改 game_paths.json）
_PROBE_DRIVES: tuple = ('D:', 'C:', 'E:', 'F:', 'G:')
_PROBE_DIRS: tuple = ('Farlight Launcher', r'Program Files\Farlight Launcher',
                      r'Program Files (x86)\Farlight Launcher',
                      'FarlightLauncher', 'Farlight')
# 游戏窗口标题关键字（严格判据，v2.1.0）
_GAME_TITLE_KWS: tuple = ('afk journey', '剑与远征', '启程')
# 明确不属于游戏的窗口（启动器/加载器）——即使同样基于 Unity 也不能算
_NOT_GAME_TITLE_KWS: tuple = ('farlight', 'launcher', 'loader')
# 进程兜底关键字：**必须带 .exe 后缀精确匹配**（v2.1.0）——
# 裸 "afkj" 会命中工具自身进程「AFKJ自动化工具」，「farlight」会命中
# 常驻启动器，两者都会造成误判
_GAME_PROC_KWS: tuple = ('afkjourney.exe', 'afk journey.exe')


def project_root() -> str:
    """项目根目录（本文件在 tasks/ 下，上一级即根）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path() -> str:
    """配置文件路径（v1.9.68b：打包后为 exe 同目录，用户可直接编辑；
    开发环境为项目根）。"""
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable),
                            CONFIG_FILENAME)
    return os.path.join(project_root(), CONFIG_FILENAME)


def ensure_config_template() -> None:
    """配置文件不存在时写出默认模板（含示例路径，用户可编辑）。"""
    p = config_path()
    if os.path.exists(p):
        return
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({
                "launcher_path": os.path.join(DEFAULT_LAUNCHER_DIR,
                                              DEFAULT_LAUNCHER_NAME),
                "launcher_args": list(DEFAULT_LAUNCH_ARGS),
                "_comment": ("启动器路径配置：也可用环境变量 "
                             "AFKJ_LAUNCHER_PATH 覆盖；路径不存在时"
                             "自动探测常见安装目录")
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_config() -> dict:
    """读取 game_paths.json（缺失/损坏 → 空 dict，不抛异常）。"""
    p = config_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_launcher_path() -> Optional[str]:
    """三级解析启动器路径：环境变量 > 配置文件 > 常见目录探测 > 快捷方式。"""
    ensure_config_template()
    # ① 环境变量
    env_p = os.environ.get(ENV_LAUNCHER_PATH, '').strip()
    if env_p and os.path.exists(env_p):
        return env_p
    # ② 配置文件
    cfg = _load_config()
    cfg_p = str(cfg.get('launcher_path', '')).strip()
    if cfg_p and os.path.exists(cfg_p):
        return cfg_p
    # ③ 常见目录探测
    for drive in _PROBE_DRIVES:
        for d in _PROBE_DIRS:
            cand = os.path.join(drive + os.sep, d, DEFAULT_LAUNCHER_NAME)
            if os.path.exists(cand):
                return cand
    # ④ 开始菜单快捷方式（.lnk）解析
    lnk = _resolve_from_start_menu()
    if lnk:
        return lnk
    return None


def _resolve_from_start_menu() -> Optional[str]:
    """解析开始菜单 .lnk 指向的 Launcher.exe（pywin32 COM，异常静默）。"""
    try:
        import glob
        import win32com.client  # type: ignore
        pats = []
        for base in (
            os.path.join(os.environ.get('APPDATA', ''),
                         r'Microsoft\Windows\Start Menu\Programs'),
            r'C:\ProgramData\Microsoft\Windows\Start Menu\Programs',
        ):
            if base and os.path.isdir(base):
                pats.append(os.path.join(base, '**', '*.lnk'))
        shell = win32com.client.Dispatch('WScript.Shell')
        for pat in pats:
            for lnk in glob.glob(pat, recursive=True):
                if 'farlight' not in os.path.basename(lnk).lower():
                    continue
                try:
                    target = shell.CreateShortcut(lnk).TargetPath
                    if (target and target.lower().endswith('.exe')
                            and os.path.exists(target)
                            and 'farlight' in target.lower()):
                        return target
                except Exception:
                    continue
    except Exception:
        pass
    return None


def resolve_launch_args() -> List[str]:
    """启动参数（配置文件可覆盖）。"""
    cfg = _load_config()
    args = cfg.get('launcher_args')
    if isinstance(args, list) and all(isinstance(a, str) for a in args):
        return args
    return list(DEFAULT_LAUNCH_ARGS)


def find_game_window(bot=None) -> Optional[int]:
    """严格查找游戏窗口（v2.1.0）。

    判据：顶层窗口标题**命中游戏词**（afk journey/剑与远征/启程）
    且不含启动器词（farlight/launcher/loader）。
    ——不再使用「Unity 类名即认」的宽判据：Farlight Launcher、其他
    Unity 程序、无标题窗口都会误判（真机 20:22 启动游戏被判「已在运行」）。
    """
    try:
        import win32gui  # type: ignore
        found: List[int] = []

        def cb(h, r):
            try:
                t = (win32gui.GetWindowText(h) or "").lower()
                if not t:
                    return
                if any(k in t for k in _NOT_GAME_TITLE_KWS):
                    return
                if any(k in t for k in _GAME_TITLE_KWS):
                    r.append(h)
            except Exception:
                pass

        win32gui.EnumWindows(cb, found)
        return found[0] if found else None
    except Exception:
        return None


def is_game_running(bot=None, ignore_process: bool = False) -> bool:
    """游戏是否在运行。

    Args:
        bot: 可选，复用其 find_hwnd（窗口枚举）
        ignore_process: v2.0.9 —— True 时**只看窗口**（关闭验证用）。
            进程兜底会受常驻启动器影响，关闭后判定必须只看窗口。
    """
    """游戏运行检测：Unity 窗口存在 → True；异常时进程兜底。"""
    # ① 窗口（主检测，v2.1.0 严格标题判据）
    try:
        if find_game_window(bot):
            return True
    except Exception:
        pass

    # ② 进程兜底
    # ② 进程兜底（关闭验证时跳过——启动器常驻会误判）
    if ignore_process:
        return False
    try:
        out = subprocess.run(
            ['tasklist', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=10,
            encoding='gbk', errors='ignore',   # v2.1.0：中文系统
            # 输出为 GBK，此前 utf-8 解码抛异常被吞（进程兜底实际失效）
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ).stdout
        out = (out or "").lower()
        for kw in _GAME_PROC_KWS:
            if kw in out:
                return True
    except Exception:
        pass
    return False


def get_game_hwnd(bot=None) -> Optional[int]:
    """游戏窗口句柄（v2.1.0：与 is_game_running 同源严格判据）。"""
    return find_game_window(bot)


def is_game_ready(bot=None, min_w: int = 800, min_h: int = 600) -> bool:
    """游戏画面是否就绪（窗口存在 **且尺寸有效**）。

    v2.0.2（真机 18:26）：启动器拉起后窗口很快出现但游戏仍在加载——
    此时尺寸为 0（或极小），截图 0 → OCR 全跳过。仅「窗口存在」判成功
    会让后续任务在加载期空转失败。
    v2.0.6：改用 **GetClientRect（客户区）**——最小化窗口的
    GetWindowRect 仍返回还原后矩形（会误判就绪），客户区才是能否
    截图的直接判据；读数异常时退回窗口矩形。
    v2.1.0：窗口定位改**严格标题判据**（find_game_window）。
    """
    hwnd = find_game_window(bot)
    if not hwnd:
        return False
    try:
        import win32gui  # type: ignore
        # v2.2.5（真机 17:47）：**最小化窗口的 GetClientRect 仍返回还原
        # 尺寸** → 只看尺寸会把"已最小化"误判成就绪（真机：报就绪后下一
        # 任务连续 75s 截图 0×0、导航空转到超时）。IsIconic 才是直判据。
        if win32gui.IsIconic(hwnd):
            return False
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        cw, ch = cr - cl, cb - ct
        if cw > 0 and ch > 0:
            return cw >= min_w and ch >= min_h
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return (r - l) >= min_w and (b - t) >= min_h
    except Exception:
        return False

def launch_game(path: str, args: List[str]) -> bool:
    """启动启动器（分离进程，不阻塞；工作目录=启动器目录）。"""
    try:
        cwd = os.path.dirname(path) or None
        subprocess.Popen([path, *args], cwd=cwd,
                         close_fds=True,
                         creationflags=getattr(subprocess,
                                               'CREATE_NO_WINDOW', 0))
        return True
    except Exception:
        return False


def close_game(hwnd: Optional[int] = None,
               should_stop: Optional[Callable[[], bool]] = None) -> bool:
    """关闭游戏：WM_CLOSE 优雅 → 等待 → taskkill 兜底。

    v2.1.27（P1-3）：等待改为**分段可中断** —— 此前裸 `time.sleep(0.5)` ×30，
    用户点「停止」后最坏还要等满 15 秒才响应。

    Args:
        hwnd: 游戏窗口句柄；None 视为未启动
        should_stop: 停止判定回调；被中断时**不执行强杀**（用户正在接管，
            不应把游戏强关掉），直接返回 False 由调用方走停止分支

    Returns:
        bool: 关闭动作是否成功发出并最终窗口消失/进程结束
    """
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception:
        return False
    if hwnd is None:
        return True   # 无窗口（未启动）→ 视为已关闭
    # ① 优雅关闭
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass
    # ② 等待消失 ≤15s（v2.1.27 P1-3：分段可中断）
    for _ in range(30):
        if not sleep_interruptible(0.5, should_stop):
            return False
        try:
            if not win32gui.IsWindow(hwnd):
                return True
        except Exception:
            return True
    # ③ 强杀兜底（按窗口 PID）
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess,
                                             'CREATE_NO_WINDOW', 0))
        return True
    except Exception:
        return False

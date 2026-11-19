"""源码版启动器（带管理员清单）。

为什么要这个 exe：
    .bat / pythonw 没有"应用清单"，Windows 不会给它们自动提权；
    而部分机器对 bat 的 runas 提权会被静默拒绝（实测：无弹窗、bat 体不执行）。
    本项目的主 exe 之所以一直是管理员，正是因为它带 requireAdministrator 清单。
    → 所以这里用**同样的机制**：一个极小的 launcher exe + --uac-admin 清单，
      双击时由 Windows 弹 UAC（与主 exe 行为一致），授权后再拉起源码版。

打包命令（仅首次/清单变更时才需要重新打包，与主程序发版无关）：
    pyinstaller --onefile --noconsole --uac-admin --clean \
        --name "AFKJ源码启动器" --icon afk_journey.ico \
        tools/launch_source_admin.py

行为：
    1) 定位项目根（本文件上两级；打包后取 AFKJ_SOURCE_DIR 或 exe 所在目录）
    2) 自动探测装好依赖的 pythonw（AFKJ_PYTHON → PATH → conda envs）
    3) 切到项目根，用 pythonw 启动 afk_gui.py（不阻塞、不留黑窗）
    4) 立刻退出（真正跑起来的是源码进程）

环境变量：
    AFKJ_SOURCE_DIR   项目根（只在打包成 exe 且不在仓库内时需要）
    AFKJ_PYTHON       pythonw.exe 的绝对路径（优先级最高）
"""
from __future__ import annotations

import os
import subprocess
import sys

ENTRY = "afk_gui.py"

#: 候选解释器必须能 import 这些模块，否则视为"不是项目环境"
_PROBE = "import customtkinter, pyautogui, onnxruntime, cv2, win32gui, pyclipper, shapely"


def _repo_root() -> str:
    """定位项目根：环境变量 > 源码运行时上两级 > 打包后 exe 所在目录。"""
    env = os.environ.get("AFKJ_SOURCE_DIR", "").strip()
    if env and os.path.isfile(os.path.join(env, ENTRY)):
        return os.path.abspath(env)

    if not getattr(sys, "frozen", False):
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(here)

    # 打包后：优先 exe 同级（launcher 放在仓库根时成立）
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.isfile(os.path.join(exe_dir, ENTRY)):
        return exe_dir
    return ""


def _works(python: str) -> bool:
    """该解释器能 import 项目依赖吗。"""
    try:
        proc = subprocess.run(
            [python, "-c", _PROBE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except Exception:
        return False


def _conda_env_pythons() -> list[str]:
    """从 conda 安装根枚举各环境的 pythonw.exe（不写死盘符）。"""
    import shutil

    found: list[str] = []
    for name in ("conda.bat", "conda.exe", "conda"):
        conda = shutil.which(name)
        if not conda:
            continue
        # <root>/Scripts/conda.* 或 <root>/Library/bin/conda.bat
        root = os.path.abspath(os.path.join(os.path.dirname(conda), "..", ".."))
        envs = os.path.join(root, "envs")
        if not os.path.isdir(envs):
            continue
        for env_name in sorted(os.listdir(envs)):
            cand = os.path.join(envs, env_name, "pythonw.exe")
            if os.path.isfile(cand):
                found.append(cand)
        if found:
            break
    return found


def find_pythonw() -> str:
    """按 AFKJ_PYTHON → PATH → conda envs 的顺序找一个可用解释器。"""
    override = os.environ.get("AFKJ_PYTHON", "").strip()
    if override and os.path.isfile(override) and _works(override):
        return override

    import shutil

    on_path = shutil.which("pythonw") or shutil.which("pythonw.exe")
    if on_path and _works(on_path):
        return on_path

    for cand in _conda_env_pythons():
        if _works(cand):
            return cand
    return ""


def main() -> int:
    log_dir = os.environ.get("TEMP", ".")
    log_path = os.path.join(log_dir, "afkj_launcher_exe.log")

    def log(msg: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    try:
        import ctypes
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False
    log(f"[launcher] admin={is_admin}")

    project_dir = _repo_root()
    if not project_dir:
        log("[launcher] ERROR repo root not found "
            "(set AFKJ_SOURCE_DIR, or place this launcher in the repo root)")
        return 1

    pythonw = find_pythonw()
    if not pythonw:
        log("[launcher] ERROR no pythonw with project deps found "
            "(set AFKJ_PYTHON, or activate the env before launching)")
        return 1

    entry = os.path.join(project_dir, ENTRY)
    if not os.path.isfile(entry):
        log(f"[launcher] ERROR entry missing: {entry}")
        return 1

    try:
        os.chdir(project_dir)
        subprocess.Popen(
            [pythonw, ENTRY],
            cwd=project_dir,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log(f"[launcher] spawned {pythonw} {ENTRY} in {project_dir}")
        return 0
    except Exception as e:            # 兜底：把异常写日志而不是静默消失
        log(f"[launcher] EXCEPTION {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

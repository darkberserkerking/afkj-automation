"""源码版启动器（带管理员清单）。

为什么要这个 exe：
    .bat / pythonw 没有"应用清单"，Windows 不会给它们自动提权；
    而本机对 bat 的 runas 提权会被静默拒绝（实测：无弹窗、bat 体不执行）。
    本项目的主 exe 之所以一直是管理员，正是因为它带 requireAdministrator 清单。
    → 所以这里用**同样的机制**：一个极小的 launcher exe + --uac-admin 清单，
      双击时由 Windows 弹 UAC（与主 exe 行为一致），授权后再拉起源码版。

打包命令（仅首次/清单变更时才需要重新打包，与主程序发版无关）：
    pyinstaller --onefile --noconsole --uac-admin --clean \
        --name "AFKJ源码启动器" --icon afk_journey.ico \
        tools/launch_source_admin.py

行为：
    1) 切到项目目录（模板/模型相对路径才不会错）
    2) 用 pythonw 启动 afk_gui.py（不阻塞、不留黑窗）
    3) 立刻退出（真正跑起来的是源码进程）
"""
from __future__ import annotations

import os
import subprocess
import sys

PROJECT_DIR = r"C:\Users\x9502\WorkBuddy\20260320211537"
PYTHONW = r"D:\anaconda3\envs\afk_gpu\pythonw.exe"
ENTRY = "afk_gui.py"


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

    if not os.path.isfile(PYTHONW):
        log(f"[launcher] ERROR pythonw missing: {PYTHONW}")
        return 1
    entry = os.path.join(PROJECT_DIR, ENTRY)
    if not os.path.isfile(entry):
        log(f"[launcher] ERROR entry missing: {entry}")
        return 1

    try:
        os.chdir(PROJECT_DIR)
        subprocess.Popen(
            [PYTHONW, ENTRY],
            cwd=PROJECT_DIR,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log("[launcher] spawned pythonw afk_gui.py")
        return 0
    except Exception as e:            # 兜底：把异常写日志而不是静默消失
        log(f"[launcher] EXCEPTION {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

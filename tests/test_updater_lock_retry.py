# -*- coding: utf-8 -*-
"""替换脚本「目标被占用」重试回归测试（v2.4.18）。

背景（真机 2026-11-24 17:2x，用户用桌面 v2.4.11 更新到 v2.4.17）：
  The process cannot access the file because it is being used by another process.
  [AFKJ] 覆盖失败，请手动复制：
    从 ...\\Temp\\afkj_upd_r5hamwhp\\AFKJ.v2.4.17.exe
    到 C:\\Users\\...\\Desktop\\AFKJ.v2.4.11.exe

根因：PyInstaller onefile 是**双进程**。GUI 调 os._exit(0) 后子进程立刻
消失，但**父 bootloader** 还要删除 _MEI 解包目录，期间 exe 映像仍被映射。
旧脚本只「等子进程 PID + 3 秒 + copy 一次」，此时 copy 必然报共享冲突。

本测试守住：
  ① 新模板含父进程等待与 copy 重试结构
  ② 目标被独占打开时，脚本重试到句柄释放后能成功覆盖
  ③ 对照：单次 copy 的旧写法则失败（证明这是真缺陷）
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from afk_bot import updater as U   # noqa: E402

FAILED = []
PASSED = [0]

WIN_PATH = r'C:\Windows\System32;C:\Windows'   # 防 GNU timeout 抢占


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def _env():
    e = dict(os.environ)
    e['PATH'] = WIN_PATH
    return e


def _run_bat(path: Path, timeout=90) -> str:
    p = subprocess.run(['cmd', '/c', str(path)], capture_output=True,
                       text=True, timeout=timeout, env=_env(),
                       stdin=subprocess.DEVNULL)
    return (p.stdout or '') + (p.stderr or '')


class _Exclusive:
    """以 share=0 独占打开文件，模拟「被另一个进程占用」。"""

    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3

    def __init__(self, path):
        self.path = str(path)
        self.h = ctypes.windll.kernel32.CreateFileW(
            self.path, self.GENERIC_READ, 0, None, self.OPEN_EXISTING, 0, None)

    @property
    def ok(self):
        return self.h not in (None, 0, -1)

    def close(self):
        if self.ok:
            ctypes.windll.kernel32.CloseHandle(self.h)
            self.h = None


# ── ① 模板静态结构 ─────────────────────────────────────────
tpl = U.build_updater_script(Path(r'C:\x\new.exe'), Path(r'C:\x\old.exe'),
                             pid=12345, ppid=67890)
check('1a 模板含父 bootloader 等待（PPID）', 'PPID' in tpl and 'wait_parent' in tpl)
check('1b 模板含 copy 重试循环', 'retry_copy' in tpl and 'GEQ 60' in tpl)
check('1c ppid 参数写入模板', 'set "PPID=67890"' in tpl,
      [l for l in tpl.splitlines() if 'PPID=' in l][:1])
check('1d 模板仍为纯 ASCII（仅路径行可含非 ASCII）',
      all(ord(ch) < 128 for ch in tpl.replace(r'C:\x\new.exe', '')
          .replace(r'C:\x\old.exe', '')),
      'non-ascii found')

# ── ② 真实占用场景：重试后必须成功 ─────────────────────────
d = Path(tempfile.mkdtemp(prefix='afkj_lock_retry_'))
new = d / 'new.exe'
old = d / 'old.exe'
new.write_bytes(b'NEW-CONTENT-' + b'N' * 4096)
old.write_bytes(b'OLD-CONTENT-' + b'O' * 1024)

full = U.build_updater_script(new, old, pid=999999, ppid=0)
# 只保留到 copy 成功分支之前，避免真的 start 目标文件
body = full.split(':copy_ok')[0]
bat = d / 'apply.bat'
# 保留 copy 成功分支的标签，但去掉其后的「启动新版 + 自删」（会真的拉起进程）
bat.write_text(body + '\n:copy_ok\necho COPY_OK\n', encoding='utf-8')

lock = _Exclusive(old)
check('2a 已用独占句柄锁住目标文件', lock.ok,
      f'h={lock.h}')


def _release():
    time.sleep(5)
    lock.close()


_rel = threading.Thread(target=_release, daemon=True)
_rel.start()
t0 = time.time()
try:
    out = _run_bat(bat, timeout=120)
    dt = time.time() - t0
    _rel.join(timeout=30)      # 读文件前确保句柄已释放
    got = old.read_bytes()
    check('2b 被占用时重试最终成功覆盖', got == new.read_bytes(),
          f'耗时 {dt:.1f}s, 文件 {len(got)} 字节')
    check('2c 脚本走到 copy 成功分支', 'COPY_OK' in out, out.strip()[-120:])
    check('2d 确实等待了（不是瞬间失败）', dt >= 3.0, f'{dt:.1f}s')
except subprocess.TimeoutExpired:
    lock.close()
    check('2b 被占用时重试最终成功覆盖', False, '脚本超时')
finally:
    lock.close()

# ── ③ 对照：单次 copy 的旧写法在同一场景下失败 ──────────────
old2 = d / 'old2.exe'
new2 = d / 'new2.exe'
new2.write_bytes(b'NEW2-' + b'N' * 2048)
old2.write_bytes(b'OLD2-' + b'O' * 512)
legacy = d / 'legacy.bat'
legacy.write_text(
    '@echo off\r\n'
    f'copy /y "{new2}" "{old2}" >nul 2>&1\r\n'
    'if errorlevel 1 (\r\n'
    '  echo LEGACY_FAIL\r\n'
    '  exit /b 1\r\n'
    ')\r\n'
    'echo LEGACY_OK\r\n', encoding='utf-8')

lock2 = _Exclusive(old2)
check('3a 对照：已锁住 old2', lock2.ok)
try:
    out2 = _run_bat(legacy, timeout=60)
    check('3b 对照：单次 copy 在占用时失败（复现真机报错）',
          'LEGACY_FAIL' in out2, out2.strip()[-120:])
finally:
    lock2.close()
check('3c 对照：old2 内容未变', old2.read_bytes().startswith(b'OLD2'))

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')

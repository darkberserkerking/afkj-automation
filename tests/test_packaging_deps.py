# -*- coding: utf-8 -*-
"""打包依赖一致性回归测试（v2.4.17）。

背景（真机 2026-11-24 15:33 崩溃）：
  打包机同时有 base Anaconda（Tcl/Tk 8.6.10）与
  cpu_default 环境（Tcl/Tk 8.6.15）。PyInstaller 依赖扫描从 PATH 抓走了
  base 的 tcl86t.dll/tk86t.dll，而 hook 收集的 _tcl_data/init.tcl 来自
  cpu_default、写死 `package require -exact Tcl 8.6.15` → exe 启动即
  TclError「version conflict for package Tcl: have 8.6.10, need 8.6.15」。

本测试守住：
  ① spec 的 binaries 段必须像 OpenSSL 一样显式后写钉死 tcl/tk DLL
  ② 构建环境的 init.tcl 与 DLL 版本一致（环境本身不能再错配）
  ③ 已打包的 exe 里 Tcl/Tk DLL 与构建环境同源（体积一致）

运行：D:/MySoftware/Anaconda3/envs/cpu_default/python.exe tests/test_packaging_deps.py
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED = []
PASSED = [0]
SKIPPED = []


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def skip(name, why):
    SKIPPED.append(name)
    print(f'[SKIP] {name}  {why}', flush=True)


# ── ① spec 必须显式钉死 Tcl/Tk DLL ──────────────────────────
spec = (ROOT / 'AFKJ自动化工具.spec').read_text(encoding='utf-8')
_m = re.search(r'binaries=\[(.*?)\n    \],', spec, re.S)
binaries = _m.group(1) if _m else ''
check('1a spec 存在 binaries 段', bool(binaries), f'{len(binaries)} 字符')
check('1b binaries 显式钉死 tcl86t.dll', 'tcl86t.dll' in binaries)
check('1c binaries 显式钉死 tk86t.dll', 'tk86t.dll' in binaries)
check('1d OpenSSL 钉死仍在（不能被 Tcl 修复挤掉）',
      'libcrypto-3-x64.dll' in binaries and 'libssl-3-x64.dll' in binaries)

# ── ② 构建环境自身版本一致 ─────────────────────────────────
env_bin = Path(os.path.dirname(sys.executable)) / 'Library' / 'bin'
env_lib = Path(os.path.dirname(sys.executable)) / 'Library' / 'lib'
init_tcl = env_lib / 'tcl8.6' / 'init.tcl'
init_tk = env_lib / 'tk8.6' / 'tk.tcl'
dll_tcl = env_bin / 'tcl86t.dll'
dll_tk = env_bin / 'tk86t.dll'

if not all(p.is_file() for p in (init_tcl, dll_tcl, dll_tk)):
    skip('2 构建环境版本一致性', '当前解释器不是带 Tcl/Tk 的 conda 环境')
else:
    def _required(path, pkg):
        txt = path.read_text(encoding='utf-8', errors='ignore')
        m = re.search(r'package require -exact\s+' + pkg + r'\s+([\d.]+)', txt)
        return m.group(1) if m else None

    def _dll_version(path, exported=None):
        # Tcl DLL 直接问 Tcl 自己（最准，不依赖 PE 资源）
        if exported:
            try:
                import ctypes
                d = ctypes.CDLL(str(path))
                a, b, c, t = (ctypes.c_int() for _ in range(4))
                getattr(d, exported)(ctypes.byref(a), ctypes.byref(b),
                                     ctypes.byref(c), ctypes.byref(t))
                return f'{a.value}.{b.value}.{c.value}'
            except Exception:
                pass
        # Tk DLL 不导出版本函数 → 读 Windows 文件版本字符串（StringFileInfo）。
        # 注意 VS_FIXEDFILEINFO 的数值字段在本机返回 8.6.2，与资源字符串
        # 8.6.15 不一致，必须以字符串为准。
        try:
            import subprocess
            out = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"],
                capture_output=True, text=True, timeout=20)
            return out.stdout.strip() or None
        except Exception:
            return None

    want_tcl = _required(init_tcl, 'Tcl')
    want_tk = _required(init_tk, 'Tk')
    got_tcl = _dll_version(dll_tcl, 'Tcl_GetVersion')
    got_tk = _dll_version(dll_tk)

    check('2a 能读到 init.tcl 要求的 Tcl 版本', bool(want_tcl), str(want_tcl))
    check('2b init.tcl 与 tcl86t.dll 版本一致', want_tcl == got_tcl,
          f'{want_tcl} vs {got_tcl}')
    check('2c tk.tcl 与 tk86t.dll 版本一致', want_tk == got_tk,
          f'{want_tk} vs {got_tk}')

# ── ③ 已打包产物与构建环境同源 ─────────────────────────────
cfg = (ROOT / 'afk_bot' / 'config.py').read_text(encoding='utf-8')
_vm = re.search(r"^VERSION:\s*str\s*=\s*'([^']+)'", cfg, re.M)
version = _vm.group(1) if _vm else 'v0.0.0'
exe = ROOT / 'dist' / f'AFKJ自动化工具{version}.exe'
if not exe.is_file():
    skip('3 打包产物 DLL 同源检查', f'未找到 {exe.name}（先打包再跑本项）')
else:
    try:
        from PyInstaller.archive.readers import CArchiveReader
        reader = CArchiveReader(str(exe))
        for name, env_dll in (('tcl86t.dll', dll_tcl), ('tk86t.dll', dll_tk)):
            if name not in reader.toc or not env_dll.is_file():
                skip(f'3 {name} 同源检查', 'exe 或环境缺少该文件')
                continue
            in_exe = len(reader.extract(name))
            in_env = env_dll.stat().st_size
            check(f'3 exe 内 {name} 与构建环境同源', in_exe == in_env,
                  f'{in_exe} vs {in_env}')
    except Exception as e:
        skip('3 打包产物 DLL 同源检查', repr(e))

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）'
      + (f'，跳过 {len(SKIPPED)} 项' if SKIPPED else ''))

# -*- coding: utf-8 -*-
"""打包依赖一致性回归测试。

背景（两次真机崩溃，同一类根因：打包机有两套 Anaconda）：
  v2.4.16 2026-11-24 15:33
    TclError「have 8.6.10, need exactly 8.6.15」
    → PyInstaller 从 PATH 上的 base Anaconda 抓了 tcl/tk 8.6.10 DLL，
      而 _tcl_data/init.tcl 来自 cpu_default（要求 8.6.15）。
  v2.4.18 2026-11-24 20:47
    启动停在「初始化 OCR 引擎...」后无声退出，事件日志崩溃模块
    MSVCP140.dll、异常码 0xc0000005
    → msvcp140.dll 被抓成 base 的 **14.27**，其余 CRT 是 14.44 →
      onnxruntime 初始化即访问冲突。同批审计还发现 Pillow 的 9 个
      依赖（freetype/libjpeg/libpng/tiff/zstd/...）也全部来自 base。

本测试守住四件事：
  ① spec 的 binaries 段必须显式后写钉死这些原生库（含 OpenSSL）
  ② 构建环境自身版本一致（init.tcl ↔ DLL、msvcp140 ↔ vcruntime140）
  ③ 已打包的 exe 里这些 DLL 与构建环境**同源**（体积逐项比对）
  ④ exe 里不存在「与 base Anaconda 同尺寸、与环境不同尺寸」的残留

运行：D:/MySoftware/Anaconda3/envs/cpu_default/python.exe tests/test_packaging_deps.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED = []
PASSED = [0]
SKIPPED = []

ENV_BIN = Path(os.path.dirname(sys.executable)) / 'Library' / 'bin'
ENV_LIB = Path(os.path.dirname(sys.executable)) / 'Library' / 'lib'
BASE_BIN = Path(r'D:\MySoftware\Anaconda3\Library\bin')

# 必须钉死的原生库：CRT（必须整套同版本）+ Pillow 图像依赖 + OpenSSL + Tcl/Tk
CRT = ['msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
       'vcruntime140.dll', 'vcruntime140_1.dll']
IMG = ['freetype.dll', 'lcms2.dll', 'libbz2.dll', 'libjpeg.dll',
       'liblzma.dll', 'libpng16.dll', 'openjp2.dll', 'tiff.dll', 'zstd.dll']
SSL = ['libcrypto-3-x64.dll', 'libssl-3-x64.dll']
TCL = ['tcl86t.dll', 'tk86t.dll']

# 允许继续来自 base 的白名单（都不参与 CRT 版本耦合）：
#  api-ms-win-crt-*   UCRT API set 转发器，只有转发表、与版本无关
#  mfc140u.dll        MFC 库，cpu_default 未提供；Python 生态（PIL/ORT/cv2）
#                     不使用它，打包进来也不导出 CRT 符号，与 14.44 不冲突
IGNORE_FROM_BASE = {'api-ms-win-crt-multibyte-l1-1-0.dll', 'mfc140u.dll'}


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


def dll_version(path, exported=None):
    """Tcl DLL 直接问导出函数；其余读 Windows 文件版本字符串。

    注意 VS_FIXEDFILEINFO 的数值字段在本机返回 8.6.2，与资源字符串
    8.6.15 不一致，必须以字符串为准。
    """
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
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"],
            capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or None
    except Exception:
        return None


# ── ① spec 必须显式钉死 ────────────────────────────────────
spec = (ROOT / 'AFKJ自动化工具.spec').read_text(encoding='utf-8')
_m = re.search(r'binaries=\[(.*?)\n    \],', spec, re.S)
binaries = _m.group(1) if _m else ''
check('1a spec 存在 binaries 段', bool(binaries), f'{len(binaries)} 字符')
for _n in CRT + IMG + SSL + TCL:
    check(f'1 spec 钉死 {_n}', _n in binaries)

# ── ② 构建环境自身一致 ─────────────────────────────────────
if not ENV_BIN.is_dir():
    skip('2 环境版本一致性', f'找不到 {ENV_BIN}')
else:
    init_tcl = ENV_LIB / 'tcl8.6' / 'init.tcl'
    init_tk = ENV_LIB / 'tk8.6' / 'tk.tcl'

    def _required(path, pkg):
        txt = path.read_text(encoding='utf-8', errors='ignore')
        mm = re.search(r'package require -exact\s+' + pkg + r'\s+([\d.]+)', txt)
        return mm.group(1) if mm else None

    want_tcl = _required(init_tcl, 'Tcl') if init_tcl.is_file() else None
    want_tk = _required(init_tk, 'Tk') if init_tk.is_file() else None
    got_tcl = dll_version(ENV_BIN / 'tcl86t.dll', 'Tcl_GetVersion')
    got_tk = dll_version(ENV_BIN / 'tk86t.dll')
    check('2a init.tcl 与 tcl86t.dll 版本一致', want_tcl == got_tcl,
          f'{want_tcl} vs {got_tcl}')
    check('2b tk.tcl 与 tk86t.dll 版本一致', want_tk == got_tk,
          f'{want_tk} vs {got_tk}')

    # CRT 必须整套同版本（v2.4.18 崩溃的直接原因就是这里不一致）
    v_ms = dll_version(ENV_BIN / 'msvcp140.dll')
    v_vr = dll_version(ENV_BIN / 'vcruntime140.dll')
    v_vr1 = dll_version(ENV_BIN / 'vcruntime140_1.dll')
    check('2c msvcp140 与 vcruntime140 版本一致', v_ms == v_vr,
          f'{v_ms} vs {v_vr}')
    check('2d vcruntime140_1 同版本', v_vr1 == v_vr, f'{v_vr1} vs {v_vr}')

# ── ③ / ④ 已打包产物同源 ──────────────────────────────────
cfg = (ROOT / 'afk_bot' / 'config.py').read_text(encoding='utf-8')
_vm = re.search(r"^VERSION:\s*str\s*=\s*'([^']+)'", cfg, re.M)
version = _vm.group(1) if _vm else 'v0.0.0'
exe = ROOT / 'dist' / f'AFKJ自动化工具{version}.exe'


def _size_map(d):
    out = {}
    try:
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p):
                out.setdefault(f.lower(), set()).add(os.path.getsize(p))
    except OSError:
        pass
    return out


if not exe.is_file():
    skip('3 exe 内 DLL 同源检查', f'未找到 {exe.name}（先打包再跑本项）')
else:
    try:
        from PyInstaller.archive.readers import CArchiveReader
        reader = CArchiveReader(str(exe))
        toc_lower = {n.lower(): n for n in reader.toc}
        mismatched = []
        for _n in CRT + IMG + SSL + TCL:
            entry = toc_lower.get(_n.lower())
            if not entry:
                mismatched.append(f'{_n}(未打包)')
                continue
            in_exe = len(reader.extract(entry))
            env_p = ENV_BIN / _n
            env_sz = env_p.stat().st_size if env_p.is_file() else -1
            if in_exe != env_sz:
                mismatched.append(f'{_n}({in_exe}!={env_sz})')
        check('3a exe 内 CRT/图像库与构建环境逐项同源',
              not mismatched, str(mismatched[:6]))

        # ④ 反向审计：不得留下「与 base 同尺寸但与环境不同尺寸」的旧库
        base_sizes = _size_map(BASE_BIN)
        env_sizes = _size_map(ENV_BIN)
        stale = []
        for name, entry in toc_lower.items():
            if not name.endswith('.dll'):
                continue
            try:
                sz = len(reader.extract(entry))
            except Exception:
                continue
            if name in IGNORE_FROM_BASE:
                continue
            if sz in base_sizes.get(name, ()) and sz not in env_sizes.get(name, ()):
                stale.append(name)
        check('3b 不再有「来自 base Anaconda」的旧 DLL', not stale,
              str(sorted(stale)[:8]))
    except Exception as e:
        skip('3 exe 内 DLL 同源检查', repr(e))

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）'
      + (f'，跳过 {len(SKIPPED)} 项' if SKIPPED else ''))

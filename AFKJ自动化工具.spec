# -*- mode: python ; coding: utf-8 -*-

import os
import re
import sys

try:
    import certifi  # noqa: E402  CA 证书（HTTPS 必需，见 afk_bot/updater.py）
    _CERTIFI_CA = certifi.where()
except Exception:
    _CERTIFI_CA = None

# v2.4.14：构建期内置的上报 token（afk_bot/_report_token.py，已 gitignore）。
# 该文件只在本地存在时才打进 exe —— 它让「其他用户」无需任何配置即可上报
# Bug；不存在就跳过，行为与旧版一致（只落本地）。绝不能进版本库。
_EXTRA_HIDDEN = []
try:
    _SPEC_DIR = SPECPATH  # noqa: F821  PyInstaller 注入
except Exception:
    _SPEC_DIR = os.getcwd()
if os.path.isfile(os.path.join(_SPEC_DIR, 'afk_bot', '_report_token.py')):
    _EXTRA_HIDDEN.append('afk_bot._report_token')


# v2.4.16：exe 名的版本号改为从 afk_bot/config.py 正则读取。
# 旧实现手写 'AFKJ自动化工具v2.4.15'，每次升补丁位都要人工改两处，漏改就
# 出现「exe 名落后于 VERSION」的漂移。这里不 import afk_bot.config —— 那会
# 触发 afk_bot/__init__ 的 pyautogui 全局设置，构建期无谓副作用。
def _read_app_version() -> str:
    try:
        with open(os.path.join(_SPEC_DIR, 'afk_bot', 'config.py'),
                  encoding='utf-8') as f:
            _m = re.search(r"^VERSION:\s*str\s*=\s*'([^']+)'", f.read(), re.M)
        if _m:
            return _m.group(1)
    except Exception:
        pass
    return 'v0.0.0'


_APP_VERSION = _read_app_version()

_a = Analysis(
    ['afk_gui.py'],
    pathex=[],
    binaries=[
        # v2.3.55：显式钉死 OpenSSL DLL（真机 2026-11-07 实锤）——
        # PyInstaller 依赖扫描曾从 PATH 上其它程序抓来旧版 OpenSSL
        # （libssl 1.0MB / libcrypto 5.8MB），而 _ssl.pyd 按本环境
        # OpenSSL 3.5（libssl 1.3MB / libcrypto 7.3MB）编译 → 错配组合
        # 下 exe 内证书解析全线异常 [ASN1: NOT_ENOUGH_DATA]（0/81 张可载）。
        # 后写覆盖先写（TOC 同名去重保留后者），保证打进对的版本。
        *([
            (os.path.join(os.path.dirname(sys.executable),
                          'Library', 'bin', 'libcrypto-3-x64.dll'), '.'),
            (os.path.join(os.path.dirname(sys.executable),
                          'Library', 'bin', 'libssl-3-x64.dll'), '.'),
        ] if os.path.isfile(os.path.join(
            os.path.dirname(sys.executable),
            'Library', 'bin', 'libcrypto-3-x64.dll')) else []),

        # v2.4.17：显式钉死 Tcl/Tk DLL（真机 2026-11-24 15:33 实锤）——
        # PyInstaller 依赖扫描从 PATH 上的 base Anaconda 抓来 8.6.10
        # （tcl86t.dll 1706496 / tk86t.dll 1481216），而 hook 收集的
        # _tcl_data/init.tcl 来自 cpu_default，里面写死
        # `package require -exact Tcl 8.6.15` → 启动即 TclError 崩溃。
        # 与 OpenSSL 同款修法：后写覆盖，TOC 同名去重保留后者。
        *([
            (os.path.join(os.path.dirname(sys.executable),
                          'Library', 'bin', 'tcl86t.dll'), '.'),
            (os.path.join(os.path.dirname(sys.executable),
                          'Library', 'bin', 'tk86t.dll'), '.'),
        ] if os.path.isfile(os.path.join(
            os.path.dirname(sys.executable),
            'Library', 'bin', 'tcl86t.dll')) else []),

        # v2.4.19：显式钉死 VC++ 运行库 + Pillow 的图像依赖（真机
        # 2026-11-24 20:47 实锤）—— PyInstaller 依赖扫描从 PATH 上的
        # base Anaconda 抓来旧版 msvcp140.dll（**14.27**），而其余 CRT
        # 是 cpu_default 的 14.44 → onnxruntime 初始化即访问冲突
        # 0xc0000005（Windows 事件日志崩溃模块 = MSVCP140.dll），
        # GUI 停在「初始化 OCR 引擎...」后无声退出。
        # 同一审计还发现 Pillow 的 9 个依赖（freetype/libjpeg/libpng/
        # tiff/zstd/...) 也全部来自 base，与 PIL 的 .pyd 不同源。
        # 一律改为取当前解释器所在环境的版本（与 v2.3.55 OpenSSL、
        # v2.4.17 Tcl 同款修法：后写覆盖，TOC 同名去重保留后者）。
        *([
            (os.path.join(os.path.dirname(sys.executable),
                          'Library', 'bin', _n), '.')
            for _n in (
                'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
                'vcruntime140.dll', 'vcruntime140_1.dll',
                'freetype.dll', 'lcms2.dll', 'libbz2.dll', 'libjpeg.dll',
                'liblzma.dll', 'libpng16.dll', 'openjp2.dll',
                'tiff.dll', 'zstd.dll',
            )
            if os.path.isfile(os.path.join(
                os.path.dirname(sys.executable), 'Library', 'bin', _n))
        ]),
    ],
    datas=[
        ('model/ocr', 'model/ocr'),   # MaaOCR 模型文件（det.onnx + rec.onnx + keys.txt）
        ('maa_ocr.py', '.'),           # MaaOCR 引擎
        ('templates', 'templates'),  # 模板匹配图片（btn_*.png 等）

        ('task_registry.py', '.'),     # 任务注册表
        ('tasks', 'tasks'),            # 任务模块目录
        ('afk_bot', 'afk_bot'),        # afk_bot 包（infra/services/auto_farm 三层）
        # v2.3.14：显式打包 CA 证书 —— 否则 exe 里 HTTPS 报
        # [ASN1: NOT_ENOUGH_DATA]（真机 2026-10-27 实证）
        *([(_CERTIFI_CA, 'certifi')] if _CERTIFI_CA else []),
    ],
    hiddenimports=[
        'customtkinter',
        'onnxruntime',
        'pyclipper',
        'shapely',
        'cv2',
        'numpy',
        *_EXTRA_HIDDEN,          # v2.4.14 构建期内置上报 token（可选）

        # afk_bot ① 默认开启层（infra）
        'afk_bot',
        'afk_bot.config',
        'afk_bot.infra.coordinate',
        'afk_bot.infra.screen',
        'afk_bot.infra.input',
        'afk_bot.infra.ocr_memory',
        'afk_bot.infra.ocr_engine',
        'afk_bot.infra.stats',
        # afk_bot ② 全局可用层（services）
        'afk_bot.services.detector',
        'afk_bot.services.navigator',
        # afk_bot ③ 仅自动推图层（auto_farm）
        'afk_bot.auto_farm.loop',
        'afk_bot.auto_farm.battle',
        'afk_bot.auto_farm.formation',
        # 门面
        'afk_bot.facade',

        'task_registry',
        'tasks',
        'tasks.base',
        'tasks.scheduler',
        'tasks.catalog',
        'tasks.auto_farm',
        'tasks.labyrinth',
        'tasks.goddess_trial',
        'tasks.home_task',
        'tasks.tianqiong_trial',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'paddle', 'paddlepaddle', 'paddleocr',
        'paddlenlp', 'paddle2onnx', 'onnxslim',
        'scipy', 'matplotlib', 'pandas', 'scikit-learn',
        'IPython', 'jupyter', 'notebook', 'spyder', 'pytest',
        'ultralytics', 'easyocr', 'cnstd',
        'rapidocr', 'rapidocr_onnxruntime',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(_a.pure)

exe = EXE(
    pyz,
    _a.scripts,
    _a.binaries,
    _a.datas,
    [],
    name=f'AFKJ自动化工具{_APP_VERSION}',
    icon='afk_journey.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 窗口模式，不显示命令行
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,         # 请求管理员权限
)

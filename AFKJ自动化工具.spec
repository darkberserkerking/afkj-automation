# -*- mode: python ; coding: utf-8 -*-

import os

try:
    import certifi  # noqa: E402  CA 证书（HTTPS 必需，见 afk_bot/updater.py）
    _CERTIFI_CA = certifi.where()
except Exception:
    _CERTIFI_CA = None

_a = Analysis(
    ['afk_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('model/ocr', 'model/ocr'),   # MaaOCR 模型文件（det.onnx + rec.onnx + keys.txt）
        ('maa_ocr.py', '.'),           # MaaOCR 引擎
        ('templates', 'templates'),  # 模板匹配图片（btn_*.png 等）

        ('task_registry.py', '.'),     # 任务注册表
        ('tasks', 'tasks'),            # 任务模块目录
        ('afk_bot', 'afk_bot'),        # afk_bot 包（infra/services/auto_farm 三层）
        # v2.2.24：显式打包 CA 证书 —— 否则 exe 里 HTTPS 报
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
    name='AFKJ自动化工具v2.2.24',
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

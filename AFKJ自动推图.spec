# -*- mode: python ; coding: utf-8 -*-

import os

_a = Analysis(
    ['afk_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('model/ocr', 'model/ocr'),   # MaaOCR 模型文件（det.onnx + rec.onnx + keys.txt）
        ('maa_ocr.py', '.'),           # MaaOCR 引擎
        ('_templates', '_templates'),  # 模板匹配图片（btn_*.png 等）
    ],
    hiddenimports=[
        'customtkinter',
        'onnxruntime',
        'pyclipper',
        'shapely',
        'cv2',
        'numpy',
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
    name='AFKJ自动推图v0.8',
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
    icon=None,              # TODO: 添加图标
)

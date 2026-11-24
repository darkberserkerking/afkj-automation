# -*- coding: utf-8 -*-
"""运行期数据目录（日志 / 配置 / 诊断 / 缓存）统一入口。

为什么不再写 exe 同目录
--------------------
微软对桌面应用的明确要求：**写安装目录不受支持**。Program Files 受 UAC
保护，普通用户无写权限；exe 放桌面时则会把日志和配置撒在用户桌面上。
可变数据必须落用户可写的应用数据区（%LOCALAPPDATA%）。

因此（v2.4.12）：
  · 打包运行 → 数据写 %LOCALAPPDATA%\\AFKJ自动化工具\\
  · 源码运行 → 数据写仓库根（保持开发便利；这些文件已在 .gitignore）
  · 环境变量 AFKJ_DATA_DIR 强制覆盖（单元测试隔离用，优先于一切）

只读资产（templates/model/ocr 等）仍走 exe 目录 / _MEIPASS，与本模块无关。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Iterable, List

APP_NAME: str = 'AFKJ自动化工具'
ENV_DATA_DIR: str = 'AFKJ_DATA_DIR'

# 旧版写在 exe 同目录的运行期文件：首次启动自动搬进数据目录（v2.4.12）
LEGACY_RUNTIME_FILES: tuple = (
    'gui_config.json',
    'CRASH_REPORT.log',
    'stderr.log',
    '_afk_gui_log.txt',
    'tasks_enabled.json',
    'afkj_config.json',
    'game_paths.json',
    'afkj_net_diag.log',
    'ocr_memory.json',
)

_APP_DIR: str = ''
_DATA_DIR: str = ''


def is_frozen() -> bool:
    """是否为 PyInstaller 打包运行。"""
    return bool(getattr(sys, 'frozen', False))


def app_dir() -> str:
    """只读资产目录（打包 = exe 所在目录；源码 = 本文件所在仓库根）。"""
    global _APP_DIR
    if not _APP_DIR:
        if is_frozen():
            _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
        else:
            _APP_DIR = os.path.dirname(os.path.abspath(__file__))
    return _APP_DIR


def _ensure_dir(path: str) -> str:
    """确保目录存在；失败退到 %%TEMP%% 下的同名目录，再失败原样返回。"""
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        pass
    fallback = os.path.join(tempfile.gettempdir(), APP_NAME)
    try:
        os.makedirs(fallback, exist_ok=True)
        return fallback
    except Exception:
        return path


def data_dir() -> str:
    """可写数据目录（保证已创建）。

    环境变量 AFKJ_DATA_DIR 每次调用都重新解析（不缓存），
    便于测试在 import 之后仍能切换目录。
    """
    global _DATA_DIR
    env = os.environ.get(ENV_DATA_DIR, '').strip()
    if env:
        return _ensure_dir(os.path.abspath(env))
    if _DATA_DIR:
        return _DATA_DIR
    if is_frozen():
        base = os.environ.get('LOCALAPPDATA', '').strip()
        if not base:
            base = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        _DATA_DIR = _ensure_dir(os.path.join(base, APP_NAME))
    else:
        _DATA_DIR = _ensure_dir(app_dir())
    return _DATA_DIR


def data_path(name: str) -> str:
    """数据目录下的文件完整路径。"""
    return os.path.join(data_dir(), name)


def migrate_legacy(names: Iterable[str] = None) -> List[str]:
    """把旧版落在 exe 同目录的运行期文件搬进数据目录。

    仅在打包运行时执行（源码模式的仓库根本身就是数据目录）。
    目标已存在 → 保留目标、不动源文件（可能是用户新配置，绝不覆盖）；
    源文件被占用（旧实例还在跑）→ 跳过，不抛异常。

    Returns:
        实际迁移成功的文件名列表。
    """
    if not is_frozen():
        return []
    src_dir, dst_dir = app_dir(), data_dir()
    if os.path.normcase(os.path.abspath(src_dir)) == \
            os.path.normcase(os.path.abspath(dst_dir)):
        return []
    moved: List[str] = []
    for name in (LEGACY_RUNTIME_FILES if names is None else names):
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_dir, name)
        try:
            if not os.path.isfile(src) or os.path.exists(dst):
                continue
            shutil.move(src, dst)
            moved.append(name)
        except Exception:
            continue
    return moved

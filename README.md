<div align="center">

# AFKJ 自动化工具

_✨ 剑与远征：启程 的 Windows 桌面前台自动化助手 ✨_

纯**截图识别 + 按键注入**驱动 · 零内存读取 · 零进程注入

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6?logo=windows)](https://github.com/darkberserkerking/afkj-automation/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/darkberserkerking/afkj-automation?display_name=tag)](https://github.com/darkberserkerking/afkj-automation/releases)
[![Last Commit](https://img.shields.io/github/last-commit/darkberserkerking/afkj-automation)](https://github.com/darkberserkerking/afkj-automation/commits/main)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

[下载最新版](https://github.com/darkberserkerking/afkj-automation/releases) · [使用说明](docs/客户使用说明.md) · [开发手册](docs/开发指导手册.md)

</div>

## 📖 使用须知

> [!IMPORTANT]
> **本工具仅用于个人学习与自动化实践。** 使用前请自行确认符合游戏用户协议；
> 因使用本工具产生的一切后果（包括但不限于账号处罚）由使用者自行承担。
>
> 本项目**不修改游戏内存、不注入进程、不篡改客户端**，只做两件事：
> ① 截屏并用 OCR/模板匹配识别界面；② 模拟键鼠点击。行为与"人手动操作"等价。

- 目前**只适配** 1280×1024 窗口化（其它分辨率需重新校准坐标与模板）
- 需要 **Windows 10/11**；以**管理员身份**运行才能把按键送进游戏窗口
- 遇到问题欢迎提 Issue，附上 `_afk_gui_log.txt` 里对应的日志段 ✅

## 🎮 功能一览

| 任务 | 说明 |
|---|---|
| 🗺️ **自动推图** | 挂机关卡页逐次战斗（幻灵挑战 / 普通挑战），支持次数与时限 |
| 🌀 **异界迷宫** | 逐层选门、战斗、领奖励；支持三种运行模式与失败重开 |
| 🌟 **女神试炼** | 挑战到当前上限层，再扫荡拉满奖励 |
| 🏯 **结社战线** | 玩法目录 → 公会玩法 → 结社战线：扫荡 + 无限循环挑战（失败直接重试） |
| ⚔️ **竞技场** | 按黑 / 白名单挑对手，循环挑战至票数用尽 |
| 🏠 **一键家园** | 愿望单自动刷单（可选是否消耗体力） |
| 💠 **迷梦之域** | 进入迷梦之域挑战，可重复至次数耗尽 |
| 📋 **每日任务** | 一键领取每日任务与活跃度奖励 |
| 🎁 **收赠友情点** | 一键赠礼并收礼，收满即完成 |
| 🚀 / 🛑 **启动 / 关闭游戏** | 游戏没开自动拉起；结束时优雅关闭 |

> 所有任务都可在界面里自由增删、排序、多开实例，并按清单顺序依次执行。

## 🚀 快速开始

### 方式一：下载 exe（推荐给只想用的）

1. 到 [Releases](https://github.com/darkberserkerking/afkj-automation/releases) 下载最新的 `*.exe`
2. 双击运行（会请求管理员权限）
3. 勾选任务 → 点「开始」

### 方式二：源码运行（推荐给想改的）

```bash
git clone https://github.com/darkberserkerking/afkj-automation.git
cd afkj-automation
pip install customtkinter opencv-python onnxruntime Pillow pywin32 keyboard
python afk_gui.py
```

或者直接双击 **`tools/launch_source.bat`**（用 `pythonw` 免控制台启动）

### 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（需管理员权限） |
| 游戏 | 窗口化 **1280×1024**，不要最小化，保持前台 |
| Python | 3.11+（开发环境：conda `afk_gpu`） |
| 依赖 | `customtkinter` `opencv-python` `onnxruntime` `Pillow` `pywin32` `keyboard` |

## 🔄 自动更新

程序启动 3 秒后静默检查更新，也可在 **设置抽屉 → 关于** 里手动点「🔄 检查更新」。

| 运行方式 | 检查方式 | 更新动作 | 需要 Token？ |
|---|---|---|---|
| **exe** | 查 GitHub Release 的 `tag_name` | 下载附件 → 原地替换 → 重启 | 公开仓库**不需要** |
| **源码** | 比对本地 HEAD 与 `origin/main` | `git pull --ff-only` | 不需要（用 git 凭据） |

> 源码模式更新前会校验工作区是否干净，**有未提交改动则中止**，不会覆盖你的修改。

## 🧠 设计要点

- **判据全部走 OCR 文本 + 模板匹配**，坐标只作兜底并标注实测出处
- **任务自判状态**：每个任务自己识别所处界面，不改动全局检测器
- **可观测**：找不到关键按钮时打印候选（文本/坐标/置信度），便于按日志定位
- **停止即时生效**：任何阻塞点都检查停止信号（F12 全局停止）
- **失败自愈**：战斗失败按策略重试，连续异常有上限，不会无限盲点

## 📁 目录结构

```
afk_gui.py           图形界面 + 任务调度入口
afk_bot/             引擎层：截图 / OCR / 输入 / 导航 / 检测
  ├─ infra/          底层（截图、输入注入、模板匹配、OCR）
  ├─ services/       检测器 + 导航器
  ├─ auto_farm/      推图主循环、阵容、状态机
  └─ updater.py      自动更新（Release / git）
tasks/               各玩法任务（继承 BaseTask）
tests/               干跑 / 专项测试（python tests/run_tests.py --level 3）
templates/           模板匹配素材
model/ocr/           PP-OCRv5 检测/识别模型（已内置）
docs/                使用说明、开发手册、界面命名规范
```

## 🧪 测试

```bash
python tests/run_tests.py --level 1            # 冒烟
python tests/run_tests.py --level 3 --jobs 4   # 全量（约 2.5 分钟）
```

## ❓ 常见问题

| 现象 | 处理 |
|---|---|
| 点「开始」没反应 | 确认游戏窗口是 **1280×1024 窗口化**且未被最小化 |
| 按键无效 / 点击没落在正确位置 | 以**管理员身份**运行；确认游戏在前台 |
| 识别不出某个界面 | 把 `_afk_gui_log.txt` 里带 `候选=` 的那行发到 Issue |
| 想换分辨率 | 改 `templates/` 与 `afkj_config.json` 里的坐标（见 `docs/坐标规范.md`） |

## 🙏 致谢

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) / [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights) —— 架构与判据思路的启蒙
- [MaaEnd](https://github.com/MaaEnd/MaaEnd) —— 界面风格参考
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) —— OCR 模型来源

## 📄 许可证

本项目采用 **[GNU AGPL-3.0](LICENSE)** 许可证。

- ✅ 可以自由使用、修改、分发（包括商用）
- ⚠️ **修改后必须同样以 AGPL-3.0 开源**（包括通过网络提供服务的情形）
- ⚠️ 必须保留版权声明与许可证文本

```
Copyright (C) 2026  darkberserkerking

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.
```

## ⚠️ 声明

本项目为**非官方**个人作品，与游戏开发商 / 发行商无任何关联。
仓库内 `templates/` 为游戏界面局部截图，版权归原权利方所有，仅用于界面识别。

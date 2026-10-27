# AFKJ 自动化工具

AFK Journey（剑与远征：启程）Windows 端自动化助手：基于 **截图 + OCR + 模板匹配** 驱动，
覆盖日常任务、迷宫、家园、竞技场、女神殿、迷梦之域等玩法，并提供图形界面与任务调度。

> 仅供个人学习与自动化实践使用。使用前请自行确认符合游戏用户协议；因使用产生的后果由使用者自负。

## 环境要求

- Windows 10/11（需以管理员权限运行，才能向游戏窗口注入按键）
- Python 3.11+（开发环境：conda env `afk_gpu`）
- 游戏以**窗口化 1280×1024** 运行（其它分辨率需重新校准坐标/模板）
- 主要依赖：`customtkinter`、`opencv-python`、`onnxruntime`、`Pillow`、`pywin32`、`keyboard`

```bash
pip install customtkinter opencv-python onnxruntime Pillow pywin32 keyboard
```

## 快速开始

```bash
python afk_gui.py
```

1. 在界面里配置**启动器路径**（Farlight Launcher 的 `Launcher.exe`），或设置环境变量
   `AFKJ_LAUNCHER_PATH`，或写入 `game_paths.json`
2. 勾选要执行的任务并调整顺序
3. 点「开始」

命令行方式（调试用）：

```bash
python -m afk_bot
```

## OCR 模型

`model/` 与 `*.onnx` **未纳入仓库**（约 41MB）。请自行放置 PP-OCRv5 检测/识别模型到：

```
model/ocr/
```

缺失模型时 OCR 相关功能不可用（界面仍可启动）。

## 目录结构

| 路径 | 说明 |
|---|---|
| `afk_gui.py` | 图形界面（customtkinter）+ 任务调度入口 |
| `afk_bot/` | 引擎层：截图/OCR/输入/导航/检测（`infra` → `services` → `auto_farm`） |
| `tasks/` | 各玩法任务（日常任务、迷宫、家园、竞技场、女神殿…） |
| `task_registry.py` | 任务注册表（界面与调度共用） |
| `tests/` | 干跑/专项测试（`python tests/run_tests.py --level 3`） |
| `templates/` | 模板匹配素材（按钮/图标裁剪） |
| `docs/` | 开发指导手册、界面命名规范、坐标规范等 |
| `backup/` | 本地历史备份（**不纳入仓库**） |

## 自动更新

程序启动 3 秒后会自动检查 GitHub 最新版本，也可在 **设置抽屉 → 关于** 里手动点「🔄 检查更新」。

- **打包 exe**：发现新版本 → 点「⬇️ 立即更新」→ 下载后自动替换并重启
  （替换由临时 `.bat` 完成：等进程退出 → `move /y` 覆盖 → 重启 → 自删）
- **源码运行**：提示用 `git pull` 更新（自动替换仅对 exe 生效）
- **私密仓库需要 Token**（GitHub API 限制），二选一：
  1. 环境变量 `AFKJ_GH_TOKEN=<token>`
  2. 程序目录放 `github_token.txt`，内容为一行 Token

命令行检查：

```bash
python -m afk_bot.updater --check    # 只看有没有新版
python -m afk_bot.updater --update   # 下载并安装（仅 exe 模式）
```

> 发布新版本：网页 → **Releases** → Draft a new release → Tag 填 `vX.Y.Z` →
> 上传 `dist\AFKJ自动化工具vX.Y.Z.exe` → Publish。程序按 `tag_name` 比较版本。

## 测试

```bash
python tests/run_tests.py --level 1          # 冒烟
python tests/run_tests.py --level 3 --jobs 4 # 全量（约 100~150 秒）
```

## 设计要点

- **不依赖固定坐标**：优先用 OCR 文本 + 模板匹配定位，坐标仅作兜底并附实测出处
- **任务自判状态**：每个任务自行 OCR 判定所处界面，不污染全局检测器
- **可观测**：找不到元素时打印候选（文本/坐标/置信度），便于按日志定位
- **停止即时生效**：任何阻塞点都检查停止信号

## 许可

代码仅供学习交流；`templates/` 内为游戏界面局部截图，版权归原权利方所有。

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

程序启动 3 秒后静默检查；也可在 **设置抽屉 → 关于** 手动点「🔄 检查更新」。

**两种模式自动切换**（按是否打包运行）：

| 运行方式 | 检查 | 更新 | 是否需要 Release/Token |
|---|---|---|---|
| **源码**（`python afk_gui.py`） | 比较本地 HEAD 与 `origin/main` | **`git pull --ff-only`** | **都不需要** ✓ |
| **打包 exe** | 查最新 Release 的 `tag_name` | 下载附件 exe 并原地替换 | 需要 Release；私密仓库另需 Token |

> 源码模式更新前会校验工作区是否干净，**有未提交改动则中止**（绝不覆盖你的修改）。

命令行（不打开界面，便于排查）：

```bash
python -m afk_bot.updater --check     # 源码模式：比对 git；exe 模式：查 Release
python -m afk_bot.updater --update    # 源码模式：git pull；exe 模式：下载并安装
```

打包版同样支持：

```bat
AFKJ自动化工具vX.Y.Z.exe --check-update   :: 打印结果后退出（可用来验证网络/证书）
```

### 发布新版本（维护者）

1. 打包：`pyinstaller AFKJ自动化工具.spec`，产物在 `dist/`
2. 网页 → **Releases** → **Draft a new release**
3. **Tag** 填 `vX.Y.Z`（必须与 `afk_bot/config.py` 的 `VERSION` 一致）
4. 上传 `dist\AFKJ自动化工具vX.Y.Z.exe` 作为附件 → **Publish**

程序按 Release 的 `tag_name` 与本地 `VERSION` 比较（语义版本，支持 `v2.2.20` 这类格式）。
> Release 附件上限 **2GB**，所以 130MB+ 的 exe 放这里没问题（仓库本身有 100MB 单文件限制）。

### 私密仓库需要 Token

GitHub API 对私密仓库要求鉴权。**公开仓库可跳过本节**。

**① 生成 Token**

1. 右上角头像 → **Settings** → 左侧最下方 **Developer settings**
2. **Personal access tokens** → **Fine-grained tokens** → **Generate new token**
3. 填写：Token name（随便，如 `afkj-updater`）、Expiration（按需，可 1 年）
4. **Repository access** → **Only select repositories** → 勾选本仓库
5. **Permissions** → **Repository permissions** → **Contents** 设为 **Read-only**
   （`Metadata` 会自动带上，无需改）
6. **Generate token** → 复制 `github_pat_...`（**只显示一次**）

**② 配置（二选一）**

```bat
:: 方式 A：环境变量（永久写入当前用户）
setx AFKJ_GH_TOKEN "github_pat_xxxxxxxx"
```

> ⚠️ `setx` 只对**之后新启动的进程**生效 —— 设置完请**关掉并重新打开工具**
> （验证：新开一个 cmd 执行 `echo %AFKJ_GH_TOKEN%`，能打印出来就对了）

**方式 B：文件（更简单，不用改环境）**

在本程序所在目录（如 `D://桌面`）新建 `github_token.txt`，内容就是一行 Token：

```
github_pat_xxxxxxxx
```

> `github_token.txt` 已在 `.gitignore` 中，**不会被提交** ✓

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

# AFKJ自动化工具 — 功能分层与重构方案 v1.5.6

> 2026-09-01 定稿。本文档是「自动推图」功能三分盘点的结果 + 物理重构的施工图。
> 目标：目录结构直接反映架构分层，为后续新任务（日常/爬塔等重写）提供清晰的路标。

---

## 一、三层功能定义

| 层 | 定义 | 目录 |
|---|---|---|
| **① 默认开启** | 任一任务启动前必须就绪的公共地基：OCR 引擎、截图、输入、坐标、窗口管理、配置、统计 | `afk_bot/infra/` |
| **② 全局可用** | 与具体任务无关的共享能力：导航、阶段检测、文字/模板识别定位、卡死恢复、弹窗处理 | `afk_bot/services/` |
| **③ 仅自动推图** | 推图任务专属：主循环编排、阵容、战斗监测、模式切换 | `afk_bot/auto_farm/` |

## 二、功能清单（精确到模块/方法级）

### ① 默认开启（infra/）

| 模块 | 职责 |
|---|---|
| `ocr_engine.py` TextRecognizer | MaaOCR（PP-OCRv5）封装，`find_text` 系列的引擎 |
| `screen.py` ScreenCapture | ImageGrab 截图、窗口区域裁剪（_SNAP_TRIM） |
| `input.py` InputController | 前台鼠标点击 / keyboard 按键（中文粘贴 ctrl+v 特例） |
| `coordinate.py` CoordSystem | 比例坐标 → 屏幕绝对坐标换算（含双倍率点击） |
| `stats.py` StatsTracker | 战斗/轮次统计 → GUI 回调 |
| `ocr_memory.py` OCRMemory | OCR 结果记忆缓存 + 策略 |
| `config.py` | 版本号、_BTN_POS 按钮坐标常量、超时配置 |
| core 生命周期方法 | `find_and_focus`（找窗+置焦）、`stop/pause/resume`、`_check_pause`、`_res_path`、`_load_config/_save_config` |

**core 中的薄委托方法（物理在 loop.py，逻辑归属本层）**：`_win_dims / _snap / _ocr_screen / _ocr_img / click_at / click_ratio / click_ratio_dual / _click_abs / _send_esc / _screen_offset / _frame_fingerprint`

**说明**：管理员权限检测 `_check_admin()` 已存在（afk_gui.py:1504 定义，App.__init__ 启动时检测并告警），原判断有误，实际无缺口。

### ② 全局可用（services/）

| 模块 | 职责 |
|---|---|
| `navigator.py` Navigator | 回主界面（navigate_to_idle_stage）、ESC 逐级退出（esc_to_main_screen）、等待待机、**通用弹窗处理**（X 关闭 btn_close 模板 / 确认弹窗 / 退出弹窗） |
| `detector.py` PhaseDetector | detect_phase 阶段检测（战斗中优先）、is_on_main_screen |
| core 恢复方法（逻辑归属本层） | `_recover_to_idle_stage`、`_try_recover_from_stuck`、`_close_result_and_go_main`、`_handle_pause_menu`、`_check_network_disconnect`、`switch_resolution` |
| core 识别委托（逻辑归属本层） | `find_text / find_text_exact / find_text_exclude / template_match / click_template` |

### ③ 仅自动推图（auto_farm/）

| 模块 | 职责 |
|---|---|
| `loop.py`（原 core.py）AFKBotCore | `run / _run_main_loop / run_auto_farm` 主循环编排；交替模式；连败切模式；`_formation_exhausted` 阵容耗尽兜底；任务指标检查（`_task_completion_*`）；`_handle_common_phase`、`_detect_season_stages`、`_record_pre_battle_stage` |
| `formation.py` FormationManager | 阵容切换（`>` region+模板匹配）、自动挑战/战斗按钮、挑战点击 |
| `battle.py` BattleMonitor | 战斗中检测、战斗结束等待（max_wait 60s 兜底） |
| facade 推图委托 | `_switch_formation / _navigate_to_other_challenge / _ocr_click_challenge / _click_challenge_btn / _prepare_and_start_battle / step_close_result / step_click_challenge_btn / _result_fallback_click / _handle_unowned_hero_dialog / _wait_for_manual_battle_end` |

**注**：`facade.py` 是跨层门面（API 保持 100% 向后兼容，GUI / scheduler 无感知），不拆分。`tasks/auto_farm.py` 是推图任务的调度包装，保留在 tasks/。

## 三、目标目录结构

```
afk_bot/
  __init__.py          # 导出 AFKBot + 各子模块（路径更新）
  config.py            # 基础设施：版本/常量
  facade.py            # 跨层门面 AFKBot（API 不变）
  infra/               # ① 默认开启
    ocr_engine.py  screen.py  input.py  coordinate.py
    stats.py  ocr_memory.py
  services/            # ② 全局可用
    navigator.py  detector.py
  auto_farm/           # ③ 仅自动推图
    loop.py(原core.py)  formation.py  battle.py
```

### 迁移映射表

| 原路径 | 新路径 |
|---|---|
| `afk_bot/ocr_engine.py` | `afk_bot/infra/ocr_engine.py` |
| `afk_bot/screen.py` | `afk_bot/infra/screen.py` |
| `afk_bot/input.py` | `afk_bot/infra/input.py` |
| `afk_bot/coordinate.py` | `afk_bot/infra/coordinate.py` |
| `afk_bot/stats.py` | `afk_bot/infra/stats.py` |
| `afk_bot/ocr_memory.py` | `afk_bot/infra/ocr_memory.py` |
| `afk_bot/navigator.py` | `afk_bot/services/navigator.py` |
| `afk_bot/detector.py` | `afk_bot/services/detector.py` |
| `afk_bot/core.py` | `afk_bot/auto_farm/loop.py` |
| `afk_bot/formation.py` | `afk_bot/auto_farm/formation.py` |
| `afk_bot/battle.py` | `afk_bot/auto_farm/battle.py` |

### 需更新的 import（全景已核实）

- `auto_farm/loop.py`：10 处子模块 import
- `facade.py`：core → `auto_farm.loop`，ocr_memory → `infra.ocr_memory`
- `infra/input.py`：coordinate/screen 2 处；`infra/screen.py`：coordinate 1 处
- `afk_bot/__init__.py`：全量重写
- `afk_gui.py:75`：`from afk_bot.ocr_memory import OCRMemory` → infra
- spec hiddenimports：afk_bot.* 13 项 → 新路径
- tasks/、tests/ 无 afk_bot 直接 import（已核实，无感知）

## 四、废弃清单（用户确认：后续重写）

| 项 | 处置 |
|---|---|
| `tasks/daily_quest.py`（303行） | 备份到 `backup/deprecated_tasks/` 后删除 |
| `tasks/goddess_challenge.py`（603行） | 同上 |
| `tasks/catalog.py` 注册项 | 删除 DailyQuestTask / GoddessChallengeTask（TowerTask 保留占位） |
| `afk_bot/benchmark_models.py`（281行） | 0 引用（已核实），备份后删除 |
| `afk_bot/mllm_data/`（空目录） | 删除 |
| 旧实例数据 | tasks_enabled.json 中残留实例由 load_config 的 registered 过滤自动清理 |

## 五、品牌重命名（用户确认：文件名一起改）

| 位置 | 现状 → 新值 |
|---|---|
| exe / spec name | `AFKJ自动推图v1.5.5` → `AFKJ自动化工具v1.5.6` |
| 窗口标题（afk_gui.py:122） | `AFKJ自动化辅助` → `AFKJ自动化工具` |
| VERSION（config.py） | v1.5.5 → v1.5.6 |
| spec 文件名 | `AFKJ自动推图.spec` → `AFKJ自动化工具.spec` |
| 任务显示名「自动推图」 | **保留不变**（这是任务名，不是品牌名） |

## 六、验证标准

1. py_compile 全链 0 错误
2. 任务层冒烟 16/16
3. GUI 抽屉冒烟 30/30（含几何防回归）
4. GUI 视觉验收（截图看图确认）
5. PyInstaller 打包成功 → `D:\桌面\AFKJ自动化工具v1.5.6.exe`

# v1.5.6 交付报告：品牌重命名 + 三层架构重构

## 完成内容

### 1. 项目重命名
- 窗口标题 / 打包名 / spec 均改为「AFKJ自动化工具」
- 旧 spec 删除，新建 `AFKJ自动化工具.spec`（hiddenimports 三层路径）

### 2. 三层架构（按功能划分）
| 层 | 目录 | 内容 | 定位 |
|---|---|---|---|
| ① 默认开启 | `afk_bot/infra/` | ocr_engine / screen / input / coordinate / stats / ocr_memory | 任一任务开启必须启动 |
| ② 全局可用 | `afk_bot/services/` | navigator / detector | 未来其他任务可复用 |
| ③ 仅自动推图 | `afk_bot/auto_farm/` | loop.py（原core.py）/ formation.py / battle.py | 自动推图专属 |

`facade.py` 跨层门面，对外 API 不变。

### 3. 废弃清理
- `tasks/daily_quest.py`、`tasks/goddess_challenge.py` 删除（备份于 `backup/deprecated_tasks/`），后续重写
- catalog 仅保留 auto_farm + tower

## 验证结果
- py_compile 全链通过（GUI + 全部包 + tests）
- 修复重构遗漏：`afk_bot_android/adb_bot.py` 9 处旧顶层 import → 三层路径（已备份）
- 调度器冒烟 16/16（更新 catalog 断言：daily_quest/goddess_challenge 已废弃）
- GUI 抽屉冒烟 30/30
- PyInstaller 打包成功

## 交付物
- `D:\桌面\AFKJ自动化工具v1.5.6.exe`（134MB）
- 设计文档：`AFKJ自动化工具_功能分层与重构方案.md`

## 备注
- 桌面旧版本 v1.5.2–v1.5.5 exe 仍在，待确认后可删（不擅自动个人目录）

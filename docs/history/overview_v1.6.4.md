# v1.6.4 交付报告：底部按钮 toggle + 图标同步

## 修正旧 bug
- v1.6.3 之前的「⇅ 全部展开」按钮**实际跑的是「全选」逻辑**（标签与功能错位）
- v1.6.4 真正实现"展开所有抽屉"功能（之前根本不存在）

## 修改
1. **`_btn_select_none` toggle**：`☑ 取消全选` ↔ `☐ 全选`（按当前勾选状态切换）
2. **`_btn_expand_all` toggle**：`⇅ 全部展开` ↔ `⇊ 全部折叠`（按当前抽屉状态切换）
3. 新增辅助方法：
   - `_any_drawer_open()`：检测是否有任务行抽屉展开
   - `_open_all_task_drawers()`：同时打开所有行内抽屉
   - `_refresh_batch_btns()`：根据状态统一刷新两个按钮文字
4. 刷新挂载点：`_render_task_rows` / `_toggle_drawer`(展开/收起两条) / `_close_all_drawers` / `_on_task_enabled_change` / 两个 toggle 自身

## 验证
- GUI 冒烟 47/47（新增 6.5 节 7 项断言）
- 视觉截图：底部按钮显示正常（截图中 v1.6.3 是因截图时还没升版本）

## 交付
- `D:\桌面\AFKJ自动化工具v1.6.4.exe`（134MB）
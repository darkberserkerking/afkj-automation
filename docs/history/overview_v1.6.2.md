# v1.6.2 交付报告：行卡延伸至分隔条

## 根因（用户反馈 "1.6.0 和 1.6.1 对比无变化"）
- `winfo_*` 测量发现 `_task_scroll w=550`，比 `_left_col w=560` 窄 10px
- CTkScrollableFrame 的内边距 + 滚动条预留把行卡死在 550，无论按钮 padx 怎么调都贴不到右边缘

## 修改
1. `_left_col` 加 `border_width=0, corner_radius=0`
2. `_task_scroll` 加 `border_width=0, corner_radius=0`
3. 行从 550 → 556（剩 4px 给滚动条，已是不可消除的结构预留）

## 验证
- GUI 冒烟 36/36
- 视觉截图：行卡明显延伸更接近分隔条
- 标题栏 v1.6.2

## 教训（写入 MEMORY）
- UI 微调要 `winfo_*` 测量验证，光看截图猜位置会漏结构性问题
- 改 padx 几像素 ≠ 解决根本问题；要改就改到结构根上

## 交付
- `D:\桌面\AFKJ自动化工具v1.6.2.exe`（134MB）
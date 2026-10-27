# v1.6.3 交付报告：✕ 真正最右（pill 修）

## 根因（你直觉对——空白不止 4px）
测量结果（v1.6.2）：
```
[4] pill(空)  x=520 right=556  ← 宽 36px 的「透明白」
[5] ✕         x=489 right=514
[6] ›         x=461 right=485
```
状态 pill 文本为空但 `padx=10` + 默认宽度占了 **36px**；它是最早 `pack(side='right')` 的，按 Tk 规则**先 pack 者最右**，所以 ✕/› 一直被挤在 pill 左边。改 btn padx 完全没用——按钮根本不在最右！

## 修复
调整 pack 顺序：btn_del 先 pack（最右）→ btn_drawer → **pill 最后 pack**（移到 › 左边）。

## 验证
- GUI 冒烟 40/40（新增「✕ 严格最右」断言，用 pack_slaves 顺序验证——headless 测试 winfo_x=0 不可用）
- 视觉截图：✕ 紧贴任务行右边缘，pill 已移到 › 左侧

## 教训（写入 MEMORY）
**Tk pack(side='right') 是 LIFO 堆叠**——先 pack 者最右；改 padding/位置前必须列出所有右侧 widget 的实际 x 坐标。

## 交付
- `D:\桌面\AFKJ自动化工具v1.6.3.exe`（134MB）
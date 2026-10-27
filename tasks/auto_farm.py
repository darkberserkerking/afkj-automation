"""
任务：自动推图

交替模式下循环幻灵挑战 / 挑战推图，自动恢复与连败切换。

实现说明（重要）：
    推图逻辑本体位于 afk_bot/core.py 的 _run_main_loop()（约 514 行）。
    本任务**不复制任何推图逻辑**，只做三件事：
      1. 前置检查 —— 确认游戏窗口存在并可聚焦
      2. 调用 core.run_auto_farm()
      3. 结果翻译 —— 规范成 'done' / 'failed' / 'stopped'

    之所以不把主循环搬进来，是因为 _run_main_loop 内沉淀了大量边界处理
    （卡死恢复、掉线重连、结算误判、首次循环标志、阵容耗尽判断等），
    重写等于把历史踩过的坑重踩一遍。

    ⚠️ v1.5.0 起运行模式与战斗模式已固定（交替模式 + 逐次战斗），
    本任务**无可调参数**，options_schema 留空 → GUI 不渲染参数表单。

    ⚠️ 旧版本依赖的 step_click_formation / step_click_auto_challenge /
    _debug_screenshot 在当前代码中已不存在，本文件为重写版本，
    旧版已备份至 backup/tasks_腐烂版_20260901/。
"""

from typing import Optional, Tuple

from tasks.base import BaseTask
from task_registry import TaskRegistry, TaskMeta

# ── 静态元数据 ────────────────────────────────────────────
task_id = "auto_farm"
label = "自动推图"
description = "幻灵/普通交替推图，连败自动换阵容，全部通关后自动结束"

# v2.1.18：沙漏收益单轮最多领取次数
# v2.1.20（用户 15:12「最高可能不止 3」）：这里**不再是 12 的硬顶**——
# 初始次数由屏幕上的 xN 决定，可能远大于 3（不再假定 3 是上限）。
# 取 99 = 正则 [xX×](\d{1,2}) 能读到的量程上限，纯粹是防御性兜底；
# 实际生效次数由「读到的 N」与「xN 归零即停」共同决定 —— 即便误读成大数，
# 只要 xN 归零就立刻收手，不会真的多点。
_HG_MAX_CLAIMS = 99
# v2.1.19：逐次验证参数（用户 14:45 定案：点一下数字立刻变 → 验证窗口可短；
#          且「宁可重试也要领满」→ 重试预算给足，但**生效次数**仍以游戏额度封顶）
_HG_SETTLE_TRIES = 3      # 每次点击后最多轮询 3 次
_HG_SETTLE_WAIT = 1.0     # 轮询步长（秒）→ 单次点击最多等 3s
_HG_RETRY_WAIT = 1.5      # 判定「未被结算」后的额外等待再补点
# v2.2.2（用户 09-12 真机「点击生效了却一直识别 x3」）：**保留 4**。
# 该预算由用户硬规则确定（tests V1：x3 且前 3 次点击被结算动画吞掉时，
# 需 6 次点击才能领满 3 次）→ 砍预算会破坏「宁可重试也要领满」的契约。
# 本 bug 的真正修法在**读数侧**（见 _HG_ANCHOR_MAX_DIST / _read_hg_count）：
# 只要读数可靠，"未递减"就真的意味着次数还在，补点不会点到钻石付费档。
_HG_MAX_RETRY = 4         # 相对额度的额外点击预算（总点击 ≤ 额度 + 4）
# 沙漏 xN 的**读取窗口**（图像比例坐标）。v2.2.13（用户 2026-09-17 11:37 报障
# 「未检测到沙漏剩余次数」但屏幕上明明有 x3）——实测证据表明 xN 的 y 位置
# **随界面状态浮动很大**，旧窗格 0.65<ry<0.90 会把下面两种情况都排除 ✗：
#   · 领取弹层内（工具点按钮在 屏幕y=956 → ry≈0.93）→ 超出上界 ✗
#   · 「点击收获奖励.png」素材实测 ry≈0.625 → 低于下界 ✗
#   · 主界面素材（挂机关卡主界面 x2 @ry0.769、本图 x3 @ry0.799）→ 仅在窗内 ✓
# 故放宽为 0.70<rx、0.55<ry<0.99；横向上界同步放宽（领取弹层里 x 约 0.76）。
# xN 的**置信度下限**。v2.2.13（用户 2026-09-17 两张现场截图实测）：
# 同一枚徽标在不同状态下 OCR 结果波动很大 —— 「收获奖励」弹层里读作 `X3`
# 且 **conf 仅 0.66**（紧贴旧的 0.60 下限 ✗），点完之后读作 `x3` conf 0.84 ✓。
# 动画/光晕帧再差一点就会掉到 0.5x → 被门槛滤掉 → 「未检测到 xN」 ✗。
# 因候选还受**区域 + xN 形状**双重约束（实测该区内除徽标外无其它 xN），
# 故下限放宽到 0.45（给动画帧留余量，不引入误报）。
_HG_CONF_MIN: float = 0.45
_HG_READ_TRIES: int = 5          # 首次读数采样次数（OCR 检出波动，多采几次）
_HG_REGION_RX_MIN: float = 0.70
_HG_REGION_RY_MIN: float = 0.55
_HG_REGION_RY_MAX: float = 0.99
# 读数与沙漏图标的**最大容许距离**（像素，图像坐标系）。
# 次数标注一定紧贴它所属的按钮；超出该距离的 xN 属于界面其他元素
# （弹层数量/倍率标注等）→ 视为"没有次数可读"，从而在免费次数用尽后
# **及时收手**，不会因为读到无关 xN 而继续点到钻石付费档。
# 真机实测参照：x2 在图像 (1085,787)，沙漏按钮在其下方 30px 内。
_HG_ANCHOR_MAX_DIST = 260
_HG_NO_PROGRESS_MAX = 5   # 一次都没成功时，连续被吞到这个数就停手（防死点）
# v2.2.22（用户 10-27 规则）：**只要显示 xN 就是免费**（不同 VIP 免费次数不同，
# 基础 2 次）；只有显示 **50/100/…（纯数字定价）** 才是"要花钻石" → 才停手。
# 故"纯数字"候选须 ≥ 该阈值才算定价（xN 的值带 x 前缀，不会误判）。
_HG_PRICE_MIN: int = 10
# 点击次数硬上限（防读数异常时死点；正常远小于它）
_HG_SAFETY_CLICKS: int = 20


class AutoFarmTask(BaseTask):
    task_id = task_id
    label = label
    description = description

    # v2.1.9（用户 22:35 规范）循环语义声明——挂机关卡页即执行域
    loop_screens = frozenset({'nav.idle_stage'})

    # v1.5.0：模式已固定，无可调参数
    options_schema = {}

    def preflight(self) -> Tuple[bool, str]:
        """执行前确认游戏窗口存在并可聚焦。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""


    def _hourglass_region(self):
        """沙漏搜索区域（**屏幕绝对坐标**），拿不到窗口尺寸时返回 None=全屏。

        v2.1.18：template_match 的 region 是屏幕坐标，旧实现传的却是按
        1280×1024 比例算出的图像坐标 → 窗口不在屏幕原点时搜索框整体偏移、
        恒不命中。此处用 window_rect() 的窗口原点+尺寸换算出真正的屏幕区域。
        """
        try:
            l, t, w, h = self.bot.window_rect()
        except Exception as _e:
            self.log(f"[调试] _hourglass_region() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return None
        if w <= 0 or h <= 0:
            return None
        return (int(l + 0.75 * w), int(t + 0.60 * h),
                int(0.25 * w), int(0.30 * h))

    def _claim_idle_rewards(self) -> None:
        """挂机主界面领取收益（v1.9.49，用户 12:55 流程定案）。

        ① OCR 找宝箱下方累计时间 XX:XX:XX（rx 0.40~0.60,
           ry 0.70~0.90，conf≥0.8）→ 点击（等效点宝箱）；
        ② 弹窗「收获奖励」（OCR 0.99）→ 点击；
        ③ 右下沙漏 xN（OCR 0.91，rx>0.75）循环点击，每点一次
           次数减一，xN 消失（或 12 次上限）即完成。
        判据全部 OCR 实测（2026-09-10 挂机/收获弹窗截图），
        模板仅沙漏含变化数字易失配 → 弃用。
        """
        import re
        if not hasattr(self.bot, "_ocr_screen"):
            return   # v1.9.49：无 OCR 能力的 bot（Mock）跳过
        items = self.bot._ocr_screen()
        chest = next(
            ((it[1], it[0]) for it in items
             if len(it) >= 4 and it[3] >= 0.8
             and re.fullmatch(r"\d{1,2}:\d{2}:\d{2}",
                              it[2].strip().replace("O", "0"))
             and 0.40 < it[1] / 1280.0 < 0.60
             and 0.70 < it[0] / 1024.0 < 0.90), None)
        if not chest:
            self.log("[信息] 未找到挂机收益宝箱（可能刚领取过），跳过")
            return
        # v2.1.18（真机 13:32 假成功复盘）：**统一坐标系**。
        # template_match 的 region 入参与返回值都是**屏幕绝对坐标**，
        # 而本方法的 chest 来自 OCR = **图像坐标**。旧实现把按 1280×1024
        # 比例算出的图像坐标 region 直接当屏幕坐标传入，又把返回的屏幕 y
        # 与图像 y 比较 → 区域搜索恒不命中（返回 None 覆盖了本来可用的
        # 全屏结果），只能退回比例偏移（真机日志 点击=(640,738)=799-61
        # 即走兜底的证据）。
        # 修：① region 经 window_rect() 按窗口比例换算成屏幕坐标；
        #     ② 命中点经 screen_to_image() 换回图像坐标，再与 chest 同系比较。
        _hg_tpl = r"templates/farm_hourglass.png"
        _hg_region = self._hourglass_region()
        _hg_screen = self.bot.template_match(
            _hg_tpl, threshold=0.80, silent=True,
            scale_range=(0.8, 1.3, 0.1), region=_hg_region)
        if _hg_screen is None:
            # v1.9.55 的区域限定失效时（窗口偏移异常/分辨率变化）→ 全屏兜底；
            # 「同排 ±80px」校验仍能挡掉「已完成全部挑战」下方 y≈870 的相似橙框
            _hg_screen = self.bot.template_match(
                _hg_tpl, threshold=0.80, silent=True,
                scale_range=(0.8, 1.3, 0.1))
        _hg_y = (self.bot.screen_to_image(*_hg_screen)[1]
                 if _hg_screen else None)
        if _hg_y is not None and abs(_hg_y - chest[1]) <= 80:
            chest_click = (chest[0], _hg_y)
        else:
            if _hg_y is not None:
                self.log(f"[警告] 沙漏模板命中位置异常 图像y={_hg_y}，"
                         f"与宝箱不同排，退回比例偏移")
            chest_click = (chest[0],
                           max(0, chest[1] - int(0.060 * 1024)))
        self.log(f"[点击] 挂机收益宝箱 | 时间={chest} 点击={chest_click}")
        self.bot.click_at(chest_click[0], chest_click[1], '挂机_宝箱')
        self._sleep(1.5)
        items = self.bot._ocr_screen()
        btn = self.bot.find_text(items, '收获奖励', min_conf=0.8)
        if btn:
            self.log("[点击] 「收获奖励」")
            self.bot.click_at(btn[0], btn[1], '挂机_收获奖励')
            # v2.1.19（真机 14:31 复盘）：等待 2.0→4.0s，并清掉「点击空白处」
            # 结算弹层。用户 14:45 实测确认：点青色 xN 按钮「数字立刻变」，
            # 所以前 3 次点击不是被动画吞 —— 是根本没送达游戏（多半卡在
            # 「收获奖励」后的结算过渡态）。此处的等待/清弹层只是降低概率，
            # **真正的保障是下面的逐次点击验证**。dismiss_blink_popup 未命中零副作用。
            self._sleep(4.0)
            if self.dismiss_blink_popup(self.bot._ocr_screen()):
                self.log("[信息] 已关闭奖励结算弹层")
                self._sleep(1.0)
        else:
            self.log("[警告] 未出现「收获奖励」弹窗，跳过沙漏领取")
            return
        # v1.9.56（真机 16:38 x2 点了 3 次修正）：按 xN 值精确点击——
        # ① OCR 读右上「x\d+」的 N（用户定案：点击次数=N 值）；
        # ② 模板定位沙漏图标 → 逐次点击，**每次点完都验证**；
        # ③ 验证：xN 递减/消失 = 该次生效。
        # v2.1.18（真机 13:32 假成功复盘）：旧实现「首次扫描读不到 xN 就
        #   直接打 [成功]」——纯负向判成功；且阈值 0.80 会随机滤掉实测
        #   0.78~0.82 的 x3 读数 → 零点击假成功。
        # v2.1.19（真机 14:31，用户指出「最多只能领取三次，为什么点了六次」）：
        #   旧结构是「按 N 连点一轮 → 重读 → 仍读到 N 就再连点一轮」，
        #   于是 x3 被打成了 6 次。真机日志证据：第 1 轮 3 次点击后重读
        #   仍是 x3（OCR 为实时截屏，无缓存），第 2 轮 3 次才生效 ——
        #   前 3 次是被结算动画吞掉的。
        #   修：① 逐次点击后**轮询** xN 是否递减，递减才计一次
        #          （把"连点"换成"点了等结算"）；
        #       ② 未递减 = 该次被吞 → 等动画走完补一次（最多 _HG_MAX_RETRY 次）；
        #       ③ **生效次数以首次读到的 N 为上限**（≤ 游戏额度，绝不多领）。
        def _hg_candidates():
            """读取区内的候选：**xN（免费剩余次数）** 与 **钻石定价**。

            Returns:
                (xn, price)
                  xn    = [(值, cx, cy, conf, 文本), ...]  ← xN 形状
                  price = [(金额, cx, cy, conf, 文本), ...] ← 无 x 前缀的纯数字

            v2.2.2：抽出为独立函数 —— 既供读数取用，也在「未结算」时打诊断
            （把区内**所有**候选写出来，复发即自带证据）。
            **用户 09-13 10:02 给出的判据**：免费次数用完后按钮显示的**不是**
            xN，而是 50/100/200 这类**钻石定价（纯数字）** → 读到纯数字即
            "免费次数已用尽、再点要花钻石"，必须立即停手。
            """
            xn, price = [], []
            # v2.2.15：**必须 no_cache** —— 缓存指纹是稀疏采样，徽标
            # x3→x2 这种小面积变化不改指纹 → 会返回旧结果，导致"点击明明
            # 生效却判未结算"（真机 2026-09-17 实证）
            for it in self.bot._ocr_screen(no_cache=True):
                if len(it) < 4 or it[3] < _HG_CONF_MIN:
                    continue
                # v2.2.13：窗口放宽 + 抽成常量（见 _HG_REGION_* 处的实测依据）
                if not (_HG_REGION_RX_MIN < it[1] / 1280.0
                        and _HG_REGION_RY_MIN < it[0] / 1024.0
                        < _HG_REGION_RY_MAX):
                    continue
                _t: str = it[2].strip()
                # v2.2.16：**数字/字母混淆纠正**（真机 2026-09-17 诊断日志实证）
                # 徽标显示 `x1`，OCR 给出 `xl`（小写 L）✗ → 正则不匹配 →
                # 误报「未检测到沙漏剩余次数」。1↔l/I/|、0↔O/o、5↔S、8↔B、
                # 2↔Z 是这类小字最常见的混淆，统一纠正后再匹配（显示仍用原文）。
                _n: str = (_t.replace('l', '1').replace('I', '1')
                           .replace('|', '1').replace('O', '0')
                           .replace('o', '0').replace('S', '5')
                           .replace('B', '8').replace('Z', '2'))
                m = re.fullmatch(r"[xX×](\d{1,2})", _n)
                if m:
                    xn.append((int(m.group(1)), it[1], it[0], it[3], _t))
                elif re.fullmatch(r"\d{1,4}", _n) and int(_n) >= _HG_PRICE_MIN:
                    # v2.2.22：≥ _HG_PRICE_MIN 才算钻石定价（50/100/200…）
                    price.append((int(_n), it[1], it[0], it[3], _t))
            return xn, price

        def _nearest_in_range(cands, anchor):
            """取锚点邻域内最近的候选值；超出 `_HG_ANCHOR_MAX_DIST` 视为无关文本。

            次数/定价一定紧贴它所属的按钮，故必须以图标为锚筛选。
            返回 None = 邻域内没有该形状文本。
            """
            if not cands:
                return None
            if anchor is None:
                return cands[0][0]
            near = [c for c in cands
                    if (c[1] - anchor[0]) ** 2 + (c[2] - anchor[1]) ** 2
                    <= _HG_ANCHOR_MAX_DIST ** 2]
            if not near:
                return None
            return min(near, key=lambda c: (c[1] - anchor[0]) ** 2
                                          + (c[2] - anchor[1]) ** 2)[0]

        def _read_hg_count(anchor: Optional[Tuple[int, int]] = None):
            """读沙漏剩余次数 xN；读不到返回 None（不猜）。

            v2.2.2（真机 09-12：「点击生效了，但工具一直识别 x3」）：
              旧实现取**宽区域内的第一个** xN。该区（rx>0.75 且 ry 0.65~0.90）
              占屏 25%×25%，一旦区内存在**另一个恒定的 xN**（弹层里的数量/
              倍率标注），读数就永远停在那个值、与真实剩余次数无关
              → 每次点击后都判「未结算」→ 反复补点，最坏点到**钻石付费档**。
              改为：**以沙漏图标为锚**，取距锚点最近的 xN（次数属于该按钮）；
              无锚点时退回旧行为（向后兼容）。
            """
            # v2.2.2：**只认紧贴沙漏图标的那个 xN**（次数标注一定在旁边）。
            # 超出 _HG_ANCHOR_MAX_DIST 的一律视为界面其他元素 →
            # 返回 None（= 读不到次数），避免被无关的恒定 xN 骗着继续补点。
            _xn, _price = _hg_candidates()
            return _nearest_in_range(_xn, anchor)

        def _locate_hourglass():
            """定位沙漏按钮（先区域后全屏），返回屏幕绝对坐标或 None。"""
            hg = self.bot.template_match(
                _hg_tpl, threshold=0.80, silent=True,
                scale_range=(0.8, 1.3, 0.1), region=_hg_region)
            if hg is None:
                hg = self.bot.template_match(
                    _hg_tpl, threshold=0.80, silent=True,
                    scale_range=(0.8, 1.3, 0.1))
            return hg

        n_val = None
        # v2.2.13：3 → 5 —— 小徽标的 OCR **检出本身就有波动**（某帧可能整条
        # 都不返回），多给几次采样机会；每次 1.2s，最坏 ~6s 可接受
        for _attempt in range(_HG_READ_TRIES):   # v2.1.18：首次读数重试
            if self.check_stop():
                return
            n_val = _read_hg_count()
            if n_val is not None:
                break
            if _attempt < _HG_READ_TRIES - 1:
                self._sleep(1.2)
        if n_val is None:
            # v2.2.13：**失败要留证据** —— 打印右下区所有疑似候选
            # （文本/坐标/置信度），下次复发即可一眼看出是"位置超窗"
            # 还是"OCR 没读到"（此前只有一句"未检测到"，无从定位）
            _diag = [(it[2], int(it[1]), int(it[0]), round(float(it[3]), 2))
                     for it in self.bot._ocr_screen(no_cache=True)
                     if len(it) >= 4 and it[3] >= 0.5
                     and it[1] / 1280.0 > 0.6 and it[0] / 1024.0 > 0.5]
            self.log(f"[信息] 未检测到沙漏剩余次数（xN），本轮不做沙漏领取"
                     f"｜右下区候选={_diag if _diag else '无'}")
            return

        allowance = n_val                        # v2.1.20：以屏幕读数为准（可能 >3）
        if allowance > _HG_MAX_CLAIMS:           # 纯防御：超出正则量程视为误读
            self.log(f"[警告] 沙漏次数读数 x{n_val} 超出预期，"
                     f"按 {_HG_MAX_CLAIMS} 处理")
            allowance = _HG_MAX_CLAIMS
        self.log(f"[信息] 沙漏剩余领取次数 x{n_val}"
                 f"（本次最多领取 {allowance} 次）")
        claimed = 0
        clicks = 0
        misses = 0
        remaining = n_val
        # v2.2.2（用户 09-13 10:02 判据）：读到**钻石定价**即免费次数已用尽。
        # 有值 = 必须立刻停手（再点就是花钻石）。
        paid_cost: Optional[int] = None
        # v2.2.22：循环以**屏幕上还剩几次**为准（remaining>0 就继续领），
        # 不再用初始读数当上限 —— 用户规则：只要还显示 xN 就是免费 ✓
        while remaining > 0 and clicks < _HG_SAFETY_CLICKS:
            if self.check_stop():
                return
            # **点击前**的最后一道闸：按钮已显示钻石定价 → 一次都不点。
            if paid_cost is not None:
                self.log(f"[信息] 沙漏按钮已显示钻石定价 {paid_cost}"
                         f"（免费次数已用尽）→ 停止领取，不消耗钻石")
                break
            # 一次都没成功却连续被吞 → 判为按钮不可用，别死点
            # 说明：v2.2.2 曾在此加「claimed==0 且已点满额度就停」的守卫，
            # 但那会破坏用户硬规则（V1：x3 且前 3 次点击被吞时仍须补点领满
            # 3 次）→ 已移除。钻石付费档的防护改由**读数侧**承担
            # （xN 距离上限 + 钻石定价判定）。
            # v2.2.22：改为**不论已领几次**都生效 —— 读数失灵时若继续盲点，
            # 可能点到钻石档（每点一次都花钻）✗。连续 N 次没确认到递减即停。
            if misses >= _HG_NO_PROGRESS_MAX:
                self.log(f"[警告] 连续 {misses} 次点击未确认到次数递减"
                         f"（已生效 {claimed} 次）→ 停止领取，避免盲点花钻")
                break
            hourglass = _locate_hourglass()
            if not hourglass:
                self.log("[警告] 沙漏图标未命中，停止领取")
                break
            clicks += 1
            self.log(f"[点击] 沙漏领 2 小时收益"
                     f"（第 {clicks} 次点击｜已生效 {claimed}/{allowance}"
                     f"｜剩余 x{remaining}） | 屏幕={hourglass}")
            self.bot._click_abs(hourglass[0], hourglass[1])
            # v2.2.2：以**沙漏图标**为锚读次数（图标=屏幕坐标 → 转图像坐标，
            # 计数一定在图标旁边；避免宽区域里别的 xN 抢答）
            try:
                _off_l, _off_t = self.bot._screen_offset()
            except Exception as _e:
                self.log(f"[调试] _screen_offset() 异常已忽略: {_e}")
                _off_l, _off_t = 0, 0
            _anchor = (hourglass[0] - _off_l, hourglass[1] - _off_t)
            # 逐次验证：轮询等待 xN 递减（用户实测点击后数字立刻变，
            # 故 _HG_SETTLE_TRIES×_HG_SETTLE_WAIT=3s 足够判定）
            ok = False
            for _wait in range(_HG_SETTLE_TRIES):
                self._sleep(_HG_SETTLE_WAIT)
                if self.check_stop():
                    return
                # 单次截图同时取两类候选（省一次 OCR）
                _xn, _prices = _hg_candidates()
                # ① **钻石定价出现** = 上一次点击已是最后一次免费领取
                #    （用户 09-13 判据）→ 记为该次生效并收手，绝不再点。
                _cost = _nearest_in_range(_prices, _anchor)
                if _cost is not None:
                    paid_cost = _cost
                    remaining = 0
                    ok = True
                    break
                # ② 否则看 xN 是否递减
                new_val = _nearest_in_range(_xn, _anchor)
                # ⚠️ v2.2.22 核心修复：**读不到 ≠ 已领完** ✗
                # 旧实现把 None 当成 remaining=0 → x3 只领 2 次就收手
                # （真机 11:59 日志：x3 → 领 2 次 → 「共 2 次」，但第 3 次免费）✗
                # 现在：只有**真读到更小的 xN** 才算这次生效；读不到/未变化
                # 都按"这次没确认"处理 → 交给 misses 重试链路（有上限）✓
                if new_val is not None and new_val < remaining:
                    remaining = new_val
                    ok = True
                    break
            if ok:
                claimed += 1
                misses = 0
                if remaining == 0:
                    break
                continue
            # 未被结算 = 该次点击没送达游戏 → 计数并补点
            misses += 1
            # v2.2.2：保留「未被结算」措辞（既有契约/测试依赖该子串），
            # 但补上「读数未递减」的如实表述 + 区内全部候选诊断。
            # 旧文案「仍显示 x{remaining}」是**误导**：remaining 只在读到
            # 更小值时才更新，并不代表这次读到的实际值。
            _xn2, _prices2 = _hg_candidates()
            _cands_txt = ('; '.join(
                f'{txt}(rx={cx / 1280:.2f},ry={cy / 1024:.2f},'
                f'conf={cf:.2f})' for _v, cx, cy, cf, txt in _xn2)
                if _xn2 else '无')
            if _prices2:
                _cands_txt += ('｜钻石定价=' + '; '.join(
                    f'{txt}(rx={cx / 1280:.2f},ry={cy / 1024:.2f})'
                    for _v, cx, cy, _cf, txt in _prices2))
            self.log(f"[警告] 第 {clicks} 次点击未被结算"
                     f"（读数未递减，记录 x{remaining}），等待 "
                     f"{_HG_RETRY_WAIT:g}s 后重试（{misses}/"
                     f"{_HG_NO_PROGRESS_MAX}）"
                     f"｜读数区候选[{len(_xn2)}]={_cands_txt}")
            self._sleep(_HG_RETRY_WAIT)

        if claimed and remaining == 0:
            if paid_cost is not None:
                self.log(f"[成功] 沙漏收益领取完毕（共 {claimed} 次；"
                         f"免费次数已用尽，按钮转入钻石定价 {paid_cost}，未消耗钻石）")
            else:
                self.log(f"[成功] 沙漏收益领取完毕（共 {claimed} 次）")
        elif claimed:
            self.log(f"[警告] 沙漏收益部分领取（已生效 {claimed} 次，"
                     f"读数 x{remaining} 未归零）")
        else:
            self.log("[警告] 沙漏未实际领取（点击被结算动画吞掉或图标未命中）")

    def run(self) -> str:
        """执行推图。

        Returns:
            'done' / 'failed' / 'stopped'
        """
        if not hasattr(self.bot, "run_auto_farm"):
            self.log("bot 不支持 run_auto_farm()，请检查 afk_bot 版本")
            return "failed"

        self.log("启动")
        # v1.9.50（真机 15:50 修正）：领取挂机收益必须在**到达挂机
        # 主界面之后**——v1.9.49 插在 run() 开头，此时还在家园
        # （bot 引擎 run_auto_farm 内部才导航），OCR 找不到宝箱时间
        # → 永远跳过。改为：任务层先导航到挂机关卡页 → 领取 → 再交
        # 引擎推图（引擎启动定位识别已在域内 → 快速续跑，无二次成本）
        if hasattr(self.bot, "_ocr_screen"):
            from tasks.global_nav import GlobalNavigator
            nav = GlobalNavigator(
                self.bot, logger=self.log,
                stop_check=self.check_stop,
            )
            if nav.navigate_to('idle_stage'):
                self._claim_idle_rewards()
            else:
                self.log("[警告] 导航未达挂机关卡页，跳过挂机收益领取")
        result = self.bot.run_auto_farm(
            max_time=self.max_time,
            max_turns=self.max_turns,
            start_turns=self._start_turns,
        )

        # 规范返回值，避免 bot 侧异常导致调度器状态混乱
        if result not in ("done", "failed", "stopped"):
            self.log(f"[警告] 未知返回值 {result!r}，按 failed 处理")
            result = "failed"

        self.log(f"结束 | 结果={result}")
        return result

    def summary(self) -> str:
        """任务行摘要（v1.9.67 精简为核心功能，路径见抽屉说明）。"""
        return "幻灵/普通交替无限推图"


# ── 注册 ──────────────────────────────────────────────────
TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=True,
    icon="🤖",
    group="游戏任务",
    status="ready",
    options_schema=AutoFarmTask.options_schema,   # 空 dict → GUI 不渲染参数表单
    nav_target='idle_stage',   # v1.8.31：入口=挂机关卡页（2 层）
    show_metrics=True,   # v1.9.67（用户 17:56）：仅自动推图保留时间/轮次
))

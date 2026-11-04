"""结社战线「结算页判定」双向矩阵测试（v2.3.7）。

真机两轮教训：
  ① 漏判（15:15）：胜利/失败是大字艺术字 → OCR 读不出 → 卡在「战斗中」空转 ✗
  ② 误判风险：「继续挑战」这类词在别处也可能出现（如挂机极限弹窗的
     「是否确认继续挑战？」）→ 绝不能判成结算 ✗

本测试用一张矩阵同时验证两个方向。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_guild_result_matrix.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tasks.guild_battle import GuildBattleTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f'[{"PASS" if cond else "FAIL"}] {name}'
          + (f'  {detail}' if detail else ''))


# ── 矩阵： (名称, 该页 OCR 文本, 期望"是结算", 期望"是胜利" or None=不适用) ──
CASES = [
    # 正向：结算页（**绝不漏判**）
    ('胜利·全读到', ['战斗胜利', '推荐提升', '继续挑战'], True, True),
    ('胜利·只有按钮', ['继续挑战'], True, True),
    ('胜利·只有艺术字', ['战斗胜利'], True, True),
    ('胜利·只有推荐提升', ['推荐提升'], True, True),
    ('失败·全读到', ['战斗失败', '再次挑战'], True, False),
    ('失败·只有按钮', ['再次挑战'], True, False),
    ('失败·只有艺术字', ['战斗失败'], True, False),
    # 反向：非结算页（**绝不误判**）
    ('结社战线主界面', ['结社战线', '扫荡', '挑战', '×0/4', '今日剩余'], False, None),
    ('备战界面', ['通关阵容', '战斗', '一键采用'], False, None),
    ('玩法目录', ['玩法目录', '公会玩法', '常规玩法', '活动'], False, None),
    ('公会玩法页(卡片)', ['公会征锋', '结社战线', '狩猎悬赏', '沙盘演兵',
                          '公会争霸战', '派遣编队中', '公会玩法'], False, None),
    ('战斗中', ['自动战斗', '跳过', '×2', '速度'], False, None),
    ('挂机极限弹窗', ['您已到达本日挂机极限进度', '是否确认继续挑战？',
                      '本日极限进度：1100'], False, None),
]

print('═' * 66)
print('结社战线「结算页判定」双向矩阵（v2.3.7）')
print('═' * 66)

_t = GuildBattleTask.__new__(GuildBattleTask)

print('\n── ① 判定矩阵 ──')
for name, texts, want_result, want_win in CASES:
    got_result = _t._is_result_page(texts)
    ok = (got_result == want_result)
    if ok and want_win is not None:
        # 复刻主循环里的胜负判定
        got_win = (any('战斗胜利' in x for x in texts)
                   or any('继续挑战' in x for x in texts)
                   or any('推荐提升' in x for x in texts))
        ok = (got_win == want_win)
        detail = f'结算={got_result} 胜利={got_win}'
    else:
        detail = f'结算={got_result}（期望 {want_result}）'
    check(f'{name}', ok, detail)

print('\n── ② 补充断言 ──')
check('②-1 主界面/备战**绝不**被判成结算',
      not _t._is_result_page(['结社战线', '扫荡', '挑战'])
      and not _t._is_result_page(['通关阵容', '一键采用']))
check('②-2 空屏/乱码不误判',
      not _t._is_result_page([]) and not _t._is_result_page(['???', '---']))
check('②-3 仅「再次挑战」也必须认（真机艺术字读不出场景）',
      _t._is_result_page(['再次挑战']) is True)
check('②-4 排除词优先级高于正向词'
      '（同屏既有"扫荡"又有"再次挑战" → 判非结算，避免主界面残留 ✗）',
      _t._is_result_page(['扫荡', '再次挑战']) is False,
      '主界面残留优先')

print()
print(f'══ 结算判定矩阵：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)

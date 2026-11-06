# -*- coding: utf-8 -*-
"""v2.4.2 下载 temp 原子落盘行为测试（学习 AUTO-MAS 移植）。

锁死行为：
  A1 幂等快路：同版本完整包已存在 → 不发请求直接返回
  A2 原子写成功：dest 完整、`.temp` 消失
  A3 中断：dest 绝不出现半截文件，temp 保留已下部分
  A4 Range 续传：第二轮基于 temp 合并 → dest 完整、temp 清除
  A5 temp 完整但没改名（上轮末尾崩溃）→ 入口补 rename
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from afk_bot import updater as U          # noqa: E402

PASSED = [0]
FAILED = []


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


# ── 诊断日志隔离（不污染用户文件）─────────────────────────────
_diag = os.path.join(tempfile.gettempdir(), 'afkj_net_diag_atomic_test.log')
if os.path.isfile(_diag):
    os.remove(_diag)
os.environ['AFKJ_NET_DIAG_PATH'] = _diag

# ── fake 网络层 ──────────────────────────────────────────────
class _FakeResp:
    """流式响应：chunks 逐块吐出；exc 非空时在吐完后抛异常（模拟断流）。"""

    def __init__(self, chunks, status=200, exc=None):
        self.status = status
        self._chunks = list(chunks)
        self._exc = exc

    def read(self, n=-1):
        if self._chunks:
            return self._chunks.pop(0)
        if self._exc is not None:
            e, self._exc = self._exc, None
            raise e
        return b''

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    def __init__(self, resp):
        self.resp = resp
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        # 代码发 Range 头；fake 忽略它，直接按预置剩余块给数据
        return self.resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_opener = _FakeOpener(None)
_old_bo = U._build_opener
_old_probe = U._probe_local_proxies
U._build_opener = lambda pm: _opener          # 所有通道用同一 fake
U._probe_local_proxies = lambda force=False: []


def _mk_info(name, size):
    return U.UpdateInfo('v1', 'n', '', 'https://example/x', name, size)


def _cleanup(td):
    for f in Path(td).glob('*'):
        f.unlink(missing_ok=True)


try:
    td = tempfile.mkdtemp(prefix='afkj_atomic_')

    # ── A1 幂等快路 ──
    info1 = _mk_info('full.exe', 6)
    dest1 = Path(td) / 'full.exe'
    dest1.write_bytes(b'ABCDEF')
    _opener.resp = _FakeResp([b'XXXXXX'])
    got = U.download_asset(info1, dest_dir=Path(td))
    check('A1a 完整包已存在 → 直接返回', got == dest1)
    check('A1b 未发任何网络请求（幂等）', _opener.calls == 0,
          f'calls={_opener.calls}')
    check('A1c 内容未被破坏', dest1.read_bytes() == b'ABCDEF')

    # ── A2 原子写成功 ──
    _cleanup(td)
    payload = b'A' * 300 + b'B' * 300
    info2 = _mk_info('app.exe', len(payload))
    _opener.resp = _FakeResp([payload[:100], payload[100:200],
                              payload[200:]], status=200)
    got2 = U.download_asset(info2, dest_dir=Path(td))
    check('A2a dest 完整落盘',
          got2 == Path(td) / 'app.exe'
          and got2.read_bytes() == payload)
    check('A2b temp 已消失（原子改名）',
          not (Path(td) / 'app.exe.temp').exists())

    # ── A3 中断：dest 不出现，temp 保留 ──
    _cleanup(td)
    half = payload[:len(payload) // 2]
    _opener.resp = _FakeResp([half[:100], half[100:]],
                             exc=ConnectionResetError('reset by peer'))
    try:
        U.download_asset(info2, dest_dir=Path(td), max_retries=1)
        check('A3a 断流必须抛 UpdateError（不该走到这）', False)
    except U.UpdateError:
        check('A3a 断流抛 UpdateError', True)
    check('A3b dest 绝不出现半截成品',
          not (Path(td) / 'app.exe').exists())
    tmp_part = Path(td) / 'app.exe.temp'
    check('A3c temp 保留已下部分（续传基础）',
          tmp_part.exists() and tmp_part.stat().st_size == len(half),
          f'size={tmp_part.stat().st_size if tmp_part.exists() else "-"}')

    # ── A4 Range 续传合并 ──
    rest = payload[len(half):]
    _opener.resp = _FakeResp([rest[:100], rest[100:]], status=206)
    got4 = U.download_asset(info2, dest_dir=Path(td))
    check('A4a 续传后 dest 完整',
          got4.exists() and got4.read_bytes() == payload)
    check('A4b 续传后 temp 清除',
          not tmp_part.exists())

    # ── A5 temp 完整但没改名 → 入口补 rename ──
    _cleanup(td)
    (Path(td) / 'app.exe.temp').write_bytes(payload)
    _calls_before_a5 = _opener.calls
    got5 = U.download_asset(info2, dest_dir=Path(td))
    check('A5a temp 完整 → 补 rename 返回 dest',
          got5 == Path(td) / 'app.exe' and got5.read_bytes() == payload)
    check('A5b 未发网络请求', _opener.calls == _calls_before_a5,
          f'calls {_calls_before_a5}→{_opener.calls}')
    check('A5c temp 清除', not (Path(td) / 'app.exe.temp').exists())

finally:
    U._build_opener = _old_bo
    U._probe_local_proxies = _old_probe
    os.environ.pop('AFKJ_NET_DIAG_PATH', None)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')

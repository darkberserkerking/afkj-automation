# -*- coding: utf-8 -*-
"""检查更新：网络瞬断重试与可读提示（v2.3.48）。

真机 2026-11-06 23:05 报障：
  检查更新失败：[ASN1: NOT_ENOUGH_DATA] not enough data (_ssl.c:4057)

排查结论（实测）：
  · exe 内 certifi/cacert.pem 与源码 md5 一致（270954 B）→ 证书没问题
  · 源码 create_default_context()/项目 _ssl_context() 均 OK
  · 用户级/机器级无代理环境变量 → exe 直连 api.github.com
  · 连续 5 次 TLS 握手 5/5 成功 → 瞬时故障
因此改为：瞬时网络错误自动重试 + 把 `_ssl.c` 这类原文翻译成可操作提示。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_updater_net_retry.py
"""
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot import updater as U

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


# 测试期间把重试间隔降为 0（不引入真实等待）
U._NET_RETRY_WAIT = 0.0

GOOD_JSON = (
    b'{"tag_name":"v9.9.9","html_url":"https://example/x","body":"",'
    b'"assets":[{"name":"AFKJ_v9.9.9.exe",'
    b'"browser_download_url":"https://example/x.exe","size":1}]}'
)


class _SslBoom(Exception):
    """模拟 NOT_ENOUGH_DATA。"""


class _FlakyFetch:
    """前 N 次抛瞬时错误，之后返回正常响应。"""

    def __init__(self, fail_times, exc=None):
        self.fail_times = fail_times
        self.calls = 0
        self.exc = exc or _SslBoom(
            '[ASN1: NOT_ENOUGH_DATA] not enough data (_ssl.c:4057)')

    def __call__(self, url, token, timeout):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return GOOD_JSON


print('── ① 瞬时错误应自动重试 ──')
f1 = _FlakyFetch(fail_times=1)
try:
    info = U.check_latest(token='x', fetch=f1)
    check('1a 失败 1 次后重试成功', info is not None and f1.calls == 2,
          f'调用 {f1.calls} 次, tag={getattr(info, "tag", None)}')
except Exception as e:
    check('1a 失败 1 次后重试成功', False, f'{type(e).__name__}: {e}')

f2 = _FlakyFetch(fail_times=2)
try:
    info = U.check_latest(token='x', fetch=f2)
    check('1b 失败 2 次（用尽全部重试）仍成功',
          info is not None and f2.calls == 3, f'调用 {f2.calls} 次')
except Exception as e:
    check('1b 失败 2 次（用尽全部重试）仍成功', False, f'{type(e).__name__}: {e}')

print('\n── ② 持续失败：提示必须可读，不能暴露 _ssl.c ──')
f3 = _FlakyFetch(fail_times=99)
try:
    U.check_latest(token='x', fetch=f3)
    check('2a 持续失败应抛 UpdateError', False, '未抛异常')
except U.UpdateError as e:
    msg = str(e)
    check('2a 持续失败抛 UpdateError', True, msg[:60])
    check('2b 提示不含底层实现细节 _ssl.c', '_ssl.c' not in msg, msg[:80])
    check('2c 提示含可操作指引',
          any(k in msg for k in ('重试', '代理', '网络')), msg[:80])
    check('2d 重试次数有界（1+2=3 次）', f3.calls == 3, f'调用 {f3.calls} 次')

print('\n── ③ HTTPError 不得重试（401/403/404 有专门提示）──')
_404_calls = []


def _raise404(url, token, timeout):
    _404_calls.append(1)
    raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)


try:
    U.check_latest(token='x', fetch=_raise404)
    check('3a 404 抛 UpdateError', False, '未抛异常')
except U.UpdateError as e:
    check('3a 404 抛 UpdateError', True, str(e)[:50])
    check('3b 404 只请求 1 次（不重试）', len(_404_calls) == 1,
          f'调用 {len(_404_calls)} 次')
    check('3c 404 保留原有语义提示',
          'Release' in str(e) or 'Token' in str(e), str(e)[:60])

print('\n── ④ 辅助函数单元断言 ──')
check('4a NOT_ENOUGH_DATA 判定为瞬时（原文含 ASN1 标记）',
      U._is_transient_net_error(_SslBoom(
          '[ASN1: NOT_ENOUGH_DATA] not enough data (_ssl.c:4057)')))
check('4a2 小写空格形式同样命中',
      U._is_transient_net_error(_SslBoom('not enough data (_ssl.c:4057)')))
check('4b 普通 ValueError 不算瞬时',
      not U._is_transient_net_error(ValueError('bad json')))
_hint = U._friendly_net_hint(
    _SslBoom('[ASN1: NOT_ENOUGH_DATA] not enough data (_ssl.c:4057)'))
check('4c 本次报障文案被翻译成中文指引',
      'TLS' in _hint and '代理' in _hint, _hint[:60])
check('4d 超时错误有独立提示',
      '超时' in U._friendly_net_hint(TimeoutError('timed out')))

# ── 6 系：v2.3.53 `_default_fetch` 裸异常兜底（真机 v2.3.52 复现）──
# `_build_opener(None)` 在 `_try` 实参位置求值，位于保护网之外；
# `_ssl_context()` 抛裸 SSL 异常时曾绕过内联诊断出口，用户只看到
# 笼统的「网络连接被中断」。修复后任何裸异常都必须转成带
# 「已尝试／内部／诊断日志」的 UpdateError。
import ssl as _ssl                                        # noqa: E402

_diag_log_6 = os.path.join(
    os.environ.get('TEMP', os.getcwd()), 'afkj_net_diag_selftest6.log')
if os.path.isfile(_diag_log_6):
    os.remove(_diag_log_6)
os.environ['AFKJ_NET_DIAG_PATH'] = _diag_log_6
_old_bo = U._build_opener


def _bo_boom(_pm):
    raise _ssl.SSLError('SSLV3_ALERT_HANDSHAKE_FAILURE')


U._build_opener = _bo_boom
try:
    U._default_fetch('https://api.github.com/x', '', timeout=2)
    check('6a 裸 SSL 异常必须转成 UpdateError（不该走到这）', False)
except U.UpdateError as _e:
    _m6 = str(_e)
    check('6a 裸 SSL 异常转 UpdateError 而非外泄',
          '检查更新失败' in _m6, _m6[:50])
    check('6b 消息内联「已尝试」诊断', '已尝试' in _m6)
    check('6c 消息标注裸异常来源「内部」', '内部' in _m6)
    check('6d 消息含「诊断日志」路径', '诊断日志' in _m6)
    check('6e TLS 瞬断被翻译成中文指引', 'TLS' in _m6)
finally:
    U._build_opener = _old_bo
    os.environ.pop('AFKJ_NET_DIAG_PATH', None)

# ── 7 系：v2.3.54 `_ssl_context` 三级容错（真机日志实锤）──────────
# create_default_context() 的 win32 分支全量拼接 Windows 证书库一次性
# load —— 一张坏证书整体炸（exe 报 [ASN1: NOT_ENOUGH_DATA]）。修复后
# 必须逐证书容错降级，任何情况下都能产出可用 SSLContext。
import ssl as _sslmod                                    # noqa: E402

_diag_log_7 = os.path.join(
    os.environ.get('TEMP', os.getcwd()), 'afkj_net_diag_selftest7.log')
if os.path.isfile(_diag_log_7):
    os.remove(_diag_log_7)
os.environ['AFKJ_NET_DIAG_PATH'] = _diag_log_7
_old_cdc = _sslmod.create_default_context

try:
    check('7a 正常路径 _ssl_context 可用',
          len(U._ssl_context().get_ca_certs()) >= 1)

    def _cdc_boom(*a, **kw):
        raise _ssl.SSLError(
            '[ASN1: NOT_ENOUGH_DATA] not enough data (_ssl.c:4057)')

    _sslmod.create_default_context = _cdc_boom
    try:
        _ctx7 = U._ssl_context()
        check('7b 默认分支炸 → 逐证书容错分支顶上',
              isinstance(_ctx7, _sslmod.SSLContext)
              and len(_ctx7.get_ca_certs()) >= 1)
    except Exception as _e:                              # noqa: BLE001
        check(f'7b 默认分支炸 → 逐证书容错分支顶上（异常 {_e}）', False)

    _orig_enum = _sslmod.enum_certificates

    def _enum_boom(_store):
        raise OSError('no store')

    _sslmod.enum_certificates = _enum_boom
    try:
        _ctx7b = U._ssl_context()
        check('7c 证书库也炸 → 仅 certifi 兜底仍有 CA',
              isinstance(_ctx7b, _sslmod.SSLContext)
              and len(_ctx7b.get_ca_certs()) >= 1)
    except Exception as _e:                              # noqa: BLE001
        check(f'7c 证书库也炸 → 仅 certifi 兜底仍有 CA（异常 {_e}）', False)
    finally:
        _sslmod.SSLContext.enum_certificates = _orig_enum
finally:
    _sslmod.create_default_context = _old_cdc
    os.environ.pop('AFKJ_NET_DIAG_PATH', None)

check('7d 降级链留痕诊断日志',
      os.path.isfile(_diag_log_7)
      and 'ssl ctx default FAIL' in open(_diag_log_7, encoding='utf-8').read())

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')

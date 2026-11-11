"""Upload a verified local snapshot to the fixed trial site.

Windows CurrentUser DPAPI protects the update secret. Service access is read
once from stdin and never persisted. Only this machine's cooperating publishers
are serialized by the lock; the remote service must enforce its own integrity.
"""
import argparse
import ctypes
from ctypes import wintypes
from contextlib import contextmanager, closing
from datetime import date, datetime, timezone
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
import config

ORIGIN = 'https://etf-stock-flow-observer-1031.sweetowlet132.chatgpt.site'
LOCAL = ROOT / 'web-trial' / '.local'
SECRET_PATH = LOCAL / 'update-secret.dpapi'
VERSION = re.compile(r'^[a-z0-9-]{1,64}$')
KEY = re.compile(r'^(?:meta|catalog|ranks|industries|etfs|etf-details|stocks-\d{4})$')
HASH = re.compile(r'^[0-9a-f]{64}$')
REQUIRED = {'meta', 'catalog', 'ranks', 'industries', 'etfs', 'etf-details'}
MAX_BYTES = 2 * 1024 * 1024

class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

def dpapi(data, protect):
    buf = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out = Blob()
    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    kernel.LocalFree.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.restype = wintypes.HANDLE
    if protect:
        ok = crypt.CryptProtectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out))
    else:
        ok = crypt.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out))
    if not ok:
        raise RuntimeError('Windows 凭据保护操作失败')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(out.pbData, wintypes.HANDLE))

def init_secret():
    LOCAL.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    cipher = dpapi(secret.encode('ascii'), True)
    with SECRET_PATH.open('xb') as f:
        f.write(cipher)
    print(secret, flush=True)

@contextmanager
def lock():
    LOCAL.mkdir(parents=True, exist_ok=True)
    with (LOCAL / 'publish.lock').open('a+b') as f:
        f.seek(0)
        if f.read(1) == b'':
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('另一个本机发布正在运行') from None
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

def read_access():
    print('Ready for access JSON on stdin', flush=True)
    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.GetStdHandle(wintypes.DWORD(-10))
    mode = wintypes.DWORD()
    has_console = bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)))
    if sys.stdin.isatty() and not has_console:
        raise RuntimeError('无法安全关闭终端回显，请使用管道输入')
    if has_console and not kernel.SetConsoleMode(handle, mode.value & ~4):
        raise RuntimeError('无法关闭终端回显')
    try:
        line = sys.stdin.readline(16385)
    finally:
        if has_console:
            kernel.SetConsoleMode(handle, mode.value)
    if not line or len(line) > 16384:
        raise ValueError('访问凭据输入无效')
    data = json.loads(line)
    token = data.get('service_token') if isinstance(data, dict) else None
    if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
        raise ValueError('访问凭据输入无效')
    return token

def verify_snapshot(path):
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8-sig'))
    version = manifest.get('version')
    files, hashes = manifest.get('files'), manifest.get('hashes')
    td = manifest.get('trade_date')
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('版本无效')
    try:
        parsed = date.fromisoformat(td)
    except (TypeError, ValueError):
        raise ValueError('交易日期无效') from None
    if parsed > date.today():
        raise ValueError('交易日期不能晚于今天')
    if not isinstance(files, list) or not 6 <= len(files) <= 1006 or len(set(files)) != len(files):
        raise ValueError('文件清单无效')
    if not all(isinstance(k, str) and KEY.fullmatch(k) for k in files) or not REQUIRED.issubset(files):
        raise ValueError('文件名无效或缺少必需文件')
    if not isinstance(hashes, dict) or set(hashes) != set(files):
        raise ValueError('摘要清单无效')
    payloads = {}
    for key in files:
        expected = hashes[key]
        if not isinstance(expected, str) or not HASH.fullmatch(expected):
            raise ValueError('摘要无效')
        raw = (path / (key + '.json')).read_bytes()
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('文件大小或摘要不匹配: ' + key)
        json.loads(raw)
        payloads[key] = raw
    meta = json.loads(payloads['meta'])
    if meta.get('version') != version or (meta.get('trade_date') or meta.get('latest_date')) != td:
        raise ValueError('快照日期或版本不一致')
    generated = datetime.fromisoformat(meta['generated_at'].replace('Z', '+00:00'))
    if generated.tzinfo is None or generated > datetime.now(timezone.utc):
        raise ValueError('快照生成时间无效')
    db_path = Path(config.DB_PATH).resolve(strict=True)
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='latest_date'").fetchone()
    if not row or row[0] != td:
        raise ValueError('快照交易日与本机最新数据不一致')
    return {'version':version, 'files':files, 'hashes':hashes, 'trade_date':td}, payloads, generated

def parse_response(response):
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError('服务返回非JSON响应（HTTP %s, %s）' % (response.status_code, response.headers.get('Content-Type', 'unknown'))) from None
    if response.status_code != 200 or not isinstance(body, dict) or body.get('code') != 0:
        raise RuntimeError('服务拒绝请求（HTTP %s）' % response.status_code)
    return body.get('data')

def publish(snapshot, token):
    manifest, payloads, generated = verify_snapshot(snapshot)
    secret = dpapi(SECRET_PATH.read_bytes(), False).decode('ascii')
    headers = {'OAI-Sites-Authorization':'Bearer ' + token, 'Authorization':'Bearer ' + secret}
    session = requests.Session()
    # Honour the user's existing system proxy and TLS configuration. Never
    # disable certificate validation or follow redirects with access tokens.
    session.trust_env = True
    def get_current():
        response = session.get(ORIGIN + '/api/v1/meta', headers=headers, timeout=(15,60), allow_redirects=False)
        if response.status_code == 503:
            return None
        return parse_response(response)
    current = get_current()
    if current:
        if current.get('version') == manifest['version'] and current.get('latest_date') == manifest['trade_date']:
            print('该版本已发布并验证。'); return
        if current.get('latest_date', '') > manifest['trade_date']:
            raise RuntimeError('远端已有更新的交易日，拒绝回退')
        if current.get('latest_date') == manifest['trade_date']:
            stamp = current.get('generated_at')
            if not stamp or generated <= datetime.fromisoformat(stamp.replace('Z', '+00:00')):
                raise RuntimeError('远端已有同日更新版本，拒绝回退')
    for key in manifest['files']:
        response = session.put(ORIGIN + '/internal/snapshots/' + manifest['version'] + '/' + key + '.json',
                               data=payloads[key], headers={**headers, 'Content-Type':'application/json'},
                               timeout=(15,60), allow_redirects=False)
        data = parse_response(response)
        if not isinstance(data, dict) or data.get('sha256') != manifest['hashes'][key]:
            raise RuntimeError('服务确认的文件摘要不一致: ' + key)
    response = session.post(ORIGIN + '/internal/commit', json=manifest, headers=headers,
                            timeout=(15,120), allow_redirects=False)
    parse_response(response)
    confirmed = get_current()
    if not confirmed or confirmed.get('version') != manifest['version'] or confirmed.get('latest_date') != manifest['trade_date']:
        raise RuntimeError('发布后版本或交易日核验失败')
    print('发布成功，版本与交易日已核验。', flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--init-secret', action='store_true')
    parser.add_argument('snapshot', nargs='?', type=Path)
    args = parser.parse_args()
    if args.init_secret:
        if args.snapshot:
            raise ValueError('初始化时不接受快照目录')
        init_secret()
    else:
        if args.snapshot is None:
            raise ValueError('缺少快照目录')
        with lock():
            token = read_access()
            publish(args.snapshot, token)

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never print network exception strings, headers, or stdin contents.
        if isinstance(exc, (requests.RequestException, json.JSONDecodeError)):
            print('发布失败：网络或输入格式错误。', file=sys.stderr)
        elif isinstance(exc, (RuntimeError, ValueError, FileNotFoundError, FileExistsError)):
            print('发布失败：' + (str(exc) if type(exc) in (RuntimeError, ValueError) else '所需文件缺失或已存在'), file=sys.stderr)
        else:
            print('发布失败：本机处理错误。', file=sys.stderr)
        sys.exit(1)

"""Verify selected source commits and deliver them to the ETF project's private main."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

PROJECT = 'aHao030/etf-flow-miniapp'
HTTPS = f'https://github.com/{PROJECT}.git'
BRANCH = 'refs/heads/main'
SHA = re.compile(r'[0-9a-f]{40}')
SENSITIVE_DIRS = {'.git', '.local', '.openai', '.wrangler', '.snapshot-store', 'node_modules', 'dist'}
PRIVATE_PREFIXES = ('server/data/', 'server/work/', 'model/outputs/', 'model/cache/',
                    'data/samples/', 'data/tools/probe_results/', 'mini-programs/src/lib/snapshot/')
SECRET = re.compile(
    rb'(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{35,}|'
    rb'AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----|'
    rb'(?i:(?:service_token|access_token|api_key|client_secret|appsecret|password)["\x27]?\s*[:=]\s*'
    rb'["\x27][A-Za-z0-9_=+/.\-]{25,}["\x27]|authorization["\x27]?\s*[:=]\s*["\x27]?bearer\s+[\w.\-]{25,}))'
)


def git(root, *args, input_bytes=None):
    result = subprocess.run(['git', *args], cwd=root, input=input_bytes, capture_output=True)
    if result.returncode:
        # Git/credential helpers may include URLs in errors. Keep them out of logs.
        raise RuntimeError(f'Git {args[0]} 失败，退出码 {result.returncode}')
    return result.stdout


def verify_origin(root):
    for flags in (['--all'], ['--push', '--all']):
        urls = git(root, 'remote', 'get-url', *flags, 'origin').decode().splitlines()
        if urls not in ([HTTPS], [HTTPS.removesuffix('.git')], [f'git@github.com:{PROJECT}.git']):
            raise ValueError('origin 与项目指定的 GitHub 仓库不一致')
    env = dict(os.environ, GH_PROMPT_DISABLED='1')
    command = ['gh', 'api', '--hostname', 'github.com', 'repos/' + PROJECT]
    response = subprocess.run(command, env=env, capture_output=True, timeout=45)
    if response.returncode:
        # An existing HTTPS credential can authenticate this single gh read.
        # Keep the helper output in memory and the token out of arguments/logs.
        credential = subprocess.run(
            ['git', 'credential', 'fill'], cwd=root,
            env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never'),
            input=f'protocol=https\nhost=github.com\npath={PROJECT}.git\n\n'.encode(),
            capture_output=True, timeout=30)
        fields = dict(line.split(b'=', 1) for line in credential.stdout.splitlines() if b'=' in line)
        if credential.returncode or not fields.get(b'password'):
            raise RuntimeError('GitHub 账号认证不可用，未能核验仓库权限')
        env['GH_TOKEN'] = fields[b'password'].decode()
        response = subprocess.run(command, env=env, capture_output=True, timeout=45)
    if response.returncode:
        raise RuntimeError('GitHub API 未能核验仓库私有性')
    repo = json.loads(response.stdout)
    if repo.get('full_name') != PROJECT or repo.get('private') is not True:
        raise ValueError('GitHub 目标须为本项目的私有仓库')


def remote_head(root):
    rows = git(root, 'ls-remote', '--exit-code', 'origin', BRANCH).decode().splitlines()
    if len(rows) != 1:
        raise ValueError('无法确认 GitHub main')
    sha, ref = rows[0].split()
    if ref != BRANCH or not SHA.fullmatch(sha):
        raise ValueError('GitHub main 返回无效的提交号')
    return sha


def protected(name):
    parts = name.lower().split('/')
    lower = '/'.join(parts)
    if SENSITIVE_DIRS.intersection(parts) or lower.startswith(PRIVATE_PREFIXES):
        return True
    if lower.startswith('mini-programs/release/') and name != 'mini-programs/release/打开最新离线包.cmd':
        return True
    if lower.startswith('work/') and lower not in ('work/miniapp-validation/package.json', 'work/miniapp-validation/package-lock.json'):
        return True
    leaf = parts[-1]
    return (leaf in {'.npmrc', 'project.private.config.json', '.env'}
            or (leaf.startswith('.env.') and leaf != '.env.example')
            or re.search(r'\.(?:sqlite3?|db)(?:-.*)?$|\.(?:key|pem|p12|pfx|dpapi|tar|tgz|gz|zip|log)$', leaf))


def inspect_commit(root, sha, destination=None):
    entries = git(root, 'ls-tree', '-rz', sha).rstrip(b'\0').split(b'\0')
    files = []
    for entry in entries:
        meta, name = entry.split(b'\t', 1)
        mode, kind, oid = meta.split()
        name = name.decode('utf-8')
        parts = name.split('/')
        if (kind != b'blob' or mode not in (b'100644', b'100755') or protected(name)
                or {'..', '.', ''}.intersection(parts) or '\\' in name or ':' in name):
            raise ValueError(f'提交含非源码路径或链接：{name}')
        files.append((name, oid, mode))
    # Read the exact Git objects. Export substitutions and working-tree filters
    # must not hide content that would be transferred in a push.
    query = b''.join(oid + b'\n' for _, oid, _ in files)
    with io.BytesIO(git(root, 'cat-file', '--batch', input_bytes=query)) as objects:
        for name, oid, mode in files:
            object_id, kind, size = objects.readline().split()
            if object_id != oid or kind != b'blob':
                raise ValueError('提交树与源码对象不一致')
            content = objects.read(int(size))
            if len(content) != int(size) or objects.read(1) != b'\n':
                raise ValueError('源码对象不完整')
            if SECRET.search(content):
                raise ValueError(f'源文件可能包含凭据：{name}')
            if destination is not None:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if mode == b'100755':
                    target.chmod(0o755)


def run_tests(root, changed):
    def check(cwd, *command):
        subprocess.run(command, cwd=cwd, check=True)
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm') or 'npm'
    web = root/'web-trial'
    native = root/'mini-programs'
    tooling = root/'work/miniapp-validation'
    if any(p.startswith('web-trial/') for p in changed):
        check(web, npm, 'ci', '--prefer-offline', '--no-audit', '--no-fund')
        check(web, npm, 'run', 'build')
        check(web, npm, 'test')
    if any(p.startswith(('mini-programs/', 'work/miniapp-validation/')) for p in changed):
        check(native, 'node', 'build.mjs', '--mode', 'local')
        check(native, 'node', '--test', *sorted(str(f) for f in (native/'tests').glob('*.test.*')))
        check(tooling, npm, 'ci', '--prefer-offline', '--no-audit', '--no-fund')
        check(native, 'node', 'tests/host-compile.mjs')
        check(native, sys.executable, '-m', 'unittest', 'discover', '-s', 'tests')
    if any(p.startswith(('server/', 'model/', 'data/', 'tests/', 'tools/')) for p in changed):
        for suite in ('server/tests', 'tests'):
            check(root, sys.executable, '-m', 'unittest', 'discover', '-s', suite)
    if any(p.startswith('miniapp/') for p in changed):
        check(root, 'node', '--test', *sorted(str(f) for f in (root/'miniapp/tests').glob('*.test.js')))


def publish(root, commits):
    if not commits or any(not SHA.fullmatch(sha) for sha in commits) or len(set(commits)) != len(commits):
        raise ValueError('请列出每个待交付提交的完整 SHA，最后一个为目标版本')
    verify_origin(root)
    target = commits[-1]
    commit_url = f'https://github.com/{PROJECT}/commit/{target}'
    before = remote_head(root)
    if before == target:
        return {'status': 'already_published', 'commit': target, 'url': commit_url}
    git(root, 'fetch', '--no-tags', 'origin', BRANCH)
    git(root, 'merge-base', '--is-ancestor', before, target)
    unpublished = set(git(root, 'rev-list', target, '^'+before).decode().split())
    if unpublished != set(commits):
        raise ValueError('推送范围与明确指定的提交不一致，请核对 GitHub main 和待交付提交')
    changed = git(root, 'diff', '--name-only', '-z', before, target).decode().strip('\x00').split('\x00')
    with tempfile.TemporaryDirectory(prefix='etf-source-release-') as temp:
        for sha in commits:
            inspect_commit(root, sha, Path(temp) if sha == target else None)
        run_tests(Path(temp), changed)
    verify_origin(root)
    if remote_head(root) != before:
        raise RuntimeError('验证期间 GitHub main 已变化，请重新核对')
    git(root, '-c', 'push.followTags=false', 'push', 'origin', f'{target}:{BRANCH}')
    if remote_head(root) != target:
        raise RuntimeError('远端提交号核对失败，请重新查询 GitHub main')
    return {'status': 'published', 'commit': target, 'url': commit_url}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', nargs='+', required=True)
    try:
        print(json.dumps(publish(Path(__file__).resolve().parents[1], parser.parse_args().commit)))
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))

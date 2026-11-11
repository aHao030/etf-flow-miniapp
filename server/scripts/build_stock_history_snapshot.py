"""Build and validate a current-weight history snapshot without changing app.db.

Run only after the daily stock pipeline and history backfill have finished.
Build artifacts are retained under web-trial/.local/stock-history-builds; the
version is moved into snapshots only after all validations pass.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

SERVER=Path(__file__).resolve().parents[1]
PROJECT=SERVER.parent
LOCAL=PROJECT/'web-trial'/'.local'


def fingerprint(app, history, day):
    """Hash source DBs including WAL and every file used by this build."""
    digest=hashlib.sha256()
    versions=[sys.version,sqlite3.sqlite_version]
    for package in ('pandas','numpy','fastapi'):
        try: versions.append(package+'='+importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError: versions.append(package+'=missing')
    versions.append(subprocess.run(['node','--version'],check=True,text=True,capture_output=True).stdout.strip())
    digest.update(json.dumps([day,versions],ensure_ascii=True).encode())
    paths=[app,Path(str(app)+'-wal'),history,Path(str(history)+'-wal'),
           SERVER/'data'/'industry_sw_l1.csv',SERVER/'data'/'inputs'/'trading_calendar.csv',
           PROJECT/'web-trial'/'worker'/'index.js',PROJECT/'web-trial'/'tests'/'worker-snapshot-check.mjs']
    paths.extend(sorted(SERVER.glob('*.py')));paths.extend(sorted((SERVER/'scripts').glob('*.py')))
    for path in paths:
        digest.update(str(path).encode('utf-8'));digest.update(b'\0')
        if not path.is_file(): digest.update(b'missing\0');continue
        digest.update(b'present\0')
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''): digest.update(chunk)
        digest.update(b'\0')
    return digest.hexdigest()


def verified_cached_snapshot(path, day):
    try:
        manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
        files=manifest['files']
        if manifest['version']!=path.name or manifest['trade_date']!=day or not 1<=len(files)<=200 or len(files)!=len(set(files)): return False
        if set(files)!=set(manifest['hashes']): return False
        for key in files:
            if not re.fullmatch(r'(?:meta|catalog|ranks|industries|etfs|etf-details|stocks-\d{4})',key): return False
            data=(path/(key+'.json')).read_bytes()
            if len(data)>2*1024*1024 or hashlib.sha256(data).hexdigest()!=manifest['hashes'][key]: return False
        return True
    except (OSError,ValueError,KeyError,TypeError): return False


def cleanup_successful_build(build_root, work):
    """Remove only this build's three disposable SQLite files and sidecars."""
    root=Path(build_root).resolve(strict=True)
    directory=Path(work).resolve(strict=True)
    if directory.parent != root or not re.fullmatch(r'[0-9a-f]{32}',directory.name):
        raise ValueError('unsafe build directory')
    candidates=[]
    for name in ('factors.db','expanded.db','app-prototype.db'):
        for suffix in ('','-wal','-shm'):
            p=directory/(name+suffix)
            if p.is_symlink() or (p.exists() and (not p.is_file() or p.resolve().parent != directory)):
                raise ValueError('unsafe build artifact')
            candidates.append(p)
    removed=[]
    for p in candidates:
        if p.exists(): p.unlink();removed.append(p.name)
    return removed


def computed(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        return db.execute("SELECT value FROM meta WHERE key='computed_date'").fetchone()[0]


def run(cmd,env=None,cwd=SERVER):
    print('Running', Path(cmd[1]).name if len(cmd)>1 else cmd[0],flush=True)
    result=subprocess.run(cmd,cwd=cwd,env=env,text=True,capture_output=True)
    if result.returncode:
        print(result.stdout, result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError('build step failed: '+Path(cmd[1]).name)
    return result.stdout.strip()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-date',required=True)
    p.add_argument('--app-db',default=str(SERVER/'data'/'app.db'))
    p.add_argument('--history-db',default=str(SERVER/'data'/'market_history.db'))
    args=p.parse_args(); app=Path(args.app_db).resolve(strict=True);history=Path(args.history_db).resolve(strict=True)
    if computed(app)!=args.expected_date: raise ValueError('application computed date is not the requested date')
    source_hash=fingerprint(app,history,args.expected_date)
    cache=LOCAL/'stock-history-builds'/('fingerprint-'+source_hash+'.json')
    if cache.exists():
        try:
            cached=json.loads(cache.read_text(encoding='utf-8')); name=cached['snapshot']
            if re.fullmatch(r'[a-z0-9-]{1,64}',name):
                previous=LOCAL/'snapshots'/name
                if verified_cached_snapshot(previous,args.expected_date) and fingerprint(app,history,args.expected_date)==source_hash:
                    print('verified_snapshot='+str(previous));print('unchanged_source=true');return
        except (OSError,ValueError,KeyError,TypeError): pass
    work=LOCAL/'stock-history-builds'/uuid.uuid4().hex;work.mkdir(parents=True)
    factor=work/'factors.db'; expanded=work/'expanded.db'; prototype=work/'app-prototype.db';review=work/'review'
    py=sys.executable
    print(run([py,'scripts/stage_stock_history.py','--app-db',str(app),'--history-db',str(history),'--end',args.expected_date,'--output',str(factor)]),flush=True)
    print(run([py,'scripts/expand_stock_history_stage.py','--stage',str(factor),'--output',str(expanded)]),flush=True)
    print(run([py,'scripts/prototype_stock_history_db.py','--app',str(app),'--factors',str(factor),'--expanded',str(expanded),'--output',str(prototype)]),flush=True)
    env=os.environ.copy();env['ETF_FLOW_DB_PATH']=str(prototype);env['ETF_HISTORY_DB_PATH']=str(history)
    out=run([py,'scripts/export_web_snapshot.py',str(review),'--disk-snapshot'],env=env);print(out,flush=True)
    parts=out.split();candidate=Path(next(x.split('=',1)[1] for x in parts if x.startswith('snapshot=')))
    print(run(['node','web-trial/tests/worker-snapshot-check.mjs',str(candidate)],cwd=PROJECT),flush=True)
    if computed(app)!=args.expected_date: raise ValueError('application date changed during build')
    if fingerprint(app,history,args.expected_date)!=source_hash: raise ValueError('source changed during build')
    dest=LOCAL/'snapshots'/candidate.name;dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists(): raise FileExistsError(dest)
    candidate.rename(dest)
    temp=cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps({'snapshot':dest.name,'fingerprint':source_hash}),encoding='utf-8')
    os.replace(temp,cache)
    print('verified_snapshot='+str(dest),flush=True)
    print('build_artifacts='+str(work),flush=True)
    try:
        print('removed_build_databases='+','.join(cleanup_successful_build(LOCAL/'stock-history-builds',work)),flush=True)
    except (OSError,ValueError) as exc:
        print('cleanup_warning='+str(exc),file=sys.stderr,flush=True)


if __name__=='__main__':main()

"""Prepare, verify or restore private local release candidates for both native clients."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))
HOSTS = ('wechat', 'xiaohongshu')
SOURCE_GROUPS = ('history', 'broad', 'market')
SNAPSHOT_BLOCKS = ('calendar.js','funds.js','indices.js','meta.js','off.js','s300750.js','s600519.js')
NAMES = {'wechat':'ETF-WeChat.zip', 'xiaohongshu':'ETF-Xiaohongshu.zip'}
MODES = ('snapshot', 'local', 'live')
ID = re.compile(r'\d+\.\d+\.\d+-(snapshot|live|local)-(?:\d{8}|online)-[0-9a-f]{16}')
NODE = shutil.which('node') or 'node'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x', encoding='utf-8') as out:
            json.dump(obj, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def secure_child(parent, name):
    dest = parent / name
    if Path(name).name != name or dest.resolve().parent != parent.resolve() or dest.is_symlink():
        raise ValueError('文件路径不属于发行目录')
    return dest


@contextmanager
def lock(release):
    """The OS releases the advisory lock even if a build process exits abruptly."""
    release.mkdir(parents=True, exist_ok=True)
    with (release / '.prepare.lock').open('a+b') as f:
        if f.tell() == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('已有打包任务运行中') from None
        yield


def package_id(manifest):
    identity = {k:manifest[k] for k in ('appVersion','mode','dataDate','platforms')}
    checksum = digest(json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(',',':')).encode())
    day = (manifest['dataDate'] or 'online').replace('-','')
    return f"{manifest['appVersion']}-{manifest['mode']}-{day}-{checksum[:16]}"


def pointer_path(release, mode):
    if mode not in MODES:
        raise ValueError('数据模式无效')
    return release / ('latest-'+mode+'.json')


def get_pointer(release, mode):
    p = pointer_path(release, mode)
    return read(p) if p.exists() else {}


def member_json(blob, prefix):
    text = blob.decode('utf-8').strip()
    start = text.index(prefix) + len(prefix)
    return json.loads(text[start:].rstrip(';\n '))


def pack_host(dist, host, dest):
    folder = dist / host
    receipt = read(dist / ('.generated-'+host+'.json'))['files']
    archive = dest / NAMES[host]
    with zipfile.ZipFile(archive, 'x') as bundle:
        for name, expected in sorted(receipt.items()):
            source = (folder / name).resolve()
            if folder.resolve() not in source.parents or '\\' in name or name.startswith('/') or '..' in name.split('/'):
                raise ValueError('非法文件清单')
            data = source.read_bytes()
            if digest(data) != expected:
                raise ValueError('打包期间文件变化 '+name)
            entry = zipfile.ZipInfo(name, (1980,1,1,0,0,0))
            entry.create_system = 0
            bundle.writestr(entry,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    config = member_json((folder/'lib/config.js').read_bytes(),'module.exports = ')
    return {'archive':archive.name,'sha256':digest(archive.read_bytes()),'buildId':config['buildId'],
            'appId':read(folder/'project.config.json')['appid'],'bytes':archive.stat().st_size,'files':len(receipt)}


def verify(release, ident):
    if not ID.fullmatch(ident):
        raise ValueError('发行编号无效')
    directory = secure_child(release/'versions',ident)
    m = read(secure_child(directory,'manifest.json'))
    if m.get('schema')!=1 or m['id']!=ident or package_id(m)!=ident or set(m['platforms'])!=set(HOSTS):
        raise ValueError('发行清单与编号不一致')
    for host in HOSTS:
        item=m['platforms'][host]
        if item['archive']!=NAMES[host]:
            raise ValueError('归档名无效')
        archive=secure_child(directory,item['archive'])
        if digest(archive.read_bytes())!=item['sha256']:
            raise ValueError(host+' 压缩包摘要不一致')
        with zipfile.ZipFile(archive) as z:
            if z.testzip() or len(set(z.namelist()))!=item['files'] or len(z.namelist())!=item['files']:
                raise ValueError(host+' 压缩包不完整')
            cfg=member_json(z.read('lib/config.js'),'module.exports = ')
            if cfg['mode']!=m['mode'] or cfg['appVersion']!=m['appVersion'] or cfg['buildId']!=item['buildId']:
                raise ValueError(host+' 包内版本与清单不一致')
            if json.loads(z.read('project.config.json'))['appid']!=item['appId']:
                raise ValueError(host+' AppID 与清单不一致')
    return m


def select(release, ident, reason, automatic=False, before_commit=None):
    m=verify(release,ident)
    old=get_pointer(release,m['mode'])
    if before_commit:
        before_commit()
    if old.get('id')==ident:
        return 'unchanged'
    if automatic and old.get('hold')==ident:
        return 'held_after_restore'
    p={'id':ident,'mode':m['mode'],'previous':old.get('id'),'selectedAt':datetime.now(BEIJING).isoformat(),
       'reason':reason,'hold':old.get('id') if reason=='operator_restore' else None}
    write(pointer_path(release,m['mode']),p)
    return 'selected'


def run(args, *, env=None, log=None):
    proc = subprocess.run(args,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          encoding='utf-8',errors='replace',check=False)
    if log:
        log.write_text(proc.stdout,encoding='utf-8')
    if proc.returncode:
        detail=proc.stdout[-1400:].strip()
        raise RuntimeError(f'{Path(str(args[1])).name} 失败（{proc.returncode}） {detail} [{log}]')
    return proc.stdout


def prepare(args, logdir):
    trigger='etf-heartbeat-' if args.action=='after-close' else 'etf-mini-'
    prefix=trigger+datetime.now(BEIJING).strftime('%Y%m%d-%H%M')+'-mini-'+uuid.uuid4().hex[:8]+'-'
    def log(name):
        return logdir/(prefix+name)
    try:
        result=prepare_locked(args, log)
    except Exception as error:
        write(log('failure.json'),{'status':'failed','reason':str(error),'remotePublication':'not_performed'})
        raise RuntimeError(f"{error}\n详见 {log('failure.json')}") from error
    result['log']=str(log('summary.json'))
    write(log('summary.json'),result)
    return result


def capture_snapshot(exported):
    """Bind both packages to the export just verified, including every offline block."""
    store=ROOT/'.snapshot-store'
    selected=read(secure_child(store,'latest.json'))
    if selected.get('schema')!=1 or selected.get('id')!=exported.get('id') or not re.fullmatch(r'[0-9a-f]{64}',selected.get('id','')):
        raise ValueError('所选离线快照已变化，请重新准备发行包')
    version=secure_child(secure_child(store,'versions'),selected['id'])
    raw=secure_child(version,'manifest.json').read_bytes()
    if digest(raw)!=selected['id']:
        raise ValueError('离线快照清单摘要不一致')
    manifest=json.loads(raw)
    sources=manifest['summary']['sourceVersions']
    if manifest.get('schema')!=1 or manifest['targetEnd']!=exported['targetEnd'] or sources!=exported['sourceVersions'] or set(sources)!=set(SOURCE_GROUPS):
        raise ValueError('离线快照与导出结果的来源或日期不一致')
    view=read(ROOT.parent/'web-trial/.local/local-view.json')
    for group in SOURCE_GROUPS:
        if view.get(group+'_version')!=sources[group] or view.get(group+'_target')!=manifest['targetEnd']:
            raise ValueError('本机历史、宽基或市场数据选择已变化，请重新准备发行包')
    hashes=manifest['files']
    if set(hashes)!=set(SNAPSHOT_BLOCKS) or any(digest(secure_child(version,name).read_bytes())!=hashes[name] for name in SNAPSHOT_BLOCKS):
        raise ValueError('所选离线快照数据块摘要不一致')
    return hashes


def check_package_snapshot(expected, hosts):
    if any(hashes.get('lib/snapshot/'+name)!=checksum for hashes in hosts.values() for name,checksum in expected.items()):
        raise ValueError('候选包中的离线快照与本次导出不一致')


def effective_appid(args, key, current):
    explicit=getattr(args,key,None)
    if explicit is not None:
        return explicit
    host='wechat' if key=='wechat_appid' else 'xiaohongshu'
    saved=current['platforms'][host]['appId'] if current else None
    pattern=r'wx[0-9a-fA-F]{16}' if host=='wechat' else r'[A-Za-z0-9_-]{24}'
    if isinstance(saved,str) and re.fullmatch(pattern,saved):
        return saved
    project=ROOT/'dist'/host/'project.config.json'
    return read(project).get('appid') if project.is_file() else None


def prepare_locked(args, log):
    automatic=args.action=='after-close'
    mode='snapshot' if automatic else args.mode
    release=ROOT/'release'
    with lock(release):
        expected=None
        if mode=='snapshot':
            exported=json.loads(run([NODE,'scripts/export-snapshot.mjs'],log=log('snapshot.log')))
            expected=capture_snapshot(exported)
        def check_current_inputs():
            if expected is not None:
                capture_snapshot(exported)
        previous=get_pointer(release,mode)
        active=verify(release,previous['id']) if previous.get('id') else None
        if active and active['mode']!=mode:
            raise ValueError('当前发行指针的数据模式不一致')
        with tempfile.TemporaryDirectory(prefix='.candidate-',dir=release) as work:
            work=Path(work)
            options=['--mode',mode]
            for key in ('base_url','wechat_appid','xhs_appid'):
                value=getattr(args,key,None) if key=='base_url' else effective_appid(args,key,active)
                if value:
                    options.extend(['--'+key.replace('_','-'),value])
            comparable=previous.get('hold') or previous.get('id')
            if comparable:
                known=active if comparable==previous.get('id') else verify(release,comparable)
                if known['mode']!=mode:
                    raise ValueError('对照发行版本的数据模式不一致')
                hashes={}
                for host in HOSTS:
                    with zipfile.ZipFile(release/'versions'/comparable/known['platforms'][host]['archive']) as z:
                        hashes[host]={name:digest(z.read(name)) for name in z.namelist()}
                write(work/'known-files.json',hashes)
            built=json.loads(run([NODE,'scripts/stage-packages.mjs',str(work),*options],log=log('build.log')))
            if comparable and len(built)==2 and all(item.get('reused') for item in built):
                if expected is not None:
                    check_package_snapshot(expected,hashes)
                return {'status':select(release,comparable,'verified_package',automatic,check_current_inputs),'id':comparable,
                        'mode':mode,'dataDate':known['dataDate'],'reusedVerifiedArchives':True,'remotePublication':'not_performed'}
            artifact=work/'result'
            artifact.mkdir()
            dist=work/'dist'
            if expected is not None:
                receipts={h:read(dist/('.generated-'+h+'.json'))['files'] for h in HOSTS}
                check_package_snapshot(expected,receipts)
            platforms={h:pack_host(dist,h,artifact) for h in HOSTS}
            cfg=member_json((dist/'wechat/lib/config.js').read_bytes(),'module.exports = ')
            metadata=member_json((dist/'wechat/lib/snapshot/meta.js').read_bytes(),'module.exports=') if mode=='snapshot' else {}
            current={'schema':1,'appVersion':cfg['appVersion'],'mode':mode,'dataDate':metadata.get('end'),
                     'platforms':platforms,'sourceVersions':{k:metadata[k]['version'] for k in ('market','history','broad') if k in metadata}}
            ident=package_id(current)
            current['id']=ident
            versions=release/'versions'
            versions.mkdir(exist_ok=True)
            dest=secure_child(versions,ident)
            if not dest.exists():
                env=dict(os.environ,ETF_MINI_DIST=str(dist))
                tests=[str(t) for t in sorted((ROOT/'tests').glob('*.test.*'))]
                run([NODE,'--test',*tests],env=env,log=log('regression.log'))
                if mode=='snapshot':
                    run([NODE,'tests/compare-local.mjs','--pinned',str(artifact/'data-check.json')],env=env,log=log('data-check.log'))
                run([NODE,'tests/host-compile.mjs','--report',str(artifact/'compile.json')],env=env,log=log('compile.log'))
                current['preparedAt']=datetime.now(BEIJING).isoformat()
                current['verification']={'regression':'passed','hostCompilers':'passed','pinnedDataComparison':'passed' if mode=='snapshot' else 'not_applicable'}
                write(artifact/'manifest.json',current)
                check_current_inputs()
                os.rename(artifact,dest)
            action=select(release,ident,'verified_package',automatic,check_current_inputs)
            result={'status':action,'id':ident,'mode':mode,'dataDate':current['dataDate'],'archives':{h:str(dest/NAMES[h]) for h in HOSTS},
                    'remotePublication':'not_performed'}
            return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    build=sub.add_parser('prepare')
    build.add_argument('--mode',choices=MODES,default='snapshot')
    build.add_argument('--base-url')
    build.add_argument('--wechat-appid')
    build.add_argument('--xhs-appid')
    sub.add_parser('after-close')
    for action in ('verify','restore'):
        p=sub.add_parser(action);p.add_argument('id')
    sub.add_parser('status')
    p=sub.add_parser('record')
    p.add_argument('platform',choices=HOSTS);p.add_argument('id')
    p.add_argument('state',choices=['trial','in_review','rejected','approved','published','rolled_back'])
    p.add_argument('--note',default='')
    args=parser.parse_args()
    release=ROOT/'release'
    if args.action in ('record','status'):
        payload=vars(args)
        completed=subprocess.run([NODE,'scripts/platform-status.mjs'],cwd=ROOT,input=json.dumps(payload),capture_output=True,text=True,encoding='utf-8',errors='replace')
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip())
        ledger=json.loads(completed.stdout)
        if args.action=='status':
            return {'localCandidates':{m:get_pointer(release,m) for m in MODES},'platformRecords':ledger['platforms']}
        return ledger
    if args.action=='verify':
        return verify(release,args.id)
    if args.action=='restore':
        with lock(release):
            return {'status':select(release,args.id,'operator_restore'),'id':args.id,'scope':'local_candidate_only'}
    if args.action=='after-close':
        server=ROOT.parent/'server'
        sys.path.insert(0,str(server/'scripts'))
        from collect_stock_prices import scheduled_gate
        target=read(ROOT.parent/'web-trial/.local/local-view.json')['market_target']
        skip=scheduled_gate(target)
        if skip:
            return {'status':'skipped','reason':skip}
    logdir=Path.home()/'Desktop'/'ETF更新日志'/datetime.now(BEIJING).date().isoformat()
    logdir.mkdir(parents=True,exist_ok=True)
    return prepare(args,logdir)


if __name__=='__main__':
    try:
        print(json.dumps(main(),ensure_ascii=False,indent=2))
    except Exception as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)

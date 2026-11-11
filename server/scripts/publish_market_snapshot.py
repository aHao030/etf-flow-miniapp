"""Publish a validated market index archive without replacing the latest stock view."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import requests
from zoneinfo import ZoneInfo
from publish_web_snapshot import ORIGIN, SECRET_PATH, dpapi, lock, read_access, parse_response, VERSION, HASH, MAX_BYTES

KEY = re.compile(r'^(?:meta|calendar|catalog|funds|ranks|industries|weights-\d{4}|factors-\d{4})$')

def verify(path):
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    files, hashes = manifest.get('files'), manifest.get('hashes')
    if not VERSION.fullmatch(manifest.get('version','')) or not isinstance(files,list) or not 3<=len(files)<=500 or len(set(files))!=len(files) or not {'meta','calendar','catalog','funds','ranks','industries'}.issubset(files) or not all(isinstance(k,str) and KEY.fullmatch(k) for k in files):
        raise ValueError('全市场文件清单无效')
    if not isinstance(hashes,dict) or set(hashes)!=set(files) or not all(isinstance(v,str) and HASH.fullmatch(v) for v in hashes.values()):
        raise ValueError('全市场摘要清单无效')
    for k in files:
        raw=(path/(k+'.json')).read_bytes()
        if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=hashes[k]:
            raise ValueError('全市场文件被修改或过大')
    meta=json.loads((path/'meta.json').read_text(encoding='utf-8'))
    if any(manifest.get(k)!=meta.get(k) for k in ['version','target_start','target_end','generated_at']):
        raise ValueError('全市场元数据不一致')
    if not ('1990-01-01'<=manifest['target_start']<=manifest['target_end']) or date.fromisoformat(manifest['target_end'])>datetime.now(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('全市场日期无效')
    generated=datetime.fromisoformat(meta['generated_at'].replace('Z','+00:00'))
    if generated.tzinfo is None or generated>datetime.now(timezone.utc):
        raise ValueError('全市场生成时间无效')
    return manifest

def publish(path,token):
    manifest=verify(path)
    secret=dpapi(SECRET_PATH.read_bytes(),False).decode('ascii')
    headers={'OAI-Sites-Authorization':'Bearer '+token,'Authorization':'Bearer '+secret}
    session=requests.Session(); session.trust_env=True
    def current():
        res=session.get(ORIGIN+'/api/v1/market/meta',headers=headers,timeout=(15,60),allow_redirects=False)
        return None if res.status_code==503 else parse_response(res)
    old=current()
    local_meta=json.loads((path/'meta.json').read_text(encoding='utf-8'))
    if old and old.get('data_revision') and old['data_revision']==local_meta.get('data_revision'):
        print('全市场数据内容未变化，保留线上版本。'); return
    if old and old.get('version')==manifest['version']:
        print('全市场版本已上线并核验。'); return
    if old and (old['target_end']>manifest['target_end'] or old['generated_at']>=manifest['generated_at']):
        raise ValueError('拒绝覆盖较新的全市场版本')
    for i,key in enumerate(manifest['files'],1):
        raw=(path/(key+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=manifest['hashes'][key]:
            raise ValueError('发布期间全市场文件被修改')
        # PUT is idempotent until commit; retry transient transport errors only.
        for attempt in range(3):
            try:
                res=session.put(ORIGIN+'/internal/market/'+manifest['version']+'/'+key+'.json',data=raw,headers={**headers,'Content-Type':'application/json'},timeout=(15,90),allow_redirects=False)
                if res.status_code in (429,502,503,504) and attempt<2:
                    time.sleep(2*(attempt+1)); continue
                result=parse_response(res)
                if result.get('sha256')!=manifest['hashes'][key]: raise ValueError('上传摘要不一致')
                break
            except requests.RequestException:
                if attempt==2: raise
                time.sleep(2*(attempt+1))
        if i%10==0 or i==len(manifest['files']): print('全市场分片已上传 %d/%d'%(i,len(manifest['files'])),flush=True)
    parse_response(session.post(ORIGIN+'/internal/market/commit',json=manifest,headers=headers,timeout=(15,180),allow_redirects=False))
    confirmed=current()
    if not confirmed or confirmed.get('version')!=manifest['version']: raise ValueError('全市场发布后核验失败')
    print(json.dumps({'published':True,'version':confirmed['version'],'total_funds':confirmed['total_funds']},ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('snapshot',type=Path); args=parser.parse_args()
    try:
        with lock(): publish(args.snapshot,read_access())
    except Exception as exc:
        print('全市场发布失败：'+(str(exc) if type(exc) in (ValueError,RuntimeError) else '网络或本机处理异常'),file=sys.stderr);sys.exit(1)

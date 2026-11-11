import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

MODULE=Path(__file__).resolve().parents[1]/'scripts/release.py'
spec=importlib.util.spec_from_file_location('mini_release',MODULE)
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class ReleaseFlow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'mini-programs'
        self.root.mkdir()
        self.store=self.root/'release'
        self.logs=self.root/'logs'
        self.logs.mkdir()
        self.marker='initial'
        self.fail_compile=False
        self.after_stage=None
        self.after_compile=None
        self.change_block=False
        self.compilations=0
        self.opts=SimpleNamespace(action='prepare',mode='snapshot')
        self.enterContext(patch.object(r,'ROOT',self.root))
        self.enterContext(patch.object(r,'run',side_effect=self.fake))
        self.install_snapshot()

    def install_snapshot(self,marker='first',day='2026-11-10'):
        self.sources={k:'20261110074529-'+r.digest((k+day).encode())[:32] for k in r.SOURCE_GROUPS}
        meta={'end':day,**{k:{'version':v} for k,v in self.sources.items()}}
        self.blocks={n:('module.exports='+json.dumps(meta if n=='meta.js' else [n,marker])+';').encode() for n in r.SNAPSHOT_BLOCKS}
        manifest={'schema':1,'targetEnd':day,'files':{n:r.digest(v) for n,v in self.blocks.items()},'summary':{'sourceVersions':self.sources}}
        raw=json.dumps(manifest).encode()
        ident=r.digest(raw)
        store=self.root/'.snapshot-store'
        directory=store/'versions'/ident
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'manifest.json').write_bytes(raw)
        for name,body in self.blocks.items():(directory/name).write_bytes(body)
        r.write(store/'latest.json',{'schema':1,'id':ident})
        self.exported={'status':'unchanged','id':ident,'targetEnd':day,'sourceVersions':self.sources}
        self.local=self.root.parent/'web-trial/.local/local-view.json'
        r.write(self.local,{**{k+'_target':day for k in self.sources},**{k+'_version':v for k,v in self.sources.items()}})

    def fake(self,cmd,*,log=None,env=None):
        if cmd[1]=='scripts/export-snapshot.mjs':
            return json.dumps(self.exported)
        if cmd[1]=='scripts/stage-packages.mjs':
            scratch=Path(cmd[2])
            byhost={}
            for host in r.HOSTS:
                build=hashlib.sha256((self.marker+host).encode()).hexdigest()[:12]
                cfg={'appVersion':'1.1.0','buildId':build,'mode':'snapshot','baseUrl':''}
                key='--wechat-appid' if host=='wechat' else '--xhs-appid'
                appid=cmd[cmd.index(key)+1] if key in cmd else ('touristappid' if host=='wechat' else '')
                files={'lib/config.js':"module.exports = "+json.dumps(cfg)+";",'project.config.json':json.dumps({'appid':appid})}
                files.update({'lib/snapshot/'+n:body.decode() for n,body in self.blocks.items()})
                if self.change_block:files['lib/snapshot/funds.js']='module.exports=[];'
                byhost[host]={n:r.digest(s.encode()) for n,s in files.items()}
                for name,data in files.items():
                    dest=scratch/'dist'/host/name
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    dest.write_bytes(data.encode())
                r.write(scratch/'dist'/('.generated-'+host+'.json'),{'files':byhost[host]})
            known=scratch/'known-files.json'
            reuse=known.exists() and r.read(known)==byhost
            if self.after_stage:self.after_stage()
            return json.dumps([{'reused':reuse} for _ in r.HOSTS])
        if cmd[1]=='tests/host-compile.mjs':
            self.compilations+=1
            if self.fail_compile:
                raise RuntimeError('simulated second host compile failure')
            if self.after_compile:self.after_compile()
        return 'ok'

    def create(self):
        return r.prepare(self.opts,self.logs)['id']

    def test_pair_dedup_failure_restore_and_corrupt_archive(self):
        old=self.create()
        self.assertEqual(self.create(),old)
        self.assertEqual(self.compilations,1)
        first=(self.store/'latest-snapshot.json').read_bytes()
        self.marker='updated'
        self.fail_compile=True
        with self.assertRaisesRegex(RuntimeError,'second host'):
            self.create()
        self.assertEqual((self.store/'latest-snapshot.json').read_bytes(),first)
        self.assertEqual(len(list((self.store/'versions').iterdir())),1)
        self.fail_compile=False
        new=self.create()
        self.assertNotEqual(old,new)
        r.select(self.store,old,'operator_restore')
        self.opts.action='after-close'
        result=r.prepare(self.opts,self.logs)
        self.assertEqual(result['status'],'held_after_restore')
        self.assertEqual(r.get_pointer(self.store,'snapshot')['id'],old)
        archive=self.store/'versions'/new/r.NAMES['xiaohongshu']
        archive.write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError,'摘要'):
            r.select(self.store,new,'operator_restore')
        self.assertEqual(r.get_pointer(self.store,'snapshot')['id'],old)
        self.assertTrue(any(self.logs.glob('*failure.json')))

    def test_concurrent_preparation_cannot_start(self):
        with r.lock(self.store):
            with self.assertRaisesRegex(RuntimeError,'已有打包任务'):
                with r.lock(self.store):
                    self.fail('double lock acquired')

    def test_explicit_appids_verified_current_and_ide_fallback(self):
        guest=self.create()
        original={'wechat':'wx1234567890abcdef','xiaohongshu':'aabbccddeeff001122334455'}
        def keys(ident):return {k:r.verify(self.store,ident)['platforms'][k]['appId'] for k in r.HOSTS}
        for host,appid in original.items():r.write(self.root/'dist'/host/'project.config.json',{'appid':appid})
        configured=self.create()
        self.assertNotEqual(guest,configured);self.assertEqual(keys(configured),original)
        for host in r.HOSTS:r.write(self.root/'dist'/host/'project.config.json',{'appid':'touristappid' if host=='wechat' else ''})
        self.install_snapshot('next','2026-11-11')
        self.opts.action='after-close'
        next_day=self.create();self.assertEqual(keys(next_day),original)
        self.opts=SimpleNamespace(action='prepare',mode='snapshot',wechat_appid='wx1111111111111111')
        override=self.create();self.assertEqual(keys(override),dict(original,wechat=self.opts.wechat_appid))
        r.select(self.store,next_day,'operator_restore')
        self.opts=SimpleNamespace(action='after-close')
        self.marker='new code'
        self.assertEqual(keys(self.create()),original,'AppID must come from the selected release, not the held previous candidate')

    def test_placeholder_id_in_previous_importable_project_yields_to_real_ide_id(self):
        self.opts.xhs_appid='placeholder'
        placeholder=self.create()
        real='abcdef0123456789abcdef01'
        r.write(self.root/'dist/xiaohongshu/project.config.json',{'appid':real})
        self.opts=SimpleNamespace(action='after-close')
        current=self.create()
        self.assertNotEqual(current,placeholder)
        self.assertEqual(r.verify(self.store,current)['platforms']['xiaohongshu']['appId'],real)

    def test_local_sources_switch_during_compile_keeps_previous_candidate(self):
        old=self.create();before=(self.store/'latest-snapshot.json').read_bytes();self.marker='updated'
        def change_market():
            view=r.read(self.local);view['market_version']='20261110074529-'+'9'*32;r.write(self.local,view)
        self.after_compile=change_market
        with self.assertRaisesRegex(RuntimeError,'数据选择已变化'):self.create()
        self.assertEqual((self.store/'latest-snapshot.json').read_bytes(),before)
        self.assertEqual([p.name for p in (self.store/'versions').iterdir()],[old])

    def test_offline_switch_in_reuse_with_identical_market_sources_is_refused(self):
        old=self.create();before=(self.store/'latest-snapshot.json').read_bytes();self.after_stage=lambda:self.install_snapshot('different raw prices')
        with self.assertRaisesRegex(RuntimeError,'所选离线快照已变化'):self.create()
        self.assertEqual((self.store/'latest-snapshot.json').read_bytes(),before);self.assertEqual(self.compilations,1)
        self.assertEqual([p.name for p in (self.store/'versions').iterdir()],[old])

    def test_candidate_bytes_must_match_pinned_snapshot_even_when_both_pointers_still_match(self):
        old=self.create();self.change_block=True
        with self.assertRaisesRegex(RuntimeError,'候选包中的离线快照'):self.create()
        self.assertEqual(r.get_pointer(self.store,'snapshot')['id'],old)


if __name__=='__main__':unittest.main()

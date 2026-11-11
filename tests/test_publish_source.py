import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'publish_source.py'
spec = importlib.util.spec_from_file_location('publish_source', SOURCE)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class SourceDelivery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'checkout'
        self.remote = Path(temporary.name)/'remote.git'
        self.root.mkdir()
        self.git('init', '--bare', str(self.remote))
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.test')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.initial = self.commit('README.md', 'first version')
        self.git('push', 'origin', 'main')
        self.enterContext(patch.object(release, 'verify_origin'))
        self.checked = self.enterContext(patch.object(release, 'run_tests'))

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE).decode().strip()

    def commit(self, name, content):
        path = self.root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content, encoding='utf-8')
        self.git('add', '--', name)
        self.git('commit', '-m', 'test change')
        return self.git('rev-parse', 'HEAD')

    def test_explicit_range_isolated_from_unrelated_stage_and_verified(self):
        a = self.commit('part.py', 'version=1')
        b = self.commit('part.py', 'version=2')
        (self.root/'part.py').write_text('uncommitted')
        self.git('add', 'part.py')
        def check(source, changed):
            self.assertEqual(changed, ['part.py'])
            self.assertEqual((source/'part.py').read_text(), 'version=2')
        self.checked.side_effect = check
        with self.assertRaisesRegex(ValueError, '范围'):
            release.publish(self.root, [b])
        self.assertEqual(release.publish(self.root, [a,b])['commit'], b)
        self.assertEqual(release.publish(self.root, [a,b])['status'], 'already_published')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'part.py')
        self.checked.assert_called_once()

    def test_rejected_checks_keep_previous_main(self):
        a = self.commit('tool.py', 'version=1')
        self.checked.side_effect = RuntimeError('failed checks')
        with self.assertRaisesRegex(RuntimeError, 'failed checks'):
            release.publish(self.root, [a])
        self.assertEqual(release.remote_head(self.root), self.initial)

    def test_intermediate_secret_and_private_paths_block_whole_release(self):
        a = self.commit('removed.txt', 'ghp_' + 'A'*40)
        self.git('rm', 'removed.txt')
        self.git('commit', '-m', 'remove')
        b = self.git('rev-parse','HEAD')
        with self.assertRaisesRegex(ValueError, '凭据'):
            release.publish(self.root, [a,b])
        self.git('reset','--hard',self.initial)
        c = self.commit('server/data/private.txt', 'sample')
        with self.assertRaisesRegex(ValueError, '非源码'):
            release.publish(self.root,[c])
        self.assertEqual(release.remote_head(self.root),self.initial)
        self.checked.assert_not_called()

    def test_concurrent_update_is_not_overwritten(self):
        a = self.commit('tool.py','reviewed change')
        self.git('switch','-c','other',self.initial)
        b = self.commit('other.py','independent change')
        self.git('switch','main')
        self.checked.side_effect = lambda *_: self.git('push','origin',b+':refs/heads/main')
        with self.assertRaisesRegex(RuntimeError, '已变化'):
            release.publish(self.root,[a])
        self.assertEqual(release.remote_head(self.root),b)
        with self.assertRaises(RuntimeError):
            release.publish(self.root,[a])

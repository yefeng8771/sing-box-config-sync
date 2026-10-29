import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sync


class SyncTests(unittest.TestCase):
    def test_source_failure_returns_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'sources.json'
            meta = root / '.sync-meta.json'
            config.write_text(json.dumps({'sources': [{'name': 'example', 'type': 'github'}]}))
            meta.write_text(json.dumps({'example': {'commit': 'last-good'}}))
            with patch.object(sync, 'SOURCES_FILE', config), patch.object(sync, 'META_FILE', meta), patch.object(sync, 'sync_github', side_effect=RuntimeError('simulated fetch error')):
                self.assertEqual(sync.main(), 1)
                self.assertEqual(json.loads(meta.read_text())['example']['commit'], 'last-good')

    def test_snapshot_changes_and_failure_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = {'name': 'example', 'type': 'github', 'dest':'sources/example'}
            d = root/'sources/example'; d.mkdir(parents=True)
            (d/'old').write_text('old')
            config = root/'sources.json'; config.write_text(json.dumps({'sources':[src]}))
            meta = root/'.sync-meta.json'; meta.write_text('{}')
            summary = root/'summary'
            with patch.object(sync, 'ROOT', root), patch.object(sync, 'SOURCES_FILE', config), patch.object(sync, 'META_FILE', meta), patch.object(sync, 'sync_github', side_effect=RuntimeError('network down')), patch.dict(os.environ, {'GITHUB_STEP_SUMMARY':str(summary)}):
                old = sync.file_snapshot(src)
                (d/'new').write_text('new'); (d/'old').write_text('changed')
                self.assertEqual(sync.changes(old, sync.file_snapshot(src)), (1,1,0))
                self.assertEqual(sync.main(), 1)
                self.assertIn('network down', summary.read_text())
                self.assertEqual((d/'old').read_text(), 'changed')

    def test_discovered_versions_mirror_and_missing_directory_preserves_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'sources/pk-box'
            (target / 'legacy').mkdir(parents=True)
            (target / 'legacy/old.txt').write_text('keep')
            (target / '1.15.0-alpha.xxx').mkdir()
            (target / '1.15.0-alpha.xxx/old.json').write_text('old')
            src = {'name': 'pk', 'type': 'github', 'repo': 'x/y', 'branch': 'main',
                   'dest': 'sources/pk-box', 'discover_version_dirs': True}
            state = {'missing': False}
            def run(cmd, cwd=None):
                if cmd[1] == 'clone':
                    work = Path(cmd[-1]); work.mkdir()
                    for version in ('1.15.0-alpha.xxx', '1.16.0-alpha.1'):
                        if state['missing'] and version.startswith('1.16'):
                            continue
                        (work / version).mkdir()
                        (work / version / 'new.json').write_text(version)
                    return ''
                if 'ls-tree' in cmd: return '1.15.0-alpha.xxx\n1.16.0-alpha.1\ndocs\n'
                if 'rev-parse' in cmd: return 'abc123\n'
                return ''
            with patch.object(sync, 'ROOT', root), patch.object(sync, '_run', side_effect=run):
                info = sync.sync_github(src, '')
                self.assertEqual(info['version_directories'], ['1.15.0-alpha.xxx', '1.16.0-alpha.1'])
                self.assertEqual((target/'legacy/old.txt').read_text(), 'keep')
                self.assertTrue((target/'1.16.0-alpha.1/new.json').is_file())
                self.assertFalse((target/'1.15.0-alpha.xxx/old.json').exists())
                before = sync.file_snapshot(src)
                state['missing'] = True
                with self.assertRaises(RuntimeError): sync.sync_github(src, '')
                self.assertEqual(before, sync.file_snapshot(src))

    def test_config_includes_latest_pk(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'sources.json').read_text())
        sources = {entry['name']: entry for entry in config['sources']}
        self.assertTrue(sources['pk-box']['discover_version_dirs'])
        self.assertEqual(sync.discover_version_dirs(['beta','1.15.0-alpha.xxx','1.16.0-alpha.1','docs','1.16x']), ['1.15.0-alpha.xxx','1.16.0-alpha.1','beta'])

if __name__ == '__main__':
    unittest.main()

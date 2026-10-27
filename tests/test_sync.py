import json
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

    def test_config_includes_latest_pk_and_excludes_boxproxy(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'sources.json').read_text())
        sources = {entry['name']: entry for entry in config['sources']}
        self.assertNotIn('boxproxy-box', sources)
        self.assertIn('1.15.0-alpha.xxx', [entry['path'] for entry in sources['pk-box']['paths']])

if __name__ == '__main__':
    unittest.main()

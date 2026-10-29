import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import normalize

class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src, self.dst = self.root / 'sources', self.root / 'normalized'
        self.src.mkdir()
        (self.root / 'sources.json').write_text(json.dumps({'sources':[{'name':'repcz-tool'}, {'name':'pk-box'}]}))
        self.patches = [patch.object(normalize, 'ROOT', self.root), patch.object(normalize, 'SRC', self.src), patch.object(normalize, 'DST', self.dst)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
    def put(self, rel, text):
        p = self.src / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    def test_success_preserves_raw_templates_and_legacy(self):
        self.put('repcz-tool/a.jsonc', '{"url":"https://x/y", "literal":",}", /*hi*/ "n":null,}')
        self.put('pk-box/template.json', '{"server_port":端口}')
        self.put('legacy-source/old.json', '{')
        (self.dst / 'legacy-source').mkdir(parents=True); (self.dst / 'legacy-source/old.json').write_text('legacy')
        self.assertEqual(normalize.main(), 0)
        self.assertEqual(json.loads((self.dst/'repcz-tool/a.json').read_text()), {'url':'https://x/y','literal':',}'})
        self.assertEqual((self.dst/'legacy-source/old.json').read_text(), 'legacy')
        self.assertFalse((self.dst/'pk-box/template.json').exists())
        self.assertEqual((self.src/'pk-box/template.json').read_text(), '{"server_port":端口}')
    def test_invalid_or_collision_leaves_previous_unchanged(self):
        (self.dst/'repcz-tool').mkdir(parents=True); (self.dst/'repcz-tool/a.json').write_text('previous')
        self.put('repcz-tool/a.json', '{bad}')
        self.assertEqual(normalize.main(),1)
        self.assertEqual((self.dst/'repcz-tool/a.json').read_text(), 'previous')
        self.put('repcz-tool/a.json', '{}'); self.put('repcz-tool/a.jsonc', '{}')
        self.assertEqual(normalize.main(),1)
        self.assertEqual((self.dst/'repcz-tool/a.json').read_text(), 'previous')
    def test_stale_removed_only_for_active_normalized_source(self):
        self.put('repcz-tool/new.json', '{}')
        (self.dst/'repcz-tool').mkdir(parents=True); (self.dst/'repcz-tool/stale.json').write_text('{}')
        (self.dst/'pk-box').mkdir(); (self.dst/'pk-box/legacy.json').write_text('{}')
        self.assertEqual(normalize.main(),0)
        self.assertFalse((self.dst/'repcz-tool/stale.json').exists())
        self.assertTrue((self.dst/'pk-box/legacy.json').exists())
    def test_unterminated_comment_and_no_token_join(self):
        with self.assertRaises(ValueError): normalize.strip_comments('{/* x')
        self.assertEqual(normalize.strip_comments('1/*x*/2'),'1 2')
    def test_missing_source_fails(self):
        self.src.rmdir(); self.assertEqual(normalize.main(),1)

if __name__ == '__main__': unittest.main()

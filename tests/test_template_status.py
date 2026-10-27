import tempfile
import unittest
from pathlib import Path
import template_status

class TemplateStatusTests(unittest.TestCase):
    def test_syntax_is_not_runtime_validation(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/'good.jsonc').write_text('{"x":1,}')
            (root/'template.json').write_text('{"port":端口}')
            (root/'README.md').write_text('docs')
            self.assertEqual(template_status.classify(root), {
                'good.jsonc':'jsonc-syntax-ok', 'template.json':'template-or-invalid-json', 'README.md':'not-json'})

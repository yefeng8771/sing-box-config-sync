import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class BuildTests(unittest.TestCase):
    def test_fake_provider_ignores_existing_real_provider_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # Deliberately invalid YAML: reading this file would fail.
            provider_file = tmp / 'providers.yaml'
            provider_file.write_text('should-not-be-read: [')
            for device in ('router', 'phone'):
                output = tmp / (device + '.json')
                result = subprocess.run([sys.executable, str(ROOT / 'build/build.py'),
                    '--device', device, '--providers', str(provider_file),
                    '--fake-providers', '--out', str(output)],
                    text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                data = json.loads(output.read_text())
                self.assertEqual(data['providers'][0]['tag'], 'ci-fake')
                self.assertNotIn('should-not-be-read', output.read_text())

if __name__ == '__main__':
    unittest.main()

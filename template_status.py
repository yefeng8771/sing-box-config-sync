#!/usr/bin/env python3
"""Classify retained pk-box files as raw templates; syntax is not runtime validity."""
import json
from pathlib import Path
import normalize

ROOT = Path(__file__).resolve().parent

def classify(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        name = str(path.relative_to(root))
        status = 'not-json'
        if path.suffix.lower() in normalize.JSON_EXTS:
            try:
                json.loads(normalize.strip_trailing_commas(normalize.strip_comments(path.read_text(encoding='utf-8'))))
                status = 'jsonc-syntax-ok'
            except (UnicodeError, ValueError):
                status = 'template-or-invalid-json'
        result[name] = status
    return result

def main():
    folder = ROOT / 'sources/pk-box'
    if not folder.is_dir():
        raise FileNotFoundError('pk-box templates absent')
    data = {'meaning': 'All pk-box files are raw upstream templates. Syntax check accepts JSONC comments and trailing commas; it is not sing-box compatibility or usable configuration validation.', 'files': classify(folder)}
    (ROOT / 'template-status.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('pk-box raw template classification:', len(data['files']), 'files')
if __name__ == '__main__':
    main()

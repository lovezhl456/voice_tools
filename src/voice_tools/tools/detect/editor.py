"""One form editor for offline reports and the local configuration service."""
import html
import json
from pathlib import Path

from voice_tools import __version__
from .definition import load, validate
from .metrics import catalog
from .schema import schema

ROOT = Path(__file__).parent


def templates():
    return [load(ROOT / 'resources' / (name + '.json')) for name in ('ai-silence', 'low-volume')]


def document(definitions=(), session=None, back_link=None):
    data = {'version': __version__, 'catalog': catalog(), 'schema': schema(), 'templates': templates(),
            'definitions': [validate(config) for config in definitions], 'session': session}
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = (ROOT / 'editor.html').read_text(encoding='utf-8')
    page = page.replace('__STYLE__', (ROOT / 'editor.css').read_text(encoding='utf-8'))
    page = page.replace('__CONTRACT__', (ROOT / 'editor-contract.js').read_text(encoding='utf-8'))
    page = page.replace('__SCRIPT__', (ROOT / 'editor.js').read_text(encoding='utf-8'))
    link = '<a href="' + html.escape(back_link, quote=True) + '">返回录音复核</a>' if back_link else ''
    return page.replace('__BACK__', link).replace('__DATA__', encoded)


def render(path, definitions=(), back_link=None):
    Path(path).write_text(document(definitions, back_link=back_link), encoding='utf-8')

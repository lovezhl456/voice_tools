"""Self-contained call-level review using maintained local assets."""
import json
from pathlib import Path

from .assessment_review import FIELDS, validate_record

ROOT = Path(__file__).parent


def render(path, records, summary, hide_paths=False, back_link=None):
    visible = []
    for record in records:
        validate_record(record)
        row = {key: record[key] for key in ('assessment_id', 'audio_sha256', 'result', 'error', 'playback_sources', 'waveform') if key in record}
        row['input'] = record['input'].replace('\\', '/').rsplit('/', 1)[-1] if hide_paths else record['input']
        if hide_paths:
            original = record['input']
            def scrub(value):
                if isinstance(value, str):
                    return value.replace(original, row['input'])
                if isinstance(value, list):
                    return [scrub(item) for item in value]
                if isinstance(value, dict):
                    return {key: scrub(item) for key, item in value.items()}
                return value
            row = scrub(row)
        visible.append(row)
    data = json.dumps({'records': visible, 'summary': summary, 'fields': FIELDS}, ensure_ascii=False, allow_nan=False)
    data = data.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = (ROOT/'assessment.html').read_text(encoding='utf-8')
    from voice_tools.core.review.page import ASSETS, embed_playback_assets
    template = embed_playback_assets(template).replace('当前机会', '当前范围').replace('标注窗口 · 固定', '建议复核范围 · 固定')
    page = template.replace('__STYLE__', (ROOT/'assessment.css').read_text())
    page = page.replace('__FILTER__', (ASSETS/'file-filter.js').read_text())
    page = page.replace('__SCRIPT__', (ROOT/'assessment.js').read_text()).replace('__DATA__', data)
    if back_link:
        import html
        page = page.replace('href="summary.csv"', 'href="' + html.escape(back_link, quote=True) + '"')
        page = page.replace('下载整通汇总', '返回任务报告')
    from voice_tools.tools.detect.editor import render as render_editor
    render_editor(Path(path).with_name('config.html'), back_link=Path(path).name)
    page = page.replace('<h1>', '<p><a href="config.html"><strong>定义指标与标签</strong></a></p><h1>', 1)
    Path(path).write_text(page, encoding='utf-8')

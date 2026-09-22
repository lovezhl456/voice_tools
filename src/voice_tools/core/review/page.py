"""Local review shell and bundled playback assets; business rules stay in tools."""
import json
from pathlib import Path
import re

ASSETS = Path(__file__).parent


def embed_playback_assets(template):
    """Use the same offline waveform and player in call and opportunity reviews."""
    vendor = ASSETS / "vendor"
    bundles = "\n".join((vendor / name).read_text(encoding="utf-8") for name in
                        ("wavesurfer.min.js", "regions.min.js", "timeline.min.js"))
    bundles = ("/* WaveSurfer.js 7.12.12 — " + (vendor / "wavesurfer.LICENSE.txt").read_text() + " */\n" + bundles).replace("</script", "<\\/script")
    template = template.replace("__WAVEFORM_VENDOR__", bundles)
    for marker, name in {
        "__WAVEFORM_STYLE__": "waveform-panel.css", "__WAVEFORM_PANEL__": "waveform-panel.html",
        "__PLAYBACK_CONTROLS__": "playback-controls.html", "__WAVEFORM_SCRIPT__": "waveform.js",
        "__PLAYBACK_SCRIPT__": "playback.js",
    }.items():
        template = template.replace(marker, (ASSETS / name).read_text(encoding="utf-8"))
    return template


def render_page(path, data, script, form=None, replacements=None):
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = embed_playback_assets((ASSETS / "review.html").read_text(encoding="utf-8"))
    if form is not None:
        template = re.sub(r'<form id="reviewForm"[\s\S]*?</form>', lambda _: form, template, count=1)
    for original, replacement in (replacements or {}).items():
        template = template.replace(original, replacement)
    page = template.replace("__REVIEW_SCRIPT__", script)
    page = page.replace("__FILE_FILTER_SCRIPT__", (ASSETS / "file-filter.js").read_text())
    Path(path).write_text(page.replace("__REVIEW_DATA__", payload), encoding="utf-8")

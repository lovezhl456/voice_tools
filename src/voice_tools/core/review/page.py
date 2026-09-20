"""Local review shell and bundled playback assets; business rules stay in tools."""
import json
from pathlib import Path
import re

ASSETS = Path(__file__).parent


def render_page(path, data, script, form=None, replacements=None):
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (ASSETS / "review.html").read_text(encoding="utf-8")
    if form is not None:
        template = re.sub(r'<form id="reviewForm"[\s\S]*?</form>', lambda _: form, template, count=1)
    for original, replacement in (replacements or {}).items():
        template = template.replace(original, replacement)
    vendor = ASSETS / "vendor"
    bundles = "\n".join((vendor / name).read_text(encoding="utf-8") for name in
                        ("wavesurfer.min.js", "regions.min.js", "timeline.min.js"))
    bundles = ("/* WaveSurfer.js 7.12.12 — " + (vendor / "wavesurfer.LICENSE.txt").read_text() + " */\n" + bundles).replace("</script", "<\\/script")
    page = template.replace("__REVIEW_SCRIPT__", script).replace("__WAVEFORM_VENDOR__", bundles)
    page = page.replace("__WAVEFORM_SCRIPT__", (ASSETS / "waveform.js").read_text())
    page = page.replace("__PLAYBACK_SCRIPT__", (ASSETS / "playback.js").read_text())
    Path(path).write_text(page.replace("__REVIEW_DATA__", payload), encoding="utf-8")

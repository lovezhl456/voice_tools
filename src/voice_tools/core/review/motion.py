"""Share the interface transitions across portable HTML exports."""
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def motion_head(interactive=True):
    directory = Path(__file__).parent
    css = (directory / "motion.css").read_text(encoding="utf-8")
    head = f'<style id="voice-motion-style">{css}</style>'
    if interactive:
        javascript = (directory / "motion.js").read_text(encoding="utf-8")
        head += f"<script>{javascript}</script>"
    return head


def embed_motion(page, *, interactive=True):
    return page.replace("</head>", motion_head(interactive) + "</head>", 1)

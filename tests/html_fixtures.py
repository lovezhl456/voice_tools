"""Read generated page data and resource references without running JavaScript."""
from html.parser import HTMLParser


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.stylesheets = []
        self.current_script = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.current_script = {"attrs": attrs, "text": ""}
            self.scripts.append(self.current_script)
        elif tag == "link" and attrs.get("rel") == "stylesheet":
            self.stylesheets.append(attrs["href"])

    def handle_data(self, data):
        if self.current_script is not None:
            self.current_script["text"] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.current_script = None

#!/usr/bin/env python3
"""Serve local reports with HTTP byte ranges for verified audio seeking."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re


class RangeHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def send_head(self):
        path = Path(self.translate_path(self.path))
        value = self.headers.get('Range')
        self.remaining = None
        if not value or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
        if not match or not any(match.groups()):
            self.send_error(416)
            return None
        start = int(match[1]) if match[1] else max(0, size - int(match[2]))
        end = min(int(match[2]), size - 1) if match[1] and match[2] else size - 1
        if start > end:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.end_headers()
            return None
        stream = path.open('rb')
        stream.seek(start)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        self.remaining = end - start + 1
        return stream

    def copyfile(self, source, output):
        if self.remaining is None:
            return super().copyfile(source, output)
        while self.remaining:
            data = source.read(min(65536, self.remaining))
            if not data:
                break
            output.write(data)
            self.remaining -= len(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8088)
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error('root 必须是现有结果目录')
    handler = partial(RangeHandler, directory=str(args.root.resolve()))
    print(f'http://127.0.0.1:{args.port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), handler).serve_forever()


if __name__ == '__main__':
    main()

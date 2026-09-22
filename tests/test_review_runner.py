from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.check_review import QuietHandler


class ReviewAudioServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        Path(cls.temp.name, 'sample.wav').write_bytes(b'0123456789')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/sample.wav'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def test_full_and_partial_audio_reads(self):
        with urlopen(self.url) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
            self.assertEqual(response.read(), b'0123456789')
        for requested, expected, content_range in [
                ('bytes=3-5', b'345', 'bytes 3-5/10'),
                ('bytes=7-', b'789', 'bytes 7-9/10'),
                ('bytes=-2', b'89', 'bytes 8-9/10')]:
            with self.subTest(requested=requested), urlopen(Request(self.url, headers={'Range': requested})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers['Content-Range'], content_range)
                self.assertEqual(response.read(), expected)

    def test_range_head_has_length_without_body(self):
        with urlopen(Request(self.url, method='HEAD', headers={'Range': 'bytes=2-4'})) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers['Content-Length'], '3')
            self.assertEqual(response.read(), b'')

    def test_unsatisfiable_range_is_rejected(self):
        with self.assertRaises(HTTPError) as failure:
            urlopen(Request(self.url, headers={'Range': 'bytes=10-20'}))
        self.assertEqual(failure.exception.code, 416)
        self.assertEqual(failure.exception.headers['Content-Range'], 'bytes */10')
        failure.exception.close()

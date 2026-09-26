import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from voice_tools.tools.nisqa import weights


class WeightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b"test-only-checkpoint-bytes"
        for name, value in (("SIZE", len(self.data)), ("SHA256", hashlib.sha256(self.data).hexdigest())):
            context = patch.object(weights, name, value)
            context.start()
            self.addCleanup(context.stop)

    def test_download_verified_and_reuse_does_not_connect(self):
        with patch.object(weights.urllib.request, "urlopen", return_value=io.BytesIO(self.data)) as request:
            result = weights.download(self.root)
        self.assertTrue(result["valid"])
        self.assertTrue(result["downloaded"])
        self.assertEqual(request.call_args.args[0].full_url, weights.URL)
        with patch.object(weights.urllib.request, "urlopen", side_effect=AssertionError("must not connect")):
            self.assertFalse(weights.download(self.root)["downloaded"])
        self.assertEqual(weights.read_verified(self.root), self.data)

    def test_bad_download_preserves_old_file_and_removes_partial(self):
        original = self.root / weights.FILENAME
        original.write_bytes(self.data)
        for payload in (b"bad", b"x" * len(self.data), self.data + b"extra"):
            with self.subTest(payload=payload), patch.object(weights.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
                with self.assertRaises(ValueError):
                    weights.download(self.root, force=True)
                self.assertEqual(original.read_bytes(), self.data)
                self.assertEqual(list(self.root.glob("*.part")), [])

    def test_invalid_existing_requires_force(self):
        (self.root / weights.FILENAME).write_bytes(b"broken")
        with patch.object(weights.urllib.request, "urlopen", side_effect=AssertionError("must not connect")):
            with self.assertRaises(ValueError):
                weights.download(self.root)
        with patch.object(weights.urllib.request, "urlopen", return_value=io.BytesIO(self.data)):
            self.assertTrue(weights.download(self.root, force=True)["valid"])

    def test_network_failure_does_not_leave_weight(self):
        with patch.object(weights.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(ValueError, "offline"):
                weights.download(self.root)
        self.assertFalse((self.root / weights.FILENAME).exists())

    def test_missing_and_symlink_are_rejected(self):
        self.assertFalse(weights.describe(self.root)["valid"])
        target = self.root / "real"
        target.write_bytes(self.data)
        (self.root / weights.FILENAME).symlink_to(target)
        with self.assertRaises(ValueError):
            weights.read_verified(self.root)
        with self.assertRaises(ValueError):
            weights.download(self.root, force=True)

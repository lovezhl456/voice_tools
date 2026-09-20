"""Pinned TorchMetrics port, loaded from bytes without its automatic downloader."""
import importlib.metadata
import io
import sys

from . import weights

DEPENDENCIES = ("torch", "torchmetrics", "librosa", "soundfile", "requests")
TORCHMETRICS_VERSION = "1.9.0"
SCORE_NAMES = ("mos", "noisiness", "discontinuity", "coloration", "loudness")


def doctor(directory=None):
    versions = {}
    for name in DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    model = weights.describe(directory)
    python_ok = sys.version_info >= (3, 10)
    ready = python_ok and all(versions.values()) and versions["torchmetrics"] == TORCHMETRICS_VERSION and model["valid"]
    return {"ready": bool(ready), "check_scope": "package_metadata_and_weight_sha256; runtime_not_loaded",
            "python": sys.version.split()[0], "python_supported": python_ok,
            "versions": versions, "device": "cpu", "model": model,
            "install_hint": "Python 3.10+；python -m pip install -e '.[nisqa]'；再运行 nisqa download",
            "license_note": "官方权重仅按 CC BY-NC-SA 4.0 使用；商业使用需另行授权"}


class Scorer:
    def __init__(self, directory=None, threads=2):
        data = weights.read_verified(directory)
        environment = doctor(directory)
        if not environment["ready"]:
            raise ValueError(f"NISQA 环境未就绪：{environment['install_hint']}；需要 torchmetrics=={TORCHMETRICS_VERSION}")
        try:
            import torch
            from torchmetrics.functional.audio import nisqa

            torch.set_num_threads(threads)
            checkpoint = torch.load(io.BytesIO(data), map_location="cpu", weights_only=True)
            self.args = checkpoint["args"]
            # Private helpers are version-pinned and exercised by the real-model smoke test.
            self.model = nisqa._NISQADIM(self.args)
            self.model.load_state_dict(checkpoint["model_state_dict"], strict=True)
            self.model.eval()
            self.torch, self.nisqa = torch, nisqa
        except Exception as error:
            raise ValueError(f"NISQA 运行库或模型加载失败：{error}") from error

    def __call__(self, samples, sample_rate):
        torch, nisqa = self.torch, self.nisqa
        spectrogram = nisqa._get_librosa_melspec(samples.reshape(1, -1), sample_rate, self.args)
        x, lengths = nisqa._segment_specs(torch.from_numpy(spectrogram), self.args)
        with torch.inference_mode():
            values = self.model(x, lengths.expand(x.shape[0]))[0].tolist()
        return dict(zip(SCORE_NAMES, values))

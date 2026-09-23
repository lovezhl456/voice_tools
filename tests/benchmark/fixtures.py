"""Synthetic benchmark evidence reused by task and latency tests."""
from voice_tools.core.files import write_json
from voice_tools.tools.benchmark.config import configuration
from voice_tools.tools.benchmark.observation import Observation


def write_evidence(directory):
    directory.mkdir(parents=True)
    config = configuration({"case_id": "threshold", "detector": {"backend": "energy"},
                            "expectations": {"first_audio_max_ms": 50}})
    observer = Observation(directory, lambda: 0, config["detector"])
    for index in range(50):
        pcm = b"\x00\x20\x00\xe0" * 80 if 5 <= index < 15 else bytes(320)
        observer.submit("rx", pcm, at=index * .02)
        observer.drain()
    observer.close()
    write_json(directory / "plan.json", {"benchmark": config, "steps": []})
    write_json(directory / "result.json", {"status": "completed", "execution_status": "completed"})
    (directory / "events.jsonl").write_text('{"event":"strategy_start","at_s":0}\n')

"""Write only the synthetic recording needed by a workflow or CLI test."""
from voice_tools.audio.io import write_wav
from voice_tools.core.files import write_json
from voice_tools.tools.recording_qa.scenarios import fixture


def write_case(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    samples, events = fixture(name)
    path = directory / (name + ".wav")
    write_wav(path, samples, 8000)
    if events is not None:
        write_json(path.with_suffix(".events.json"), events)
    return path

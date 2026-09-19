"""Create synthetic audio and a placeholder scenario; does not make a call."""
import argparse
import math
import struct
import wave

from voice_tools.core.files import new_output, write_json
from voice_tools.tools.sip.scenario import template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = new_output(args.out)
    with wave.open(str(out / "tone.wav"), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(struct.pack("<h", round(8000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(8000)))
    spec = template()
    spec["steps"] = [{"action": "wait", "seconds": 1}, {"action": "dtmf", "digits": "1"},
                     {"action": "play", "file": "tone.wav"}, {"action": "wait", "seconds": 2}, {"action": "hangup"}]
    write_json(out / "scenario.json", spec)
    print(out / "scenario.json")


if __name__ == "__main__":
    main()

"""Single source for public settings, units and result contracts (Python 3.9+)."""
import math

SCHEMA_VERSION = "1.0"
SAMPLE_RATES = (8000, 16000, 24000, 32000, 44100, 48000)
MAX_SECONDS = 3600
MAX_DECODED_BYTES = 512 * 1024**2
EXIT_CODES = {"0": "execution completed; measurement may be insufficient", "2": "global argument/configuration/dependency error", "3": "one or more files failed; completed evidence retained"}
# Millisecond settings use whole 10 ms frames; rejecting fractional frames avoids silent truncation.
PARAMETERS = {
    "energy_threshold": {"default": 50.0, "min": 0.001, "max": 1000000, "unit": "mean_square_x_1e6"},
    "ai_min_speaking_ms": {"default": 20, "min": 10, "max": 2000, "unit": "ms", "multiple_of": 10},
    "human_min_speaking_ms": {"default": 20, "min": 10, "max": 2000, "unit": "ms", "multiple_of": 10},
    "min_silence_ms": {"default": 2000, "min": 10, "max": 10000, "unit": "ms", "multiple_of": 10},
    "onset_peak_mult": {"default": 5.0, "min": 1, "max": 100, "unit": "ratio"},
    "onset_rel_frac": {"default": 0.10, "min": 0, "max": 1, "unit": "fraction"},
    "crosstalk_ratio": {"default": 3.0, "min": 0, "max": 100, "unit": "energy_ratio"},
    "min_ai_response_ms": {"default": 300, "min": 0, "max": 10000, "unit": "ms", "multiple_of": 10},
    "min_human_speech_ms": {"default": 300, "min": 0, "max": 10000, "unit": "ms", "multiple_of": 10},
}
FIXED_PARAMETERS = {"crosstalk_window_ms": 500, "barge_window_s": 0.6, "min_resumption_s": 1.0,
                    "frame_ms": 10, "trim_energy_multiplier": 10, "trim_sustained_ms": 30}


def validate_parameters(supplied=None):
    supplied = supplied or {}
    if set(supplied) - set(PARAMETERS):
        raise ValueError("未知 latency 参数")
    values, sources = {}, {}
    for name, rule in PARAMETERS.items():
        value = supplied.get(name)
        sources[name] = "explicit" if value is not None else "default"
        value = rule["default"] if value is None else value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} 须为有限数值")
        wrong_step = "multiple_of" in rule and value % rule["multiple_of"] != 0
        if not rule["min"] <= value <= rule["max"] or wrong_step:
            raise ValueError(f"{name} 范围 {rule['min']}–{rule['max']} {rule['unit']}；步长 {rule.get('multiple_of', '不限')}")
        values[name] = int(value) if "multiple_of" in rule else float(value)
    return values, sources


def public_contract():
    return {"kind": "latency_run", "schema_version": SCHEMA_VERSION, "parameters": PARAMETERS,
            "fixed_parameters": FIXED_PARAMETERS, "sample_rates": list(SAMPLE_RATES),
            "max_seconds": MAX_SECONDS, "max_decoded_bytes": MAX_DECODED_BYTES,
            "file_timeout_s": {"default": 600, "min": 0.1, "max": 86400}, "exit_codes": EXIT_CODES,
            "time_origin": "analyzed_recording_start", "coverage": "paired eligible human segments / all eligible human segments; null when denominator is zero"}

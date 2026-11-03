import subprocess
import sys

import media
import pytest

SILENCEDETECT = """
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 0.62 | silence_duration: 0.62
[silencedetect @ 0x1] silence_start: 5.98
[silencedetect @ 0x1] silence_end: 6.88 | silence_duration: 0.9
[silencedetect @ 0x1] silence_start: 9.5
"""
LOUDNORM = """
[Parsed_loudnorm_0 @ 0x1]
{
	"input_i" : "-20.07",
	"input_tp" : "-0.11",
	"input_lra" : "3.90",
	"input_thresh" : "-30.44",
	"output_i" : "-14.00",
	"output_tp" : "-1.50",
	"output_lra" : "3.90",
	"output_thresh" : "-24.40",
	"normalization_type" : "dynamic",
	"target_offset" : "3.22"
}
"""
EBUR128 = """
[Parsed_ebur128_0 @ 0x1] t: 1.0 M: -20.0 S: -21.0 I: -19.0 LUFS
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -14.0 LUFS
    Threshold: -24.3 LUFS

  True peak:
    Peak:       -1.9 dBFS
"""


def test_parse_silences_closes_an_open_silence_at_the_end():
    assert media.parse_silences(SILENCEDETECT, 12.0) == [(0.0, 0.62), (5.98, 6.88), (9.5, 12.0)]


def test_parse_loudnorm_and_its_second_pass():
    measured = media.parse_loudnorm(LOUDNORM)
    assert measured == {
        "input_i": -20.07,
        "input_tp": -0.11,
        "input_lra": 3.9,
        "input_thresh": -30.44,
        "target_offset": 3.22,
    }
    second = media.loudnorm_second_pass(measured)
    assert second.startswith(media.LOUDNORM)
    assert "measured_I=-20.07:measured_TP=-0.11" in second and second.endswith(":linear=true")
    with pytest.raises(media.ToolError):
        media.parse_loudnorm("no json here")


def test_parse_ebur128_reads_the_summary_not_the_running_values():
    assert media.parse_ebur128(EBUR128) == (-14.0, -1.9)
    with pytest.raises(media.ToolError):
        media.parse_ebur128("[Parsed_ebur128_0] t: 1.0 I: -19.0 LUFS")


def test_limiter_gain_iterates_to_the_target(fake_run):
    # Without the limiter the file measures -20; the limiter gives back a third of each step.
    def measure(args):
        chain = args[args.index("-af") + 1]
        gain = float(chain.split("volume=")[1].split("dB")[0]) if "volume=" in chain else 0.0
        loud = -20.0 + gain * (2 / 3 if gain else 1)
        return "", LOUDNORM.replace('"-20.07"', f'"{loud:.2f}"')

    fake_run.answers.append(measure)
    gain = media.limiter_gain(media.Path("mix.wav"))
    assert abs(-20.0 + gain * 2 / 3 - (media.TARGET_LUFS - 0.3)) <= 0.2


def test_run_reports_a_missing_program_and_a_failure():
    with pytest.raises(media.ToolError, match="not installed"):
        media.run(["no-such-program-spotcool"])
    with pytest.raises(media.ToolError, match="exited 3"):
        media.run([sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"])
    done = media.run([sys.executable, "-c", "print('ok')"])
    assert isinstance(done, subprocess.CompletedProcess) and done.stdout.strip() == "ok"

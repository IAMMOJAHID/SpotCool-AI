"""Speak the narration with Windows' built-in voice, as a placeholder until it is recorded.

    uv run python scripts/video/tts_placeholder.py [--rate N] [--voice NAME] [--out WAV]

Renders ``video/narration.md`` with ``System.Speech`` through Windows PowerShell (nothing to
install), one paragraph to a file, and joins them with ffmpeg into
``video/renders/narration_placeholder.wav``: the paragraphs in storyboard order, ``PAUSE_S`` of
silence between them, as the person recording is asked to leave (SAPI shortens long breaks of
its own, so the silence is added after). It then cuts the
file the way the assembly will (``voice.placements_for``) and prints where each paragraph lands;
it exits 1 if one runs past its beat (raise ``--rate``). The assembly uses this file until
``video/raw/narration.wav`` exists.
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import media
import voice
from storyboard import PLACEHOLDER_NARRATION, load_narration, load_storyboard, narrated

PAUSE_S = 2.0
DEFAULT_RATE = 0
"""SAPI's rate, -10 to 10; at 0 the default voice speaks about 140 words a minute."""
SCRIPT = r"""
param([string]$In, [string]$OutDir, [int]$Rate, [string]$Voice = "")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
# Not @(...): PowerShell 5.1 passes a JSON array down the pipe as one object.
$paragraphs = Get-Content -Raw -Encoding UTF8 -LiteralPath $In | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) { $synth.SelectVoice($Voice) }
$synth.Rate = $Rate
$audio = "System.Speech.AudioFormat"
$format = New-Object "$audio.SpeechAudioFormatInfo" 48000,
  ([type]"$audio.AudioBitsPerSample")::Sixteen, ([type]"$audio.AudioChannel")::Mono
$i = 0
foreach ($p in $paragraphs) {
  $synth.SetOutputToWaveFile((Join-Path $OutDir "p$i.wav"), $format)
  $synth.Speak([string]$p)
  $i++
}
$synth.SetOutputToNull()
$synth.Dispose()
"""


def synthesize(
    paragraphs: list[str], out: Path, rate: int = DEFAULT_RATE, voice_name: str | None = None
) -> Path:
    """Speak the paragraphs into a 48 kHz mono WAV, ``PAUSE_S`` apart.

    Raises:
        media.ToolError: If Windows PowerShell is missing, or the speech or ffmpeg fails.
    """
    powershell = shutil.which("powershell")
    if not powershell:
        raise media.ToolError("Windows PowerShell (powershell.exe) is not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        script, text = Path(work) / "speak.ps1", Path(work) / "paragraphs.json"
        script.write_text(SCRIPT, "utf-8-sig")  # a BOM, so PowerShell 5.1 reads it as UTF-8
        text.write_text(json.dumps(paragraphs, ensure_ascii=False), "utf-8")
        args = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
        args += ["-File", str(script), "-In", str(text), "-OutDir", work, "-Rate", str(rate)]
        if voice_name:
            args += ["-Voice", voice_name]
        media.run(args)
        join(sorted(Path(work).glob("p*.wav"), key=lambda p: int(p.stem[1:])), out)
    return out


def join(parts: list[Path], out: Path) -> Path:
    """Join WAVs into one, with ``PAUSE_S`` of silence before, between and after them."""
    inputs = [arg for part in parts for arg in ("-i", str(part))]
    pads = "".join(
        f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,apad=pad_dur={PAUSE_S}[p{i}];"
        for i in range(len(parts))
    )
    lead = f"anullsrc=r=48000:cl=mono,atrim=0:{PAUSE_S / 2}[lead];"  # a real lead-in, not a delay
    joined = "[lead]" + "".join(f"[p{i}]" for i in range(len(parts)))
    graph = f"{pads}{lead}{joined}concat=n={len(parts) + 1}:v=0:a=1[out]"
    encode = [
        "-map",
        "[out]",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        "-fflags",
        "+bitexact",
    ]
    media.ffmpeg(*inputs, "-filter_complex", graph, *encode, str(out))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rate", type=int, default=DEFAULT_RATE, choices=range(-10, 11))
    parser.add_argument("--voice", help='an installed voice, e.g. "Microsoft Zira Desktop"')
    parser.add_argument("--out", type=Path, default=PLACEHOLDER_NARRATION)
    args = parser.parse_args(argv)
    board, narration = load_storyboard(), load_narration()
    paragraphs = [narration[beat.narration_id] for beat in narrated(board) if beat.narration_id]
    try:
        synthesize(paragraphs, args.out, args.rate, args.voice)
        placements = voice.placements_for(args.out, board, narration)
    except (media.ToolError, voice.NarrationError) as exc:
        print(f"tts placeholder: {exc}", file=sys.stderr)
        return 1
    for p in placements:
        room = board.beat(p.beat_id).end - voice.TAIL_S - p.at_s
        print(f"{p.beat_id:10} {p.at_s:6.1f} s  {p.length_s:5.1f} s of {room:5.1f} s")
    print(f"placeholder narration -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

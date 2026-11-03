import json
from pathlib import Path

import pytest
import tts_placeholder


def test_synthesize_speaks_each_paragraph_then_joins_them(fake_run, tmp_path, monkeypatch):
    monkeypatch.setattr(tts_placeholder.shutil, "which", lambda name: "powershell.exe")
    seen: dict[str, object] = {}

    def speak(args):
        if args[0] == "powershell.exe":
            seen["paragraphs"] = json.loads(Path(args[args.index("-In") + 1]).read_text("utf-8"))
            seen["script"] = Path(args[args.index("-File") + 1]).read_text("utf-8-sig")
            out_dir = Path(args[args.index("-OutDir") + 1])
            for i in range(2):
                (out_dir / f"p{i}.wav").write_bytes(b"")
            seen["rate"] = args[args.index("-Rate") + 1]
        return "", ""

    fake_run.answers.append(speak)
    tts_placeholder.synthesize(["First, 19.94 %.", "Second."], tmp_path / "n.wav", rate=2)
    assert seen["paragraphs"] == ["First, 19.94 %.", "Second."]
    assert "System.Speech" in str(seen["script"]) and seen["rate"] == "2"
    join = fake_run.calls[1]
    assert join[0] == "ffmpeg" and join[-1] == str(tmp_path / "n.wav")
    graph = join[join.index("-filter_complex") + 1]
    assert "apad=pad_dur=2.0" in graph and "[lead][p0][p1]concat=n=3:v=0:a=1[out]" in graph


def test_synthesize_needs_windows_powershell(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_placeholder.shutil, "which", lambda name: None)
    with pytest.raises(tts_placeholder.media.ToolError, match="PowerShell"):
        tts_placeholder.synthesize(["x"], tmp_path / "n.wav")

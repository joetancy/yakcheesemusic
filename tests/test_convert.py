from app.audio.convert import build_command, should_convert, LOSSLESS


def test_should_convert():
    assert should_convert("flac", "mp3")
    assert should_convert("FLAC", "mp3")
    assert not should_convert("mp3", "mp3")
    assert not should_convert("flac", "original")
    assert not should_convert(None, "mp3")
    assert "flac" in LOSSLESS and "mp3" not in LOSSLESS


def test_build_command():
    cmd = build_command("a.flac", "a.mp3")
    assert cmd[:3] == ["ffmpeg", "-hide_banner", "-loglevel"]
    assert "-b:a" in cmd and "320k" in cmd
    assert "libmp3lame" in cmd and cmd[-1] == "a.mp3"
    assert "-map" in cmd  # tags + art ride along


def test_convert_roundtrip(tmp_path):
    import wave
    from app.audio.convert import convert_to_mp3
    src = str(tmp_path / "t.flac")
    # real minimal flac via ffmpeg (available in test image)
    import subprocess
    w = str(tmp_path / "s.wav")
    with wave.open(w, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 8000)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", w, src], check=True, timeout=60)
    out = convert_to_mp3(src)
    assert out.endswith(".mp3")
    import os
    assert os.path.exists(out) and not os.path.exists(src)
    from mutagen import File as MFile
    audio = MFile(out)
    assert audio.info.length > 0


def test_convert_failure_keeps_source(tmp_path):
    from app.audio.convert import convert_to_mp3
    src = str(tmp_path / "bad.flac")
    open(src, "w").write("not audio")
    try:
        convert_to_mp3(src)
        raise AssertionError("should raise")
    except RuntimeError:
        pass
    import os
    assert os.path.exists(src)  # source untouched on failure


def test_preferred_format_setting(client):
    r = client.patch("/api/settings", json={"preferred_format": "mp3"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["preferred_format"] == "mp3"
    r = client.patch("/api/settings", json={"preferred_format": "ogg"})
    assert r.status_code == 422

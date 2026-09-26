from app.audio.replaygain import build_command, scan_library


def test_build_command():
    cmd = build_command(["a.mp3", "b.flac"], -14.0)
    assert cmd == ["rsgain", "custom", "-s", "i", "-l", "-14.0",
                   "-S", "-q", "a.mp3", "b.flac"]


def test_scan_library(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.flac").write_text("x")
    (tmp_path / "b.mp3").write_text("x")
    (tmp_path / "c.txt").write_text("x")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "d.mp3").write_text("x")
    found = scan_library(str(tmp_path))
    assert found == [str(tmp_path / "b.mp3"), str(tmp_path / "sub" / "a.flac")]


def test_apply_batches(monkeypatch):
    import subprocess
    import app.audio.replaygain as rg
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, failed = rg.apply_replaygain(["a", "b", "c"], -14.0, chunk=2)
    assert (ok, failed) == (3, [])
    assert len(calls) == 2 and all(c[:7] == ["rsgain", "custom", "-s", "i",
                                             "-l", "-14.0", "-S"] for c in calls)


def test_apply_records_failures(monkeypatch):
    import subprocess
    import app.audio.replaygain as rg

    def fake_run(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, failed = rg.apply_replaygain(["a"], -14.0)
    assert (ok, failed) == (0, ["a"])

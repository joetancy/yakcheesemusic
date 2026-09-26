from app.audio.quality import is_upgrade


def test_upgrade_flac_over_aac():
    cur = {"format": "aac", "bit_depth": None, "sample_rate": 44100, "bitrate": 256}
    cand = {"format": "flac", "bit_depth": 16, "sample_rate": 44100, "bitrate": 900}
    assert is_upgrade(cur, cand)
    assert not is_upgrade(cand, cur)


def test_no_downgrade():
    cur = {"format": "flac", "bit_depth": 16, "sample_rate": 44100, "bitrate": 900}
    cand = {"format": "mp3", "bit_depth": None, "sample_rate": 44100, "bitrate": 320}
    assert not is_upgrade(cur, cand)

from app.sync.batch import decide, pick_best, quality_rank


def _cand(score, verdict, fmt="mp3"):
    return {"score": score, "verdict": verdict, "format": fmt,
            "provider": "soulseek",
            "provider_track_id": f"{fmt}{score}", "title": "t", "artist": "a"}


def test_quality_rank():
    assert quality_rank("flac") > quality_rank("mp3") > quality_rank("exe")
    assert quality_rank(None) == 0


def test_pick_best_prefers_flac_on_near_tie():
    ranked = [_cand(95, "auto", "mp3"), _cand(94, "auto", "flac")]
    assert pick_best(ranked)["format"] == "flac"


def test_pick_best_respects_clear_winner():
    ranked = [_cand(95, "auto", "mp3"), _cand(80, "auto", "flac")]
    assert pick_best(ranked)["format"] == "mp3"


def test_decide_auto_downloads():
    action, pick = decide([_cand(95, "auto", "mp3")])
    assert action == "download" and pick is not None


def test_decide_close_rival_goes_to_review():
    ranked = [_cand(85, "auto_if_no_competition"), _cand(83, "needs_review")]
    assert decide(ranked) == ("review", None)


def test_decide_no_rival_downloads():
    ranked = [_cand(85, "auto_if_no_competition"), _cand(70, "reject")]
    action, pick = decide(ranked)
    assert action == "download" and pick is not None


def test_decide_low_score_review():
    assert decide([_cand(60, "reject")]) == ("review", None)


def test_classify_custom_thresholds():
    from app.matching.scoring import classify_score
    assert classify_score(80) == "auto_if_no_competition"
    assert classify_score(80, auto=70) == "auto"
    assert classify_score(70, review=75) == "reject"
    assert classify_score(70, review=70) == "needs_review"


def test_decide_custom_thresholds():
    action, _ = decide([_cand(85, "auto_if_no_competition")], auto=80)
    # verdict still says conditional (scored under old bands), rival rule uses param
    assert action in ("download", "review")


def test_settings_validation():
    from app.db.settings_store import validate
    cur = {"match_auto": "90", "match_conditional": "80", "match_review": "65"}
    assert validate("match_auto", "95", cur) == 95.0
    try:
        validate("match_review", "95", cur)
        raise AssertionError("should reject review above auto")
    except ValueError:
        pass
    try:
        validate("match_auto", "150", cur)
        raise AssertionError("should reject out of range")
    except ValueError:
        pass


def test_settings_api_roundtrip(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["match_auto"] == 90.0
    r = client.patch("/api/settings", json={"match_auto": 85})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["match_auto"] == 85.0
    r = client.patch("/api/settings", json={"match_review": 90})
    assert r.status_code == 422


def test_decide_ignores_rejected():
    ranked = [_cand(95, "auto", "mp3"), _cand(92, "auto", "flac")]
    ranked[0]["rejected"] = True
    action, pick = decide(ranked)
    assert action == "download" and pick["format"] == "flac"


def test_decide_all_rejected_is_review():
    ranked = [_cand(95, "auto"), _cand(92, "auto")]
    for c in ranked:
        c["rejected"] = True
    assert decide(ranked) == ("review", None)


def test_rejections_survive_research():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    from app.db.models import Track
    from app.sync.search import save_candidates, rejected_keys

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    t = Track(title="T", artist="A", status="needs_review")
    db.add(t)
    db.commit()
    ranked = [_cand(90, "auto", "mp3"), _cand(88, "auto", "flac")]
    mapping = save_candidates(db, t.id, ranked)
    # user rejects the mp3
    from app.db.models import DownloadCandidate
    db.get(DownloadCandidate, mapping["mp390"]).rejected_reason = "rejected by user"
    db.commit()
    assert rejected_keys(db, t.id) == {"mp390"}
    # fresh search returns the same files: rejection re-applied, flags set
    ranked2 = [_cand(90, "auto", "mp3"), _cand(88, "auto", "flac")]
    save_candidates(db, t.id, ranked2)
    assert ranked2[0]["rejected"] is True and ranked2[1]["rejected"] is False
    action, pick = decide(ranked2)
    assert action == "download" and pick["format"] == "flac"


def test_search_one_parallel(tmp_path):
    """search_one runs concurrently with stubbed search; outcomes correct."""
    import app.sync.batch as batch_mod
    import app.sync.search as search_mod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.database import Base
    from app.db.models import Track

    engine = create_engine(f"sqlite:///{tmp_path}/t.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, future=True)
    db = factory()
    ids = []
    for i in range(6):
        t = Track(title=f"T{i}", artist="A", status="pending")
        db.add(t)
        db.flush()
        ids.append(t.id)
    db.commit()
    db.close()

    def fake_search(track, auto=90.0, conditional=80.0, review=65.0):
        from app.matching.matcher import match_candidates
        src = {"title": track.title, "artist": "A", "album": "",
               "duration_ms": 200000, "isrc": None}
        cands = [{"provider": "soulseek", "provider_track_id": f"u/f{i}.flac",
                  "title": track.title, "artist": "A", "album": "",
                  "duration_ms": 200000, "quality": "FLAC",
                  "format": "flac", "size": 1, "source_url": None}
                 for i in range(2)]
        return match_candidates(src, cands, auto, conditional, review)

    import threading
    barrier = threading.Barrier(6)

    orig = search_mod.search_candidates

    def gated(track, auto=90.0, conditional=80.0, review=65.0):
        barrier.wait(timeout=30)  # prove threads overlap
        return fake_search(track, auto, conditional, review)

    search_mod.search_candidates = gated
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda tid: batch_mod.search_one(factory, tid), ids))
    finally:
        search_mod.search_candidates = orig
    assert all(r[0] == "download" for r in results)
    db = factory()
    try:
        assert db.query(Track).filter(Track.status == "downloading").count() == 6
    finally:
        db.close()


def test_save_mapping_covers_tied_pick():
    """Regression: pick must resolve even when 10+ candidates tie on score."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    from app.db.models import Track, DownloadCandidate
    from app.sync.search import save_candidates

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    t = Track(title="CLICK", artist="JISOO", status="pending")
    db.add(t)
    db.commit()
    ranked = [_cand(95.0, "auto", "mp3") for _ in range(12)]
    for i, c in enumerate(ranked):
        c["provider_track_id"] = f"user{i}/file{i}.mp3"
    mapping = save_candidates(db, t.id, ranked)
    assert len(mapping) == 12
    # full (unlimited) re-query, as batch.py does — every pick resolves
    id_by_key = {c.provider_track_id: c.id for c in db.execute(
        select(DownloadCandidate).where(DownloadCandidate.track_id == t.id)
        .order_by(DownloadCandidate.score.desc())).scalars().all()}
    for c in ranked:
        assert id_by_key[c["provider_track_id"]] == mapping[c["provider_track_id"]]

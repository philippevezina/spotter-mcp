from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import garminconnect
import pytest
from sqlalchemy import Engine, func, select, update

from spotter.cli import main
from spotter.db.schema import exercises
from spotter.db.seed import seed_exercises
from spotter.garmin import tokens
from spotter.garmin.client import CatalogEntry
from tests.test_garmin_client import REFRESHED, TOKEN, FakeGarmin


def _count(db: Engine) -> int:
    with db.connect() as conn:
        return conn.execute(select(func.count()).select_from(exercises)).scalar_one()


def test_seed_exercises_is_idempotent(
    env: pytest.MonkeyPatch, db: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["seed-exercises"])
    assert capsys.readouterr().out.strip() == "inserted: 1527, total: 1527"
    assert _count(db) == 1527

    main(["seed-exercises"])
    assert capsys.readouterr().out.strip() == "inserted: 0, total: 1527"


def test_seed_keeps_curated_fields(env: pytest.MonkeyPatch, db: Engine) -> None:
    main(["seed-exercises"])
    bench = (exercises.c.garmin_category == "BENCH_PRESS") & (
        exercises.c.garmin_name == "BARBELL_BENCH_PRESS"
    )
    with db.begin() as conn:
        conn.execute(
            update(exercises)
            .where(bench)
            .values(curated=True, display_name="Bench", increment_lb=5)
        )
    main(["seed-exercises"])
    with db.connect() as conn:
        row = conn.execute(select(exercises).where(bench)).one()
    assert (row.curated, row.display_name, row.increment_lb) == (True, "Bench", 5)


def test_import_tokens(
    env: pytest.MonkeyPatch,
    db: Engine,
    token_key: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "garmin_tokens.json"
    path.write_text(TOKEN)
    main(["import-tokens", str(path)])
    out = capsys.readouterr().out
    assert "di_client_id, di_refresh_token, di_token" in out
    assert "r1" not in out
    with db.connect() as conn:
        assert tokens.load_tokens(conn, token_key) == TOKEN


def test_import_tokens_rejects_missing_keys(
    env: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"di_token": "secret-value"}))
    with pytest.raises(SystemExit) as info:
        main(["import-tokens", str(path)])
    assert info.value.code == 1
    err = capsys.readouterr().err
    assert "missing keys" in err
    assert "secret-value" not in err


def test_import_tokens_without_key(
    env: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from spotter.config import get_settings

    env.delenv("GARMIN_TOKEN_KEY")
    get_settings.cache_clear()
    path = tmp_path / "t.json"
    path.write_text(TOKEN)
    with pytest.raises(SystemExit):
        main(["import-tokens", str(path)])
    assert "GARMIN_TOKEN_KEY is not set" in capsys.readouterr().err


@pytest.fixture
def fake_garmin(env: pytest.MonkeyPatch) -> type[FakeGarmin]:
    class Fake(FakeGarmin):
        instances: ClassVar[list[FakeGarmin]] = []

    env.setattr(garminconnect, "Garmin", Fake)
    return Fake


def test_garmin_check_saves_refresh(
    fake_garmin: type[FakeGarmin],
    db: Engine,
    token_key: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
    fake_garmin.refresh_on_login = True
    main(["garmin-check"])
    assert capsys.readouterr().out.split() == ["ok", "refreshed:", "true"]
    with db.connect() as conn:
        assert tokens.load_tokens(conn, token_key) == REFRESHED


def test_garmin_check_unchanged(
    fake_garmin: type[FakeGarmin], db: Engine, token_key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
    main(["garmin-check"])
    assert capsys.readouterr().out.split() == ["ok", "refreshed:", "false"]


def test_garmin_check_without_tokens(
    fake_garmin: type[FakeGarmin], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main(["garmin-check"])
    assert "No Garmin tokens stored" in capsys.readouterr().err
    assert fake_garmin.instances == []


def test_garmin_check_expired(
    fake_garmin: type[FakeGarmin], db: Engine, token_key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
    fake_garmin.login_error = garminconnect.GarminConnectAuthenticationError("401")
    with pytest.raises(SystemExit):
        main(["garmin-check"])
    assert "Garmin session expired" in capsys.readouterr().err


def test_bootstrap_login(
    fake_garmin: type[FakeGarmin], db: Engine, token_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["a@b.c", "654321"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda _: "pw")
    main(["bootstrap-login"])
    inst = fake_garmin.instances[0]
    assert (inst.email, inst.password) == ("a@b.c", "pw")
    with db.connect() as conn:
        stored = tokens.load_tokens(conn, token_key)
    assert stored is not None and json.loads(stored)["mfa"] == "654321"


def test_bootstrap_login_429_stops(
    fake_garmin: type[FakeGarmin],
    db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "a@b.c")
    monkeypatch.setattr("getpass.getpass", lambda _: "pw")
    fake_garmin.login_error = garminconnect.GarminConnectTooManyRequestsError("429")
    with pytest.raises(SystemExit):
        main(["bootstrap-login"])
    assert "do not retry" in capsys.readouterr().err
    assert len(fake_garmin.instances) == 1


FIXTURES = Path(__file__).parent / "fixtures" / "garmin"


@pytest.fixture
def garmin_history(env: pytest.MonkeyPatch, db: Engine, token_key: str) -> type[FakeGarmin]:
    """Stored tokens, a bench catalog row, and a fake Garmin with one strength session."""

    class Fake(FakeGarmin):
        instances: ClassVar[list[FakeGarmin]] = []

        def get_activities_by_date(self, startdate: str, enddate: str) -> list[dict[str, object]]:
            return [json.loads((FIXTURES / "strength_activity_summary.json").read_text())]

        def get_activity_exercise_sets(self, activity_id: int) -> dict[str, object]:
            return json.loads((FIXTURES / "exercise_sets.json").read_text())

    env.setattr(garminconnect, "Garmin", Fake)
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
        seed_exercises(
            conn, [CatalogEntry("Barbell Bench Press", "BENCH_PRESS", "BARBELL_BENCH_PRESS")]
        )
    return Fake


def test_backfill_then_strength_log(
    garmin_history: type[FakeGarmin], capsys: pytest.CaptureFixture[str]
) -> None:
    main(["backfill", "--since", "2025-10-01"])
    out = capsys.readouterr().out.splitlines()
    assert "start: 2025-10-01" in out
    assert "strength_sessions: 1" in out
    assert "partial: false" in out
    assert "tokens_refreshed: false" in out

    main(["strength-log"])
    assert capsys.readouterr().out.splitlines() == [
        "2025-10-05  activity 900000001",
        "  set  0  Barbell Bench Press: 5 x 190.0 lb",
        "  set  2  Barbell Bench Press: 0 x 190.0 lb",
        "  stats  Barbell Bench Press: top 190.0 lb x 5, working sets 1, reps 5"
        ", volume 950.0 lb, e1rm -",
    ]

    main(["rebuild-stats"])
    assert capsys.readouterr().out.strip() == "activities rebuilt: 1"


def test_sync_respects_min_interval(
    garmin_history: type[FakeGarmin], capsys: pytest.CaptureFixture[str]
) -> None:
    main(["sync"])
    assert "skipped: " in capsys.readouterr().out.splitlines()
    main(["sync"])
    assert "skipped: recent" in capsys.readouterr().out.splitlines()
    main(["sync", "--force"])
    assert "skipped: " in capsys.readouterr().out.splitlines()

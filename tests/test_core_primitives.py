import re
import tomllib
from pathlib import Path

import pytest

import careeros
from careeros.core import ids, models, versions
from careeros.core.models import State


def test_prefix_table_has_27_unique_three_letter_prefixes() -> None:
    assert len(ids.PREFIXES) == 27
    assert len(set(ids.PREFIXES.values())) == 27
    assert all(len(p) == 3 and p.isalpha() and p.islower() for p in ids.PREFIXES.values())
    assert ids.PREFIXES["job"] == "job"
    assert ids.PREFIXES["event"] == "evt"


def test_new_id_shape_and_validity() -> None:
    value = ids.new_id("job")
    assert re.fullmatch(r"job_[0-9a-hjkmnp-tv-z]{10}", value)
    assert ids.is_valid_id("job", value)
    assert not ids.is_valid_id("event", value)
    assert not ids.is_valid_id("job", "job_SHORT")
    assert not ids.is_valid_id("job", None)


def test_new_id_retries_on_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = iter([b"\x00" * 10, b"\x01" * 10])
    monkeypatch.setattr(ids.secrets, "token_bytes", lambda n: next(chunks))
    assert ids.new_id("job", existing={"job_0000000000"}) == "job_1111111111"


def test_new_id_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown entity kind"):
        ids.new_id("spaceship")


def test_parse_and_compare_versions() -> None:
    assert versions.parse_version("0.3.0") == (0, 3, 0)
    assert versions.parse_version("1.2.3.dev4") == (1, 2, 3)
    assert versions.compare_versions("0.3.0", "0.2.9") == 1
    assert versions.compare_versions("0.3.0", "0.3.0.dev1") == 0
    assert versions.compare_versions("0.2.0", "0.10.0") == -1


@pytest.mark.parametrize("bad", ["", "abc", "1.2", "v1.2.3"])
def test_parse_version_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        versions.parse_version(bad)


def test_utc_now_has_full_iso_shape() -> None:
    assert models.is_utc_timestamp(models.utc_now())


@pytest.mark.parametrize(
    "bad",
    ["2026-10-06", "2026-10-06T09:15:00", "2026-10-06T09:15:00+00:00", "2026-13-01T00:00:00Z", None, 5],
)
def test_is_utc_timestamp_rejects_other_shapes(bad: object) -> None:
    assert not models.is_utc_timestamp(bad)


def test_date_to_utc_makes_midnight_and_validates() -> None:
    assert models.date_to_utc("2026-10-01") == "2026-10-01T00:00:00Z"
    with pytest.raises(ValueError):
        models.date_to_utc("10/01/2026")


def test_state_enum_lists_17_states_in_lifecycle_order() -> None:
    assert [s.name for s in State] == [
        "DISCOVERED", "EVALUATED", "SHORTLISTED", "RESEARCHED", "PREPARING",
        "READY_TO_APPLY", "APPROVAL_REQUIRED", "APPLIED", "RECRUITER_REPLIED",
        "SCREEN", "TECHNICAL", "HM", "FINAL", "OFFER", "ACCEPTED", "REJECTED", "WITHDRAWN",
    ]


def test_installed_version_comes_from_metadata_and_matches_pyproject() -> None:
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert careeros.__version__ == pyproject["project"]["version"] == versions.installed_version()
    assert versions.SCHEMA_VERSION == 1


def test_clock_fixture_controls_utc_now(clock) -> None:
    first, second = models.utc_now(), models.utc_now()
    assert first == "2026-10-06T09:00:01Z"
    assert second == "2026-10-06T09:00:02Z"

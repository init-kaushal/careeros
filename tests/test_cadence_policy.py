import pytest
from pydantic import ValidationError

from careeros.core.models import CadencePolicy
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    policy = CadencePolicy(days_between_touches=7, max_touches=4, max_follow_ups_per_run=3)
    policy.save(storage)
    loaded = CadencePolicy.load(storage)
    assert loaded.days_between_touches == 7
    assert loaded.max_touches == 4
    assert loaded.max_follow_ups_per_run == 3


def test_load_missing_file_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        CadencePolicy.load(storage)


def test_rejects_days_between_touches_at_zero():
    with pytest.raises(ValidationError):
        CadencePolicy(days_between_touches=0, max_touches=4, max_follow_ups_per_run=3)


def test_rejects_days_between_touches_above_ninety():
    with pytest.raises(ValidationError):
        CadencePolicy(days_between_touches=91, max_touches=4, max_follow_ups_per_run=3)


def test_rejects_max_touches_at_zero():
    with pytest.raises(ValidationError):
        CadencePolicy(days_between_touches=7, max_touches=0, max_follow_ups_per_run=3)


def test_rejects_max_touches_above_ten():
    with pytest.raises(ValidationError):
        CadencePolicy(days_between_touches=7, max_touches=11, max_follow_ups_per_run=3)


def test_rejects_max_follow_ups_per_run_at_zero():
    with pytest.raises(ValidationError):
        CadencePolicy(days_between_touches=7, max_touches=4, max_follow_ups_per_run=0)


def test_rejects_max_follow_ups_per_run_above_twenty():
    with pytest.raises(ValidationError):
        CadencePolicy(days_between_touches=7, max_touches=4, max_follow_ups_per_run=21)


def test_loads_a_thirteen_a_era_file_and_defaults_the_connection_cap(tmp_path):
    # A cadence_policy.json written during Phase 13a has exactly the three
    # fields that existed then. CadencePolicy.load deliberately raises when
    # the file is absent, so the new field must be defaulted rather than
    # required: a required fourth field would turn every already-written
    # policy file into a load failure.
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    storage.atomic_write(
        "config/cadence_policy.json",
        b'{"days_between_touches": 7, "max_touches": 4, "max_follow_ups_per_run": 3}',
    )
    loaded = CadencePolicy.load(storage)
    assert loaded.max_connection_requests_per_run == 5


def test_round_trip_preserves_max_connection_requests_per_run(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    CadencePolicy(
        days_between_touches=7, max_touches=4, max_follow_ups_per_run=3,
        max_connection_requests_per_run=2,
    ).save(storage)
    assert CadencePolicy.load(storage).max_connection_requests_per_run == 2


def test_rejects_max_connection_requests_per_run_at_zero():
    with pytest.raises(ValidationError):
        CadencePolicy(
            days_between_touches=7, max_touches=4, max_follow_ups_per_run=3,
            max_connection_requests_per_run=0,
        )


def test_rejects_max_connection_requests_per_run_above_twenty():
    with pytest.raises(ValidationError):
        CadencePolicy(
            days_between_touches=7, max_touches=4, max_follow_ups_per_run=3,
            max_connection_requests_per_run=21,
        )

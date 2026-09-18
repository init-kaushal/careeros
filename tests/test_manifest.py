import json
import pytest
from careeros.workspace.manifest import (
    Manifest,
    CAREEROS_VERSION,
    SUPPORTED_SCHEMA_VERSION,
    check_schema_compatibility,
    UnsupportedSchemaVersion,
)


def test_create_new_has_expected_fields():
    m = Manifest.create_new()
    assert m.careeros_version == CAREEROS_VERSION
    assert m.schema_version == SUPPORTED_SCHEMA_VERSION
    assert m.storage_type == "local"
    assert m.migrations_applied == []


def test_create_new_generates_unique_ids():
    m1 = Manifest.create_new()
    m2 = Manifest.create_new()
    assert m1.workspace_id != m2.workspace_id


def test_to_json_is_valid_json():
    m = Manifest.create_new()
    data = json.loads(m.to_json())
    assert data["schema_version"] == SUPPORTED_SCHEMA_VERSION


def test_round_trip():
    m = Manifest.create_new()
    m.migrations_applied = ["001_initial"]
    restored = Manifest.from_json(m.to_json())
    assert restored.workspace_id == m.workspace_id
    assert restored.migrations_applied == ["001_initial"]


def test_compatible_schema_does_not_raise():
    m = Manifest.create_new()
    check_schema_compatibility(m)  # must not raise


def test_newer_schema_raises():
    m = Manifest.create_new()
    m.schema_version = "999"
    with pytest.raises(UnsupportedSchemaVersion, match="999"):
        check_schema_compatibility(m)


def test_no_pii_in_manifest_fields():
    m = Manifest.create_new()
    data = json.loads(m.to_json())
    assert "name" not in data
    assert "email" not in data
    assert "resume" not in data

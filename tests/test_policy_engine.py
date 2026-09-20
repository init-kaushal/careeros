from datetime import datetime, timezone

import pytest

from careeros.core.models import Job, PolicyConfig
from careeros.core.policy_engine import PolicyEngine
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _make_job(company="Acme", salary_min=None, location=None):
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id="acme-sre-abc1", source="browse", company=company, title="Senior SRE",
        salary_min=salary_min, location=location, stage="saved",
        created_at=now, updated_at=now,
    )


class TestPolicyConfigPersistence:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        config = PolicyConfig.load(storage)
        assert config.blocked_companies == []
        assert config.min_salary is None
        assert config.blocked_locations == []

    def test_save_and_load_round_trip(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        init_workspace(storage)
        config = PolicyConfig(blocked_companies=["Bad Co"], min_salary=150000, blocked_locations=["Antarctica"])
        config.save(storage)
        loaded = PolicyConfig.load(storage)
        assert loaded.blocked_companies == ["Bad Co"]
        assert loaded.min_salary == 150000
        assert loaded.blocked_locations == ["Antarctica"]

    def test_load_malformed_json_raises(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        init_workspace(storage)
        storage.atomic_write("config/policies.json", b"not valid json")
        with pytest.raises(Exception):
            PolicyConfig.load(storage)


class TestPolicyEngineBlockedCompanies:
    def test_blocks_case_insensitive_company_match(self):
        engine = PolicyEngine(PolicyConfig(blocked_companies=["acme"]))
        result = engine.check_job(_make_job(company="Acme"))
        assert result.blocked is True
        assert result.rule == "blocked_company:acme"

    def test_does_not_block_different_company(self):
        engine = PolicyEngine(PolicyConfig(blocked_companies=["Acme"]))
        result = engine.check_job(_make_job(company="Beta"))
        assert result.blocked is False
        assert result.rule is None


class TestPolicyEngineMinSalary:
    def test_blocks_salary_below_floor(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=100000))
        assert result.blocked is True
        assert result.rule == "min_salary"

    def test_does_not_block_salary_at_floor(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=150000))
        assert result.blocked is False

    def test_does_not_block_salary_above_floor(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=200000))
        assert result.blocked is False

    def test_does_not_block_when_job_salary_missing(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=None))
        assert result.blocked is False

    def test_does_not_block_when_no_min_salary_configured(self):
        engine = PolicyEngine(PolicyConfig())
        result = engine.check_job(_make_job(salary_min=1))
        assert result.blocked is False


class TestPolicyEngineBlockedLocations:
    def test_blocks_substring_match_case_insensitive(self):
        engine = PolicyEngine(PolicyConfig(blocked_locations=["antarctica"]))
        result = engine.check_job(_make_job(location="Remote, Antarctica"))
        assert result.blocked is True
        assert result.rule == "blocked_location:antarctica"

    def test_does_not_block_when_location_missing(self):
        engine = PolicyEngine(PolicyConfig(blocked_locations=["Antarctica"]))
        result = engine.check_job(_make_job(location=None))
        assert result.blocked is False

    def test_does_not_block_non_matching_location(self):
        engine = PolicyEngine(PolicyConfig(blocked_locations=["Antarctica"]))
        result = engine.check_job(_make_job(location="San Francisco, CA"))
        assert result.blocked is False


class TestPolicyEngineNoRules:
    def test_no_rules_configured_never_blocks(self):
        engine = PolicyEngine(PolicyConfig())
        result = engine.check_job(_make_job(company="Anything", salary_min=1, location="Anywhere"))
        assert result.blocked is False
        assert result.rule is None


class TestPolicyEngineRuleOrdering:
    def test_blocked_company_checked_before_salary_and_location(self):
        engine = PolicyEngine(PolicyConfig(
            blocked_companies=["Acme"], min_salary=150000, blocked_locations=["SF"],
        ))
        result = engine.check_job(_make_job(company="Acme", salary_min=1, location="SF"))
        assert result.rule == "blocked_company:Acme"

    def test_min_salary_checked_before_location_when_company_not_blocked(self):
        engine = PolicyEngine(PolicyConfig(
            blocked_companies=["Other"], min_salary=150000, blocked_locations=["SF"],
        ))
        result = engine.check_job(_make_job(company="Acme", salary_min=1, location="SF"))
        assert result.rule == "min_salary"

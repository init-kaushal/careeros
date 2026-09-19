from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.research_cmd import research_app
from careeros.core.ids import make_company_id, make_person_id
from careeros.core.models import Company, CompensationDataPoint, Job, Person, Preferences
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _setup_workspace(tmp_path, with_job=True):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    if with_job:
        now = datetime.now(timezone.utc).isoformat()
        Job(
            id="acme-sre-abc1", source="browse", url="https://example.com/job",
            company="Acme Corp", title="Senior SRE", stage="saved",
            created_at=now, updated_at=now,
        ).save(storage)
    return str(tmp_path)


class TestResearchCompany:
    def test_job_not_found_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_job=False)
        result = runner.invoke(research_app, ["company", "--job", "nonexistent", "--workspace", ws_path])
        assert result.exit_code == 1

    def test_researches_and_saves_company(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.fetch_jd_text", return_value="Acme Corp page content"), \
             patch("careeros.cli.research_cmd.extract_company_info", return_value={"industry": "Software", "size": "51-200", "notes": "Series B"}):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["company", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        company_id = make_company_id("Acme Corp")
        company = Company.load(storage, company_id)
        assert company.industry == "Software"
        assert company.size == "51-200"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "company_researched" in log_content


class TestResearchPeople:
    def test_job_not_found_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_job=False)
        result = runner.invoke(research_app, ["people", "--job", "nonexistent", "--workspace", ws_path])
        assert result.exit_code == 1

    def test_researches_and_saves_people(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [
            {"name": "Jane Doe", "title": "Engineering Manager", "linkedin_url": "https://linkedin.com/in/jane"},
        ]
        with patch("careeros.cli.research_cmd.PeopleSearchScraper", return_value=mock_scraper), \
             patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.classify_person_role", return_value="em"):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["people", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        company_id = make_company_id("Acme Corp")
        person = Person.load(storage, make_person_id("Jane Doe", company_id))
        assert person.role_category == "em"
        assert person.title == "Engineering Manager"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "people_researched" in log_content


class TestResearchCompensation:
    def test_job_not_found_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_job=False)
        result = runner.invoke(research_app, ["compensation", "--job", "nonexistent", "--workspace", ws_path])
        assert result.exit_code == 1

    def test_researches_and_saves_compensation(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        Preferences(seniority="senior").save(storage)

        with patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.fetch_jd_text", return_value="Senior SRE at Acme Corp: $180K-$220K"), \
             patch("careeros.cli.research_cmd.extract_compensation_data", return_value={
                 "base_min": 180000, "base_max": 220000, "bonus": "10%", "equity": "0.02%", "confidence": "medium",
             }):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["compensation", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        # Find the saved CompensationDataPoint by scanning compensation/ (ID has a random suffix)
        comp_files = [p for p in storage.list("compensation/") if p.endswith(".json")]
        assert len(comp_files) == 1
        point = CompensationDataPoint.model_validate_json(storage.read(comp_files[0]).decode())
        assert point.base_min == 180000
        assert point.seniority == "senior"
        assert point.confidence == "medium"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "compensation_researched" in log_content

    def test_thin_page_content_saves_low_confidence(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.research_cmd.extract_compensation_data", return_value={
                 "base_min": None, "base_max": None, "bonus": None, "equity": None, "confidence": "low",
             }):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["compensation", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        comp_files = [p for p in storage.list("compensation/") if p.endswith(".json")]
        assert len(comp_files) == 1
        point = CompensationDataPoint.model_validate_json(storage.read(comp_files[0]).decode())
        assert point.confidence == "low"
        assert point.base_min is None

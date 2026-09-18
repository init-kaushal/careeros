import re
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestLinkedInScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.linkedin import LinkedInScraper
        scraper = LinkedInScraper()
        results = scraper.parse_listings(_read("linkedin_results.html"))
        assert len(results) == 2
        assert results[0]["source_board"] == "linkedin"
        assert results[0]["title"] == "Senior SRE"
        assert results[0]["company"] == "Acme Corp"
        assert results[0]["location"] == "San Francisco, CA"
        assert "linkedin.com/jobs/view/111" in results[0]["url"]

    def test_parse_listings_empty_html_returns_empty_list(self):
        from careeros.browser.scrapers.linkedin import LinkedInScraper
        scraper = LinkedInScraper()
        assert scraper.parse_listings("<html><body></body></html>") == []

    def test_parse_listings_second_result(self):
        from careeros.browser.scrapers.linkedin import LinkedInScraper
        scraper = LinkedInScraper()
        results = scraper.parse_listings(_read("linkedin_results.html"))
        assert results[1]["title"] == "Platform Engineer"
        assert results[1]["company"] == "Beta Inc"


class TestIndeedScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.indeed import IndeedScraper
        scraper = IndeedScraper()
        results = scraper.parse_listings(_read("indeed_results.html"))
        assert len(results) == 2
        assert results[0]["source_board"] == "indeed"
        assert results[0]["title"] == "Senior SRE"
        assert results[0]["company"] == "Acme Corp"
        assert results[0]["location"] == "San Francisco, CA"
        assert "indeed.com" in results[0]["url"] and "jk=" in results[0]["url"]

    def test_parse_listings_empty_html_returns_empty_list(self):
        from careeros.browser.scrapers.indeed import IndeedScraper
        assert IndeedScraper().parse_listings("<div></div>") == []


class TestWellfoundScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.wellfound import WellfoundScraper
        scraper = WellfoundScraper()
        results = scraper.parse_listings(_read("wellfound_results.html"))
        assert len(results) == 2
        assert results[0]["source_board"] == "wellfound"
        assert results[0]["title"] == "Senior SRE"
        assert results[0]["company"] == "Acme Corp"
        assert "wellfound.com" in results[0]["url"]


class TestGenericScraper:
    def test_parse_listings_extracts_job_pattern_hrefs(self):
        from careeros.browser.scrapers.generic import GenericScraper
        scraper = GenericScraper()
        results = scraper.parse_listings(_read("generic_jobs_page.html"))
        urls = [r["url"] for r in results]
        assert any("/jobs/" in u for u in urls)
        assert any("/careers/" in u for u in urls)
        assert all(r["source_board"] == "generic" for r in results)

    def test_parse_listings_ignores_non_job_links(self):
        from careeros.browser.scrapers.generic import GenericScraper
        scraper = GenericScraper()
        results = scraper.parse_listings(_read("generic_jobs_page.html"))
        urls = [r["url"] for r in results]
        assert not any("/about" in u for u in urls)
        assert not any("/contact" in u for u in urls)

    def test_parse_listings_empty_page_returns_empty_list(self):
        from careeros.browser.scrapers.generic import GenericScraper
        html = "<html><body><a href='/about'>About</a></body></html>"
        assert GenericScraper().parse_listings(html) == []

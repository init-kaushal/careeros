from pathlib import Path
from unittest.mock import MagicMock

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

    def test_search_resolves_relative_url_to_absolute(self):
        from careeros.browser.scrapers.linkedin import LinkedInScraper
        scraper = LinkedInScraper()
        page = MagicMock()
        page.url = "https://www.linkedin.com/jobs/search/?keywords=sre"

        link = MagicMock()
        link.inner_text.return_value = "Senior SRE"
        link.get_attribute.return_value = "/jobs/view/123"

        company_locator = MagicMock()
        company_locator.first.inner_text.return_value = "Acme"

        loc_locator = MagicMock()
        loc_locator.first.count.return_value = 0

        def card_locator(selector):
            if "link" in selector:
                m = MagicMock()
                m.first = link
                return m
            if "company-name" in selector:
                return company_locator
            if "metadata-item" in selector:
                return loc_locator
            return MagicMock()

        card = MagicMock()
        card.locator.side_effect = card_locator
        page.locator.return_value.all.return_value = [card]

        results = scraper.search(page, "sre", 1)
        assert results[0]["url"] == "https://www.linkedin.com/jobs/view/123"


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

    def test_search_resolves_relative_url_to_absolute(self):
        from careeros.browser.scrapers.wellfound import WellfoundScraper
        scraper = WellfoundScraper()
        page = MagicMock()
        page.url = "https://wellfound.com/jobs?q=sre"

        link = MagicMock()
        link.inner_text.return_value = "Senior SRE"
        link.get_attribute.return_value = "/jobs/42"

        company_locator = MagicMock()
        company_locator.first.inner_text.return_value = "Acme"

        loc_locator = MagicMock()
        loc_locator.first.count.return_value = 0

        def card_locator(selector):
            if "title" in selector:
                m = MagicMock()
                m.first = link
                return m
            if "company" in selector:
                return company_locator
            if "location" in selector:
                return loc_locator
            return MagicMock()

        card = MagicMock()
        card.locator.side_effect = card_locator
        page.locator.return_value.all.return_value = [card]

        results = scraper.search(page, "sre", 1)
        assert results[0]["url"] == "https://wellfound.com/jobs/42"

    def test_search_terminates_when_extraction_always_fails_on_growing_dom(self):
        # Regression for an unbounded loop: if every card's extraction raises
        # (e.g. markup changed) while the DOM keeps reporting more cards on
        # each scroll, the loop must still terminate via the page cap.
        from careeros.browser.scrapers.wellfound import WellfoundScraper
        scraper = WellfoundScraper()
        page = MagicMock()
        page.url = "https://wellfound.com/jobs?q=sre"

        broken_card = MagicMock()
        broken_card.locator.side_effect = Exception("markup changed")

        call_count = {"n": 0}

        def growing_cards():
            call_count["n"] += 1
            return [broken_card] * call_count["n"]

        page.locator.return_value.all.side_effect = growing_cards

        results = scraper.search(page, "sre", limit=100)
        assert results == []
        # bounded by _MAX_PAGES, not by limit or by lack of DOM growth
        assert call_count["n"] < 1000


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

    def test_search_resolves_relative_url_to_absolute(self):
        from careeros.browser.scrapers.generic import GenericScraper
        scraper = GenericScraper()
        page = MagicMock()
        page.url = "https://example.com/careers"
        page.content.return_value = '<a href="/jobs/eng-42">Engineer</a>'
        results = scraper.search(page, "https://example.com/careers", 10)
        assert results[0]["url"] == "https://example.com/jobs/eng-42"

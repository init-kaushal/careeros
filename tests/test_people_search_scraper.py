from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestPeopleSearchScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        scraper = PeopleSearchScraper()
        results = scraper.parse_listings(_read("linkedin_people_results.html"))
        assert len(results) == 2
        assert results[0]["name"] == "Jane Doe"
        assert results[0]["title"] == "Engineering Manager at Acme Corp"
        assert "linkedin.com/in/jane-doe-123" in results[0]["linkedin_url"]

    def test_parse_listings_second_result(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        scraper = PeopleSearchScraper()
        results = scraper.parse_listings(_read("linkedin_people_results.html"))
        assert results[1]["name"] == "John Smith"
        assert results[1]["title"] == "Technical Recruiter at Acme Corp"

    def test_parse_listings_empty_html_returns_empty_list(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        scraper = PeopleSearchScraper()
        assert scraper.parse_listings("<html><body></body></html>") == []

    def test_source_board_attribute(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        assert PeopleSearchScraper().source_board == "linkedin_people"

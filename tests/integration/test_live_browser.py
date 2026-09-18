import pytest


@pytest.mark.integration
def test_linkedin_search_returns_results():
    """Requires Chrome with active LinkedIn session. Run: pytest -m integration"""
    from careeros.browser.driver import launch_browser
    from careeros.browser.scrapers.linkedin import LinkedInScraper
    scraper = LinkedInScraper()
    with launch_browser(headless=False) as (_, page):
        results = scraper.search(page, "site reliability engineer", limit=5)
    assert len(results) >= 1
    assert all(r["source_board"] == "linkedin" for r in results)
    assert all(r["title"] for r in results)
    assert all(r["url"].startswith("https://") for r in results)


@pytest.mark.integration
def test_indeed_search_returns_results():
    """Requires Chrome with active Indeed session. Run: pytest -m integration"""
    from careeros.browser.driver import launch_browser
    from careeros.browser.scrapers.indeed import IndeedScraper
    scraper = IndeedScraper()
    with launch_browser(headless=False) as (_, page):
        results = scraper.search(page, "site reliability engineer", limit=5)
    assert len(results) >= 1
    assert all(r["source_board"] == "indeed" for r in results)
    assert all(r["title"] for r in results)
    assert all(r["url"].startswith("https://") for r in results)

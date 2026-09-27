from careeros.browser.boards import BOARDS, BOARD_NAMES, Board


def test_registry_covers_browser_boards():
    assert set(BOARDS) == {"linkedin", "indeed", "wellfound"}


def test_board_names_matches_registry_keys_in_order():
    assert BOARD_NAMES == tuple(BOARDS)


def test_every_board_is_fully_populated():
    for name, board in BOARDS.items():
        assert isinstance(board, Board)
        assert board.name == name
        assert board.login_url.startswith("https://")
        assert board.session_cookie
        assert board.cookie_domain.startswith("https://")


def test_every_scraper_source_board_matches_its_key():
    # Guards the failure this registry exists to prevent: a board that is
    # scrapeable under one name but authorizable under another.
    for name, board in BOARDS.items():
        assert board.scraper.source_board == name


def test_board_is_immutable():
    import dataclasses
    import pytest
    board = BOARDS["linkedin"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        board.session_cookie = "tampered"


def test_registry_matches_the_automation_board_whitelist():
    from careeros.core.models import _VALID_AUTOMATION_BOARDS
    assert _VALID_AUTOMATION_BOARDS == ("linkedin", "indeed", "wellfound")
    assert _VALID_AUTOMATION_BOARDS == BOARD_NAMES

"""Opt-in tests that use real external resources.

Everything in this package is marked `integration` and is skipped by the
default `pytest` run. Passing `-m integration` selects them, and doing so also
disarms the autouse SMTP and browser guards in `tests/conftest.py` — that is
the point of the marker, but it means these tests really do reach the outside
world. Know what you are asking for before you type it:

- `test_live_browser.py` opens a **visible** real Chrome window against the
  isolated CareerOS profile and runs live LinkedIn and Indeed searches. It
  gates on nothing but the marker, so it runs as soon as you opt in, and it
  needs `careeros browser login --board <name>` to have been run first.
- `test_live_apply.py`'s two `can_handle` tests touch no network. Its two
  `*_fill_live` tests **fill a real application form** and gate themselves on
  `CAREEROS_TEST_GREENHOUSE_URL` / `CAREEROS_TEST_LEVER_URL`, skipping unless
  you name a specific real job URL — so `-m integration` alone cannot submit
  an application.
- `tests/test_resume_pdf.py::test_real_chromium_renders_a_pdf` launches an
  ephemeral headless chromium with no persistent profile and no navigation.

Until 2026-09-23 this package was unreachable: the collection hook in
`tests/conftest.py` skipped every `integration`-marked item unconditionally,
including under the `-m integration` its own skip reason advertised. That
broken gate was quietly load-bearing for safety, since it was the only thing
standing between a casual run and a real form submission. It is fixed, and the
safety now rests on the marker plus the per-test URL gates above rather than
on a bug.
"""

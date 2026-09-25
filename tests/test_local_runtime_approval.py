"""The approval prompt must ask about exactly what it is approving.

`LocalRuntime.request_approval` is the terminal confirmation gate behind
`outreach send`, `apply` and `outreach connect`. Its summary is built from
scraped data — a person's name, a company, a job title — and it used to hand
that raw string to `Confirm.ask`, which renders with Rich console markup
enabled. Two things followed, and the quieter one is the worse one:

- a bracketed span was silently *deleted* from the prompt, so a person
  recorded as "Jane [dim]Doe" produced a question about "Jane Doe" and the
  user approved a summary that did not match the record; and
- a closing-tag-shaped span raised `MarkupError` from inside the prompt,
  taking all three commands down before the question was asked.

This is the same defect class Phase 13a fixed on the follow-up review panel
and the cover-letter panel. This was the last instance, and the one sitting
directly on the approval gate.
"""
import io

import pytest
from rich.console import Console
from rich.prompt import Confirm

from careeros.runtime.base import ActionProposal


def _ask(summary: str, monkeypatch) -> str:
    """Run the real request_approval body against a recording console."""
    from careeros.runtime.local import LocalRuntime

    console = Console(file=io.StringIO(), width=200)
    captured = {}

    real_ask = Confirm.ask

    def recording_ask(prompt, **kwargs):
        captured["prompt"] = prompt
        kwargs["console"] = console
        kwargs["stream"] = io.StringIO("")
        return real_ask(prompt, **kwargs)

    monkeypatch.setattr("careeros.runtime.local.Confirm.ask", staticmethod(recording_ask))
    runtime = LocalRuntime.__new__(LocalRuntime)  # no workspace needed for this path
    runtime.request_approval(ActionProposal(action="send_connection_request", summary=summary))
    return console.file.getvalue()


@pytest.mark.parametrize(
    "fragment",
    ["[dim]", "[/b]", "[bold]", "[link=x]"],
    ids=["deleted-span", "closing-tag", "open-tag", "link-tag"],
)
def test_a_bracketed_summary_reaches_the_prompt_intact(fragment, monkeypatch):
    summary = "Send connection request to Jane " + fragment + " Doe at Acme?"

    rendered = _ask(summary, monkeypatch)

    # The characters themselves must survive. Asserting a bracket-free
    # substring is what gave false confidence about the equivalent panel bug.
    assert fragment in rendered, fragment + " was eaten by rich markup"
    assert "Jane" in rendered and "Doe" in rendered

from __future__ import annotations

from rich.text import Text


def verbatim(value: str) -> Text:
    """Wrap text that must reach the screen unaltered.

    Rich console markup is on by default, so a bracketed span in a string
    handed to console.print/Panel is *interpreted*: it is either deleted
    from the display or, if it looks like a closing tag, raises
    rich.errors.MarkupError. Neither is acceptable for anything derived
    from an LLM draft, a scraped job title, or a researched person's name,
    and both were happening here. A draft reading "See the
    [posting](https://x.com/job) I mentioned" displayed as "See the
    (https://x.com/job) I mentioned" while execute_* emailed the raw
    stored bytes — which destroys the only safety argument these review
    loops have, that the bytes on screen are the bytes transmitted. And a
    draft containing "[/b]" raised MarkupError, which is not an
    OperationError, so it escaped `review`'s per-item guard and abandoned
    the rest of the queue with a traceback and no summary.

    A rich Text instance carries no markup by definition, so passing one
    is what makes the rendered characters the source characters. Applied
    to panel titles as well as bodies: a title is built from job.company,
    job.title and person.name, all of which are external data this project
    never authored.
    """
    return Text(value)


def verbatim_panel_args(body: str, title: str) -> tuple[Text, Text]:
    """The two arguments a review Panel needs, both unaltered.

    A convenience so a call site cannot remember the body and forget the
    title: the title is built from a scraped company and job title, and a
    closing-tag-shaped span in either one raises identically.
    """
    return verbatim(body), verbatim(title)

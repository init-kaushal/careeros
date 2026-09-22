from __future__ import annotations

import html

from careeros.core.models import Profile, ResumeVariant

# Inline only. An external stylesheet or font would make rendering depend on
# the network, and render_pdf deliberately never loads a URL.
_CSS = """
@page { size: Letter; margin: 0; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt;
       line-height: 1.4; color: #111; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 2pt 0; letter-spacing: 0.3pt; }
p.contact { font-size: 9.5pt; color: #444; margin: 0 0 14pt 0; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: 0.6pt;
     border-bottom: 1px solid #999; padding-bottom: 2pt;
     margin: 14pt 0 6pt 0; }
ul { margin: 0; padding-left: 16pt; }
li { margin-bottom: 4pt; }
"""


def _esc(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def build_resume_html(variant: ResumeVariant, profile: Profile) -> str:
    """Render a variant to a self-contained HTML document.

    Every quote and profile field is escaped: verbatim resume text becomes
    markup here, so text like "C++ & <legacy> tooling" must render as
    written rather than as broken or injected markup.

    The document carries no provenance annotations. Line numbers and the
    dropped list live in variant.json; this is what an employer sees.
    """
    contact = [b for b in (profile.title, profile.location, profile.email) if b]

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>" + _esc(profile.name or "Resume") + "</title>",
        "<style>" + _CSS + "</style>",
        "</head><body>",
        "<h1>" + _esc(profile.name) + "</h1>",
        '<p class="contact">' + " &middot; ".join(_esc(b) for b in contact) + "</p>",
    ]

    for section in variant.sections:
        if not section.entries:
            continue
        parts.append("<h2>" + _esc(section.heading) + "</h2>")
        parts.append("<ul>")
        for entry in section.entries:
            parts.append("<li>" + _esc(entry.quote) + "</li>")
        parts.append("</ul>")

    parts.append("</body></html>")
    return "\n".join(parts)

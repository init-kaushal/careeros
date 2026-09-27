import json
from careeros.llm import complete
from careeros.skills.sanitize import wrap_untrusted
_CONTENT_CAP = 4000

_EXTRACT_INSTRUCTIONS = """\
Extract structured facts about a company from the page content below.
Return ONLY valid JSON — no markdown, no explanation.

{
  "industry": "<industry, or null if unknown>",
  "size": "<employee count range, e.g. '51-200', or null if unknown>",
  "notes": "<1-2 sentences of other relevant context, or null>"
}
"""

_FAILURE = {"industry": None, "size": None, "notes": None}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_company_info(page_content: str, model: str | None = None) -> dict:
    user_text = "Page content:\n" + wrap_untrusted(page_content[:_CONTENT_CAP])
    try:
        resp = complete(
            model=model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": _EXTRACT_INSTRUCTIONS},
                {"role": "user", "content": user_text},
            ],
        )
        result = _parse_json(resp.choices[0].message.content)
        return {
            "industry": result.get("industry"),
            "size": result.get("size"),
            "notes": result.get("notes"),
        }
    except Exception:
        return dict(_FAILURE)

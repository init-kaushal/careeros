import json
import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_CONTENT_CAP = 4000

_EXTRACT_INSTRUCTIONS = """\
Extract structured facts about a company from the page content below.
Return ONLY valid JSON — no markdown, no explanation.

{
  "industry": "<industry, or null if unknown>",
  "size": "<employee count range, e.g. '51-200', or null if unknown>",
  "notes": "<1-2 sentences of other relevant context, or null>"
}

Page content:
"""

_FAILURE = {"industry": None, "size": None, "notes": None}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_company_info(page_content: str, model: str | None = None) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    prompt = _EXTRACT_INSTRUCTIONS + page_content[:_CONTENT_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.choices[0].message.content)
        return {
            "industry": result.get("industry"),
            "size": result.get("size"),
            "notes": result.get("notes"),
        }
    except Exception:
        return dict(_FAILURE)

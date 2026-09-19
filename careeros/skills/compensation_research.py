import json
import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_CONTENT_CAP = 4000

_EXTRACT_INSTRUCTIONS = """\
Extract structured compensation facts from the page content below, which was
fetched from a public salary-data page for a specific company and role.
If the page content is empty, too thin, or doesn't contain real compensation
figures, return all numeric/text fields as null and confidence as "low" —
never invent a plausible-sounding number.
Return ONLY valid JSON — no markdown, no explanation.

{
  "base_min": <integer base salary low end, or null>,
  "base_max": <integer base salary high end, or null>,
  "bonus": "<bonus description, e.g. '10-15%', or null>",
  "equity": "<equity description, e.g. '0.01-0.05%', or null>",
  "confidence": "<'low', 'medium', or 'high' based on how much real data was present>"
}

Page content:
"""

_FAILURE = {"base_min": None, "base_max": None, "bonus": None, "equity": None, "confidence": "low"}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_compensation_data(page_content: str, model: str | None = None) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    prompt = _EXTRACT_INSTRUCTIONS + page_content[:_CONTENT_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.choices[0].message.content)
        confidence = result.get("confidence")
        if confidence not in ("low", "medium", "high"):
            confidence = "low"
        return {
            "base_min": result.get("base_min"),
            "base_max": result.get("base_max"),
            "bonus": result.get("bonus"),
            "equity": result.get("equity"),
            "confidence": confidence,
        }
    except Exception:
        return dict(_FAILURE)

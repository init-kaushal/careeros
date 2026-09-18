import json
import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

_JD_INSTRUCTIONS = """\
Extract the following fields from this job description as JSON.
Return ONLY valid JSON — no markdown, no explanation.
Use null for any field not found.

{
  "company": "<company name or null>",
  "title": "<job title or null>",
  "location": "<city/region or null>",
  "remote": <true | false | null>,
  "salary_min": <integer annual salary minimum or null>,
  "salary_max": <integer annual salary maximum or null>,
  "currency": "<USD | GBP | EUR | AUD | null>",
  "requirements": ["<requirement>", "..."],
  "summary": "<1-2 sentence job summary or null>"
}

Job description:
"""

_JD_CAP = 4000


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_job_fields(jd_text: str, model: str | None = None) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    truncated = jd_text[:_JD_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": _JD_INSTRUCTIONS + truncated}],
        )
        return _parse_json(resp.choices[0].message.content)
    except Exception:
        return {}

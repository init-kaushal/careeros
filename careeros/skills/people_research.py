import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_VALID_CATEGORIES = ("ic", "em", "recruiter", "hiring_manager")

_CLASSIFY_INSTRUCTIONS = """\
Classify this person's role at their company into exactly one category:
ic, em, recruiter, or hiring_manager.
Return ONLY the category word, nothing else.

Name: """


def classify_person_role(name: str, title: str, model: str | None = None) -> str:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    prompt = _CLASSIFY_INSTRUCTIONS + name + "\nTitle: " + title
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=16,
            messages=[{"role": "user", "content": prompt}],
        )
        category = resp.choices[0].message.content.strip().lower()
        if category in _VALID_CATEGORIES:
            return category
        return "ic"
    except Exception:
        return "ic"

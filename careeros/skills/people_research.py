from careeros.llm import complete
from careeros.skills.sanitize import wrap_untrusted
_VALID_CATEGORIES = ("ic", "em", "recruiter", "hiring_manager")

_CLASSIFY_INSTRUCTIONS = """\
Classify this person's role at their company into exactly one category:
ic, em, recruiter, or hiring_manager.
Return ONLY the category word, nothing else.
"""


def classify_person_role(name: str, title: str, model: str | None = None) -> str:
    user_text = wrap_untrusted("Name: " + name + "\nTitle: " + title)
    try:
        resp = complete(
            model=model,
            max_tokens=16,
            messages=[
                {"role": "system", "content": _CLASSIFY_INSTRUCTIONS},
                {"role": "user", "content": user_text},
            ],
        )
        category = resp.choices[0].message.content.strip().lower()
        if category in _VALID_CATEGORIES:
            return category
        return "ic"
    except Exception:
        return "ic"

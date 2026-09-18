import json
import urllib.error
import urllib.request
from html.parser import HTMLParser


class ATSFetchError(Exception):
    pass


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def _get_json(url: str) -> object:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "careeros/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ATSFetchError("not_found")
        raise ATSFetchError(str(e))
    except urllib.error.URLError as e:
        raise ATSFetchError(str(e))
    except json.JSONDecodeError as e:
        raise ATSFetchError(f"malformed JSON: {e}")


_DESC_CAP = 4000
_GH_BASE = "https://boards-api.greenhouse.io/v1/boards"
_LEVER_BASE = "https://api.lever.co/v0/postings"


def fetch_greenhouse(company: str) -> list[dict]:
    data = _get_json(f"{_GH_BASE}/{company}/jobs?content=true")
    result = []
    for job in data.get("jobs", []):
        loc = job.get("location") or {}
        raw = job.get("content") or ""
        result.append({
            "source_id": str(job["id"]),
            "title": job["title"],
            "url": job["absolute_url"],
            "location": loc.get("name"),
            "description": _strip_html(raw)[:_DESC_CAP],
        })
    return result


def fetch_lever(company: str) -> list[dict]:
    data = _get_json(f"{_LEVER_BASE}/{company}?mode=json")
    result = []
    for posting in data:
        cats = posting.get("categories") or {}
        desc = posting.get("descriptionPlain") or ""
        result.append({
            "source_id": posting["id"],
            "title": posting["text"],
            "url": posting["hostedUrl"],
            "location": cats.get("location"),
            "description": desc[:_DESC_CAP],
        })
    return result

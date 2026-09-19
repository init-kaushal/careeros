from pydantic import BaseModel
from careeros.storage.interface import StorageProvider


class Profile(BaseModel):
    name: str = ""
    email: str | None = None
    title: str | None = None
    years_of_experience: int | None = None
    location: str | None = None
    summary: str | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/profile.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider) -> "Profile":
        if not storage.exists("profile/profile.json"):
            raise FileNotFoundError("profile/profile.json not found in workspace")
        return cls.model_validate_json(storage.read("profile/profile.json").decode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Profile":
        if not storage.exists("profile/profile.json"):
            return cls()
        return cls.load(storage)


class Skill(BaseModel):
    name: str
    category: str | None = None
    level: str | None = None
    years: int | None = None
    source: str | None = None
    last_used: str | None = None


class Skills(BaseModel):
    skills: list[Skill] = []

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/skills.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Skills":
        if not storage.exists("profile/skills.json"):
            return cls()
        return cls.model_validate_json(storage.read("profile/skills.json").decode())


class Preferences(BaseModel):
    target_roles: list[str] = []
    seniority: str | None = None
    locations: list[str] = []
    remote_preference: str | None = None
    industries: list[str] = []
    target_companies: list[str] = []
    minimum_compensation: int | None = None
    compensation_currency: str = "USD"
    employment_types: list[str] = []
    visa_sponsorship_required: bool = False
    notice_period_days: int | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/preferences.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Preferences":
        if not storage.exists("profile/preferences.json"):
            return cls()
        return cls.model_validate_json(storage.read("profile/preferences.json").decode())


class Goals(BaseModel):
    short_term: list[str] = []
    long_term: list[str] = []
    non_negotiables: list[str] = []
    open_to: list[str] = []

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/goals.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Goals":
        if not storage.exists("profile/goals.json"):
            return cls()
        return cls.model_validate_json(storage.read("profile/goals.json").decode())


JOB_STAGES = ("saved", "applied", "interviewing", "offer", "closed")

_JOB_DESC_CAP = 4000


class Job(BaseModel):
    id: str
    source: str
    source_id: str | None = None
    url: str | None = None
    company: str
    title: str
    location: str | None = None
    remote: bool | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "USD"
    description: str | None = None
    requirements: list[str] = []
    stage: str = "saved"
    applied_at: str | None = None
    notes: list[str] = []
    created_at: str
    updated_at: str

    def save(self, storage: StorageProvider) -> None:
        data = self
        if data.description and len(data.description) > _JOB_DESC_CAP:
            data = data.model_copy(update={"description": data.description[:_JOB_DESC_CAP]})
        storage.atomic_write(f"jobs/{self.id}.json", data.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, job_id: str) -> "Job":
        path = f"jobs/{job_id}.json"
        if not storage.exists(path):
            raise FileNotFoundError(f"Job {job_id!r} not found")
        return cls.model_validate_json(storage.read(path).decode())

    @classmethod
    def list_all(cls, storage: StorageProvider) -> list["Job"]:
        paths = [p for p in storage.list("jobs") if p.endswith(".json")]
        jobs = [cls.model_validate_json(storage.read(p).decode()) for p in paths]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)


class AutomationPolicy(BaseModel):
    auto_apply_min_score: int
    max_auto_applies_per_run: int
    boards: list[str]

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("config/automation_policy.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider) -> "AutomationPolicy":
        if not storage.exists("config/automation_policy.json"):
            raise FileNotFoundError("config/automation_policy.json not found in workspace")
        return cls.model_validate_json(storage.read("config/automation_policy.json").decode())


class Company(BaseModel):
    id: str
    name: str
    url: str | None = None
    industry: str | None = None
    size: str | None = None
    notes: str | None = None
    researched_at: str

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("companies/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, company_id: str) -> "Company":
        path = "companies/" + company_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Company " + repr(company_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())


class Person(BaseModel):
    id: str
    company_id: str
    name: str
    role_category: str
    title: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    researched_at: str

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("people/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, person_id: str) -> "Person":
        path = "people/" + person_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Person " + repr(person_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())


class OutreachMessage(BaseModel):
    id: str
    job_id: str
    person_id: str
    draft_text: str
    send_state: str = "drafted"
    referral_state: str = "research"
    created_at: str
    sent_at: str | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("outreach/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, message_id: str) -> "OutreachMessage":
        path = "outreach/" + message_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("OutreachMessage " + repr(message_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())

from pydantic import BaseModel, Field, field_validator
from careeros.browser.boards import BOARD_NAMES
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


class Evidence(BaseModel):
    """Records that `quote` appeared verbatim in `source_file` at `line`,
    as checked by careeros.skills.resume_evidence.verify_quote at ingest
    time — whitespace collapsed and case folded, nothing else.

    That is the entire guarantee. It is not a guarantee that the skill
    claim itself is accurate, nor that the source file still contains the
    quote now: the file may have changed since ingestion ran.
    """

    quote: str
    line: int
    source_file: str


class Skill(BaseModel):
    name: str
    category: str | None = None
    level: str | None = None
    years: int | None = None
    source: str | None = None
    last_used: str | None = None
    evidence: Evidence | None = None


class Skills(BaseModel):
    skills: list[Skill] = []

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/skills.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Skills":
        if not storage.exists("profile/skills.json"):
            return cls()
        return cls.model_validate_json(storage.read("profile/skills.json").decode())


class VariantSection(BaseModel):
    heading: str
    entries: list[Evidence] = []


class ResumeVariant(BaseModel):
    """A job-tailored resume assembled only from verbatim spans of the master.

    Every entry is an Evidence record whose quote verify_quote located in
    source_file, so each line of the rendered document traces to a line
    number in the master resume. `dropped` names what the model proposed
    that did not survive the guards.
    """

    job_id: str
    job_company: str = ""
    job_title: str = ""
    generated_at: str
    source_file: str
    sections: list[VariantSection] = []
    dropped: list[str] = []

    # Contact-header fields as rendered, recorded for provenance. These come
    # from profile.json, NOT from verified spans of the master resume, and are
    # the one part of the document not backed by an Evidence line.
    header: dict[str, str] = {}

    # sha256 of the rendered resume.pdf this sidecar describes, and of the master
    # resume it was generated from. A sidecar that does not match its PDF is stale
    # (a concurrent regeneration committed between the two writes); a master hash
    # that no longer matches means the citations point into a file that has since
    # been replaced. Both are detected at selection time rather than trusted.
    pdf_sha256: str = ""
    master_sha256: str = ""

    def entry_count(self) -> int:
        return sum(len(s.entries) for s in self.sections)

    # Path helpers live here so the command, the selector, and apply cannot
    # drift apart on where a variant is stored. Per-job subdirectories keep
    # tailored output out of the untailored fallback pool.
    @staticmethod
    def variant_dir(job_id: str) -> str:
        return "resumes/versions/" + job_id + "/"

    @staticmethod
    def pdf_path(job_id: str) -> str:
        return ResumeVariant.variant_dir(job_id) + "resume.pdf"

    @staticmethod
    def json_path(job_id: str) -> str:
        return ResumeVariant.variant_dir(job_id) + "variant.json"


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


class Sighting(BaseModel):
    """One place a posting was seen. Recorded when dedup merges a new
    sighting into an existing record."""

    source: str
    url: str | None = None
    seen_at: str


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
    sightings: list[Sighting] = []
    created_at: str
    updated_at: str

    def save(self, storage: StorageProvider) -> None:
        """Write this job, overwriting any record with the same id.

        For a NEW job use JobStore.save_new instead: it deduplicates against
        existing records, which this method deliberately does not do.
        """
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


_VALID_AUTOMATION_BOARDS = BOARD_NAMES


class AutomationPolicy(BaseModel):
    auto_apply_min_score: int = Field(ge=1, le=100)
    max_auto_applies_per_run: int = Field(ge=1, le=50)
    boards: list[str]

    @field_validator("boards")
    @classmethod
    def _validate_boards(cls, value: list[str]) -> list[str]:
        unknown = [b for b in value if b not in _VALID_AUTOMATION_BOARDS]
        if unknown:
            raise ValueError(
                "Unknown board(s): " + ", ".join(unknown)
                + ". Valid: " + ", ".join(_VALID_AUTOMATION_BOARDS)
            )
        return value

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("config/automation_policy.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider) -> "AutomationPolicy":
        if not storage.exists("config/automation_policy.json"):
            raise FileNotFoundError("config/automation_policy.json not found in workspace")
        return cls.model_validate_json(storage.read("config/automation_policy.json").decode())


class PolicyConfig(BaseModel):
    blocked_companies: list[str] = []
    min_salary: int | None = None
    blocked_locations: list[str] = []

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("config/policies.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider) -> "PolicyConfig":
        if not storage.exists("config/policies.json"):
            return cls()
        return cls.model_validate_json(storage.read("config/policies.json").decode())


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


class Approval(BaseModel):
    """A durable record of one approval-gated action, readable across processes.

    States: pending -> approved -> executed, with declined and superseded as
    two other terminal outcomes reachable only from pending. executed is
    terminal on the success path, but not always: execute_* marks the record
    executed before attempting its external action (closing the window where
    a crash or a race could let a second process act on a still-approved
    record), and moves it on to failed if that action then raises. So the
    fourth path out of pending is pending -> approved -> executed -> failed,
    not a direct approved -> failed edge. Either way, only `approved` may
    execute, and executing advances the record out of that state
    immediately, so one approval can never authorize two external actions.

    `payload` is an open string map on purpose: it holds only the identifiers
    and workspace-relative paths execute_* needs to find its inputs, which
    keeps this file a contract a third runtime can read without importing
    CareerOS. It never holds drafted content (there would be two sources of
    truth) and never holds absolute paths (the workspace is portable).
    """

    id: str
    action: str
    summary: str
    state: str = "pending"
    entity_type: str | None = None
    entity_id: str | None = None
    payload: dict[str, str] = Field(default_factory=dict)
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    reason: str | None = None
    executed_at: str | None = None
    detail: str | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write(
            "approvals/" + self.id + ".json", self.model_dump_json(indent=2).encode()
        )

    @classmethod
    def load(cls, storage: StorageProvider, approval_id: str) -> "Approval":
        path = "approvals/" + approval_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Approval " + repr(approval_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())


class CompensationDataPoint(BaseModel):
    id: str
    job_id: str
    role: str
    seniority: str | None = None
    geo: str | None = None
    company: str | None = None
    currency: str = "USD"
    base_min: int | None = None
    base_max: int | None = None
    bonus: str | None = None
    equity: str | None = None
    source: str = "levels.fyi"
    source_url: str | None = None
    confidence: str = "low"
    researched_at: str

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("compensation/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, data_point_id: str) -> "CompensationDataPoint":
        path = "compensation/" + data_point_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("CompensationDataPoint " + repr(data_point_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())

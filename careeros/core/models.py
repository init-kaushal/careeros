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

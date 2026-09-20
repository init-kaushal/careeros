from dataclasses import dataclass

from careeros.core.models import Job, PolicyConfig


@dataclass
class PolicyResult:
    blocked: bool
    rule: str | None = None


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self._config = config

    def check_job(self, job: Job) -> PolicyResult:
        for company in self._config.blocked_companies:
            if job.company.strip().lower() == company.strip().lower():
                return PolicyResult(blocked=True, rule="blocked_company:" + company)

        if self._config.min_salary is not None and job.salary_min is not None:
            if job.salary_min < self._config.min_salary:
                return PolicyResult(blocked=True, rule="min_salary")

        if job.location:
            location_lower = job.location.lower()
            for blocked in self._config.blocked_locations:
                if blocked.strip().lower() in location_lower:
                    return PolicyResult(blocked=True, rule="blocked_location:" + blocked)

        return PolicyResult(blocked=False)

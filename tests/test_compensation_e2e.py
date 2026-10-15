"""One end-to-end walk through structured compensation on invented data, through the real CLI: the cap, the stamp, foreign
currency, conflict, the write scope, the ledger events, isolation, and the 0.5.0 -> 0.6.0 migration including every
interruption point of the migration recovery table."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from helpers import assert_only_ledger_appended, snapshot, state
from test_evaluation_command import evl_events, run
from test_evaluation_compensation import FIXTURE, POSTED, V1_JOB
from test_evaluation_record import COMP_OBS, JD, agent_half, edit, load, make_job, write_agent, write_comp

from careeros.core import ledger
from careeros.core.compensation import record as comp_record
from careeros.core.workspace import join_frontmatter, split_frontmatter

PAY_LINE = "Pay: INR 2,500,000 to 3,000,000 per year (base)."
HEADING = "Migrated from evaluation.md (0.5.0)"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def comp_events(root: Path) -> list[dict]:
    return [e for e in ledger.read_events(root) if e["type"].startswith("compensation.")]


def validate_is_read_only(root: Path, expect_exit: int = 0) -> str:
    before = state(root)
    res = run(root, "validate")
    assert res.exit_code == expect_exit, res.output
    assert state(root) == before                     # every file, ledger and .careeros-free snapshot, byte-identical
    return res.output


def comp_checked(root: Path, job: dict, expect_exit: int, expected_types: list[str]) -> str:
    """`comp` changes nothing but the ledger, and the ledger only by exactly the expected events."""
    before = state(root)
    res = run(root, "comp", job["id"])
    assert res.exit_code == expect_exit, res.output
    assert_only_ledger_appended(before, state(root), expected_types)
    return res.output


def evaluate(root: Path, job: dict) -> tuple[dict, str]:
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 0, res.output
    return load(root, job).result, res.output


def test_structured_compensation_end_to_end(career, tmp_path: Path) -> None:
    root, ids = career
    job = make_job(root, comp=False)
    jd = job["path"].parent / "jd.md"
    jd.write_text(JD + PAY_LINE + "\n", encoding="utf-8")
    write_agent(root, job, agent_half(ids), evaluate=False)
    guarded = {k: v for k, v in snapshot(root).items() if k.startswith("career/")}
    guarded |= {"job.md": job["path"].read_bytes(), "jd.md": jd.read_bytes()}

    # (a) a posted figure below a verified floor caps the evaluation at weak
    res = run(root, "comp", job["id"], "--init")
    assert res.exit_code == 0 and job["comp"].is_file() and comp_events(root) == []
    write_comp(job, [POSTED])
    comp_checked(root, job, 0, ["compensation.recorded"])
    result, _ = evaluate(root, job)
    assert result["tier"] == "weak" and [c["code"] for c in result["caps"]] == ["compensation"]
    assert result["basis"]["basis_version"] == "2" and result["stamps"]["compensation"]
    assert validate_is_read_only(root).count("EVL") == 0
    comp_checked(root, job, 0, [])                                       # unchanged record: no event

    # (b) a figure above the floor stales the evaluation (EVL008 naming compensation), re-evaluation lifts the cap
    write_comp(job, [dict(POSTED, high=4000000)])
    out = validate_is_read_only(root)
    assert "EVL008" in out and "compensation" in out and "EVL007" not in out
    comp_checked(root, job, 0, ["compensation.recorded"])
    result, _ = evaluate(root, job)
    assert result["caps"] == [] and result["tier"] == "pursue"
    assert "EVL" not in validate_is_read_only(root)
    count = len(evl_events(root))
    result, out = evaluate(root, job)
    assert "(unchanged)" in out and len(evl_events(root)) == count

    # (c) foreign currency: no cap without a conversion, a cap with one, none for a conversion dated after the observation
    usd = dict(COMP_OBS, id="u1", low=30000, high=35000, currency="USD", as_of="2026-09-15")
    write_comp(job, [usd])
    comp_checked(root, job, 0, ["compensation.recorded"])
    result, _ = evaluate(root, job)
    assert result["caps"] == [] and any("FX_CONVERSION_MISSING" in a for a in result["action_items"])
    rate = {"from": "USD", "to": "INR", "rate": "83.20", "source": "bank", "as_of": "2026-09-10"}
    write_comp(job, [usd], conversions=[rate])
    assert "EVL008" in validate_is_read_only(root)
    comp_checked(root, job, 0, ["compensation.recorded"])
    result, _ = evaluate(root, job)
    assert [c["code"] for c in result["caps"]] == ["compensation"] and result["tier"] == "weak"
    write_comp(job, [usd], conversions=[dict(rate, as_of="2026-09-16")])
    comp_checked(root, job, 0, ["compensation.recorded"])
    result, _ = evaluate(root, job)
    assert result["caps"] == [] and any("FX_RATE_OUTSIDE_WINDOW" in a for a in result["action_items"])

    # (d) a conflicting pair never caps and asks to resolve
    write_comp(job, [dict(COMP_OBS, id="c1", low=2000000, high=2500000), dict(COMP_OBS, id="c2", low=2800000, high=3000000)])
    comp_checked(root, job, 0, ["compensation.recorded"])
    result, _ = evaluate(root, job)
    assert result["caps"] == [] and "pay: resolve conflicting recruiter pay figures" in result["action_items"]
    valid_conflict = job["comp"].read_bytes()

    # (f)/(g) an invalid record: exit 1, one rejection, deduped; the revert to the latest valid record adds nothing
    write_comp(job, [dict(COMP_OBS, low=5, high=1)])
    out = comp_checked(root, job, 1, ["compensation.rejected"])
    assert "CMP003" in out
    comp_checked(root, job, 1, [])
    assert "CMP003" in validate_is_read_only(root, 1)
    job["comp"].write_bytes(valid_conflict)
    comp_checked(root, job, 0, [])

    # (g) event totals across the run: 6 distinct valid records, 1 rejection, and a healthy chain
    kinds = [e["type"] for e in comp_events(root)]
    assert kinds.count("compensation.recorded") == 6 and kinds.count("compensation.rejected") == 1
    assert ledger.verify_chain(root) == []

    # (h) isolation: comp and evaluate never touched career memory, the job file or the posting
    after = {k: v for k, v in snapshot(root).items() if k.startswith("career/")}
    after |= {"job.md": job["path"].read_bytes(), "jd.md": jd.read_bytes()}
    assert after == guarded

    migration_matrix(tmp_path)


# --- (e) the golden 0.5.0 record and the migration interruption matrix --------------------------

def fresh_v1(tmp_path: Path, name: str) -> tuple[Path, dict]:
    root = tmp_path / name
    shutil.copytree(FIXTURE, root)
    job_dir = root / V1_JOB
    fm, _ = split_frontmatter((job_dir / "job.md").read_text(encoding="utf-8"))
    return root, {"id": fm["id"], "eval": job_dir / "evaluation.md", "comp": job_dir / "compensation.md",
                  "path": job_dir / "job.md"}


def legacy_obs(root: Path, job: dict) -> dict:
    """The skill's mapping of the stored v1 legacy pay to one observation (the answers the user gives are fixed)."""
    pay = load(root, job).agent["job_facts"]["compensation"]
    return {"id": "c1", "kind": "recruiter", "low": pay["range"]["low"], "high": pay["range"]["high"],
            "currency": pay["range"]["currency"], "basis": pay["range"]["basis"], "period": "year",
            "source": "legacy posting source", "quote": None, "as_of": "2026-09-15", "confidence": pay["confidence"]}


def fill_record(root: Path, job: dict, obs: dict) -> None:
    """Step 1: `comp --init` when there is no file, then fill the skeleton (the only edit of an existing file)."""
    if not job["comp"].exists():
        assert run(root, "comp", job["id"], "--init").exit_code == 0
    fm, body = split_frontmatter(job["comp"].read_text(encoding="utf-8"))
    fm.update(market="Atlantis (home)", observations=[obs])
    notes = f"{body.rstrip()}\n\n## {HEADING}\n\nrange {obs['low']}-{obs['high']} {obs['currency']} ({obs['basis']})\n"
    job["comp"].write_text(join_frontmatter(fm, notes), encoding="utf-8")


def remove_block(root: Path, job: dict) -> None:
    edit(root, job, lambda fm: fm["agent"]["job_facts"].pop("compensation"))        # step 3


def derivation(root: Path, job: dict) -> dict:
    r = load(root, job).result
    return {k: r[k] for k in ("tier", "caps", "stamps", "action_items")} | {
        "cap_inputs": r["basis"]["cap_inputs"], "versions": (r["basis"]["basis_version"], r["basis"]["rules_version"])}


def observation_count(job: dict) -> int:
    rec, _ = comp_record.parse(comp_record.load(job["comp"], job["comp"].parents[3]))
    return len(rec.observations)


def finish(root: Path, job: dict, *, validate_first: bool = True) -> dict:
    """Steps 2-4 for whatever state was found, then an `(unchanged)` repeat."""
    if validate_first:
        assert run(root, "comp", job["id"]).exit_code == 0
    if "compensation" in load(root, job).agent["job_facts"]:
        remove_block(root, job)
    result, _ = evaluate(root, job)
    assert result["basis"]["basis_version"] == "2"
    again = run(root, "evaluate", job["id"])
    assert again.exit_code == 0 and "(unchanged)" in again.output
    return derivation(root, job)


def migration_matrix(tmp_path: Path) -> None:
    # the golden record: stale (never tampered), refused with the migration wording
    root, job = fresh_v1(tmp_path, "v1-ref")
    out = validate_is_read_only(root)
    assert "EVL008" in out and not any(c in out for c in ("EVL007", "EVL010"))
    refused = run(root, "evaluate", job["id"])
    assert refused.exit_code == 1 and "EVL003" in refused.output
    assert "migrate" in " ".join(refused.output.split()) and "`evaluate` skill" in " ".join(refused.output.split())

    # the uninterrupted migration is the reference
    obs = legacy_obs(root, job)
    fill_record(root, job, obs)
    first_write = sha(job["comp"])
    reference = finish(root, job)
    assert [c["code"] for c in reference["caps"]] == ["compensation"] and reference["versions"] == ("2", "2")
    assert sha(job["comp"]) == first_write and observation_count(job) == 1
    assert "EVL" not in validate_is_read_only(root)

    def converge(name: str, setup, *, fill: bool, validate_first: bool = True) -> None:
        r, j = fresh_v1(tmp_path, name)
        setup(r, j)
        written = sha(j["comp"]) if j["comp"].exists() else None
        if fill:                                                        # the resume step that writes the record
            fill_record(r, j, obs)
            written = sha(j["comp"])
        assert derivation_after(r, j, validate_first) == reference
        assert sha(j["comp"]) == written                                # never rewritten after its full write
        assert observation_count(j) == 1 and j["comp"].read_text(encoding="utf-8").count(HEADING) == 1
        events = [e["type"] for e in comp_events(r)]
        assert events.count("compensation.rejected") == 0

    def derivation_after(r: Path, j: dict, validate_first: bool) -> dict:
        return finish(r, j, validate_first=validate_first)

    # row 2: stopped after step 1 (file written, block present) -> resume at step 3
    converge("after-step-1", lambda r, j: fill_record(r, j, obs), fill=False)

    # row 7: an empty skeleton with the block present -> evaluate refuses, the skill fills the skeleton, then steps 2-4
    def skeleton(r: Path, j: dict) -> None:
        assert run(r, "comp", j["id"], "--init").exit_code == 0
        message = " ".join(run(r, "evaluate", j["id"]).output.split())
        assert "compensation.md is empty" in message and "confirm the job has no pay information" in message
    converge("empty-skeleton", skeleton, fill=True)

    # row 4: stopped after step 3 (file valid, block removed, not yet re-evaluated) -> step 4
    def after_three(r: Path, j: dict) -> None:
        fill_record(r, j, obs)
        assert run(r, "comp", j["id"]).exit_code == 0
        remove_block(r, j)
        assert "EVL008" in validate_is_read_only(r)
    converge("after-step-3", after_three, fill=False, validate_first=False)

    # row 5: block removed and no compensation.md -> rebuild from the stored v1 result's cap_inputs
    def no_file(r: Path, j: dict) -> None:
        remove_block(r, j)
        assert not j["comp"].exists() and "EVL008" in validate_is_read_only(r)
    r5, j5 = fresh_v1(tmp_path, "block-gone")
    no_file(r5, j5)
    stored = load(r5, j5).result["basis"]["cap_inputs"]["range"]
    assert (stored["low"], stored["high"], stored["currency"], stored["basis"]) == (
        obs["low"], obs["high"], obs["currency"], obs["basis"])
    fill_record(r5, j5, dict(obs, low=stored["low"], high=stored["high"]))
    assert derivation_after(r5, j5, True) == reference
    assert observation_count(j5) == 1

"""The skills move a job's status only through `careeros transition`, new jobs get an id, and the workspace
CLAUDE.md states the rules. Wording only; the commands themselves are covered elsewhere."""

from __future__ import annotations

from test_skills_evaluation import TEMPLATES, skill

CLAUDE = TEMPLATES / "workspace" / "CLAUDE.md"
STATUS_SKILLS = ("track", "offer", "interview")
HAND_EDIT_CUES = ("Change `Status:", "Update the job file status", "Update `jobs/pipeline.md`", "Update pipeline.md",
                  "Update the checkbox in `jobs/pipeline.md`", "Update `Status:` in the job's file",
                  "update the pipeline status to")


def test_no_skill_tells_the_agent_to_edit_a_status_or_the_pipeline_by_hand() -> None:
    for name in STATUS_SKILLS:
        text = skill(name)
        for cue in HAND_EDIT_CUES:
            assert cue not in text, (name, cue)
        assert "careeros transition" in text, name


def test_track_and_offer_ask_before_a_transition_and_never_force() -> None:
    for name in ("track", "offer"):
        text = skill(name)
        assert "Only if the user says yes" in text, name
        assert "refuses" in text and "stop" in text, name
    track = skill("track")
    assert "Never run `--force` yourself" in track
    assert "careeros approve <job-slug>" in track
    assert "--to ACCEPTED" in skill("offer") and "--to WITHDRAWN" in skill("offer") and "--to OFFER" in skill("offer")


def test_browse_registers_the_jobs_it_saves() -> None:
    text = skill("browse")
    step = text[text.index("**C2. Register the saved jobs.**"):text.index("**D0.")]
    assert step.index("careeros migrate --dry-run") < step.index("careeros migrate --yes")
    assert "do not add frontmatter or ids by hand" in step
    assert text.index("**C.** Append to `activity.md`") < text.index("**C2.") < text.index("**D0.")


def test_the_workspace_entry_file_states_the_state_rules() -> None:
    text = CLAUDE.read_text(encoding="utf-8")
    rules = text[text.index("## State rules"):]
    for phrase in ("only through `careeros transition`", "`careeros migrate` after saving", "tell the user what it said and stop",
                   "Only the user runs `careeros approve` and `careeros memory confirm`"):
        assert phrase in rules, phrase
    assert "YYYY-MM-DD-co-title.md" not in text

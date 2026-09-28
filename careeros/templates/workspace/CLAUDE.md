# CareerOS — Job Search Workspace

## On every session start

1. Check if `profile.md` exists.
   - **Missing** → load `.claude/skills/onboard/SKILL.md` and run it before anything else. Do not read other files first.
   - **Present** → read `profile.md` above the fold, then read `jobs/pipeline.md` above the fold.

2. Wait for the user's first message, then act.

## Skills

| Trigger | Skill |
|---------|-------|
| "browse [board]" / "find jobs on [board]" | `.claude/skills/browse/SKILL.md` |
| "show pipeline" / "track" / "status" | `.claude/skills/track/SKILL.md` |
| "apply to [job]" / "fill application for [job]" | `.claude/skills/apply/SKILL.md` |
| "research [company]" / "find people at [company]" / "comp research" | `.claude/skills/research/SKILL.md` |
| "connect with [person]" / "send outreach to [person]" / "draft email to [person]" | `.claude/skills/outreach/SKILL.md` |
| "check follow-ups" / "who needs a follow-up?" / "follow-up queue" | `.claude/skills/follow-up/SKILL.md` |

## Workspace layout

```
profile.md          — career profile + scoring rubric (fold file)
boards.md           — configured boards and browse URLs
jobs/
  pipeline.md       — active pipeline (fold file)
  discovered/       — one directory per saved job
    YYYY-MM-DD-company-title.md   — job details
    [job-slug]/
      company.md    — company + compensation research
      people.md     — contacts: hiring managers, recruiters, peers
activity.md         — append-only action log (source of truth for follow-up cadence)
```

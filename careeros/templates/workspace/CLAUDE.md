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

## Workspace layout

```
profile.md          — career profile + scoring rubric (fold file)
boards.md           — configured boards and browse URLs
jobs/
  pipeline.md       — active pipeline (fold file)
  discovered/       — one .md per saved job
activity.md         — append-only action log
```

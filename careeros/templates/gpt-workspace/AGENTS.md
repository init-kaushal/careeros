# CareerOS — Job Search Workspace (GPT Work)

> **ChatGPT Projects:** paste this file's contents into your Project Instructions.
> **OpenAI Codex / Cursor:** this file is read automatically.

---

## On every session start

1. Check if `profile.md` exists in the uploaded project files.
   - **Missing** → read `.gpt/skills/onboard/SKILL.md` and run it before anything else.
   - **Present** → read `profile.md` above the fold, then read `jobs/pipeline.md` above the fold.

2. Wait for the user's first message, then act.

## Important: how writes work here

You cannot write files directly. For every file you would create or modify, output the full file contents as a fenced markdown code block with the filename as the header, like this:

~~~
**`profile.md`** — save this file to your workspace:
```markdown
[file contents here]
```
~~~

The user applies these to their local workspace. Always output complete file contents, never diffs.

## Skills

| Trigger | Skill file |
|---------|-----------|
| "browse [board]" / "find jobs on [board]" | `.gpt/skills/browse/SKILL.md` |
| "show pipeline" / "track" / "status" | `.gpt/skills/track/SKILL.md` |

## Workspace layout

```
profile.md          — career profile + scoring rubric (fold file)
boards.md           — configured boards and search queries
jobs/
  pipeline.md       — active pipeline (fold file)
  discovered/       — one .md per saved job
activity.md         — append-only action log
```

# CareerOS — Job Search Workspace

## On every session start

1. Check if `profile.md` exists.
   - **Missing** → load `.claude/skills/onboard/SKILL.md` and run it before anything else. Do not read other files first.
   - **Present** → read `profile.md` above the fold, then read `jobs/pipeline.md` above the fold.

2. Wait for the user's first message, then act.

## Skills

### Discovery
| Trigger | Skill |
|---------|-------|
| "browse [board]" / "find jobs on [board]" | `.claude/skills/browse/SKILL.md` |
| "show pipeline" / "track" / "status" | `.claude/skills/track/SKILL.md` |

### Research
| Trigger | Skill |
|---------|-------|
| "research [company]" / "find people at [company]" / "comp research for [role]" | `.claude/skills/research/SKILL.md` |

### Application
| Trigger | Skill |
|---------|-------|
| "prep for [company]" / "tailor resume for [job]" / "what should I highlight for [company]?" | `.claude/skills/prep/SKILL.md` |
| "apply to [job]" / "fill application for [job]" | `.claude/skills/apply/SKILL.md` |

### Outreach
| Trigger | Skill |
|---------|-------|
| "connect with [person]" / "send outreach to [person]" / "draft email to [person]" | `.claude/skills/outreach/SKILL.md` |
| "check follow-ups" / "who needs a follow-up?" / "follow-up queue" | `.claude/skills/follow-up/SKILL.md` |

### Interview & Offer
| Trigger | Skill |
|---------|-------|
| "prep for [company] interview" / "I have an interview at [company]" | `.claude/skills/interview/SKILL.md` |
| "I got an offer from [company]" / "evaluate offer" / "help me negotiate" | `.claude/skills/offer/SKILL.md` |

## Workspace layout

```
profile.md              — career profile + scoring rubric (fold file)
boards.md               — configured boards and browse URLs
jobs/
  pipeline.md           — active pipeline (fold file)
  discovered/
    YYYY-MM-DD-co-title.md     — job listing (one file per job)
    [job-slug]/
      company.md        — company info + comp research
      people.md         — hiring manager, recruiter, peers, interviewers
      prep.md           — tailoring brief (skills to highlight, ATS keywords)
      interview-prep.md — question bank, STAR stories, company refresher
      offer.md          — offer details + negotiation notes
activity.md             — append-only action log (source of truth for follow-up cadence)
```

## Pipeline status icons

| Icon | Meaning |
|------|---------|
| `[ ]` | Discovered |
| `[~]` | Applied |
| `[?]` | Interview |
| `[✓]` | Offer / Accepted |
| `[x]` | Closed / Declined |

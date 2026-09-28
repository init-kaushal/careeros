# Cowork — Agent-native job search

Cowork turns Claude or ChatGPT into a full job-search assistant. One command scaffolds a workspace; you chat from there — no forms, no dashboards, no switching between tools.

---

## Full capability map

CareerOS covers every phase of a job switch:

### Discovery
| Say | What happens |
|-----|-------------|
| "browse linkedin" | Opens your real Chrome → extracts listings → scores against your profile → saves selected jobs |
| "browse wellfound for senior backend roles" | Same, with a refined search |
| "show pipeline" / "status" | Renders your pipeline table with status icons |

### Research
| Say | What happens |
|-----|-------------|
| "research Stripe" | Navigates to company page → extracts size, stack, culture signals, recent news |
| "find people at Stripe" | LinkedIn people search → identifies hiring managers, recruiters, peers |
| "comp research for staff engineer at Stripe" | Checks Levels.fyi, Glassdoor, LinkedIn Salary → benchmarks with confidence rating |

### Application prep
| Say | What happens |
|-----|-------------|
| "prep my application for Stripe" | Reads JD → maps your skills against must-haves → produces tailoring brief with ATS keywords and bullet suggestions |
| "apply to Stripe" | Drafts cover letter → surveys the form → fills fields from profile → waits for your approval → submits |

### Outreach
| Say | What happens |
|-----|-------------|
| "connect with Jane Doe at Stripe" | Drafts LinkedIn note (≤300 chars) → navigates to profile → waits for your approval → sends |
| "draft outreach email to the recruiter at Stripe" | Drafts email (you send it from your own client) |
| "check follow-ups" | Scans activity log → surfaces overdue invites / applications / interviews → drafts per-item → confirms before each send |

### Interviews
| Say | What happens |
|-----|-------------|
| "I have an interview at Stripe tomorrow" | Looks up interviewers on LinkedIn → generates question bank → drafts STAR stories from your profile → writes interview-prep.md |
| "prep for my Stripe hiring manager round" | Same, scoped to behavioural + leadership questions |

### Offers
| Say | What happens |
|-----|-------------|
| "I got an offer from Stripe" | Records base / equity / bonus / benefits → calculates Year 1 and steady-state total comp |
| "help me negotiate with Stripe" | Identifies negotiation levers → drafts negotiation email with specific ask |
| "compare my Stripe and Notion offers" | Side-by-side comp breakdown + non-comp factors + recommendation |

---

## Workspace layout

```
profile.md              — career profile + scoring rubric
boards.md               — board URLs and search queries
jobs/
  pipeline.md           — active pipeline
  discovered/
    YYYY-MM-DD-co-title.md    — job listing
    [job-slug]/
      company.md        — company info + comp research
      people.md         — hiring manager, recruiter, peers, interviewers
      prep.md           — tailoring brief: keywords, bullets to emphasise
      interview-prep.md — question bank, STAR stories, company refresher
      offer.md          — offer details + negotiation notes
activity.md             — append-only action log (drives follow-up cadence)
```

---

## Choose your runtime

| | Claude Code | GPT Work |
|---|---|---|
| **Scaffold** | `careeros init ~/my-job-search` | `careeros init ~/my-job-search --runtime gpt` |
| **Browse** | Real Chrome (logged-in sessions, no detection) | Web search for public boards; paste for LinkedIn |
| **File writes** | Direct | Code blocks you apply manually |
| **LinkedIn outreach** | Fully automated (real session) | Manual — drafts only |
| **Form filling** | Yes (Claude-in-Chrome) | No |

**Recommendation:** use Claude Code for the full experience. GPT Work is useful for research and drafting when you don't have Claude Code access.

---

## Step-by-step guides

- [Claude Code setup](claude-code.md)
- [GPT Work setup](chatgpt.md)

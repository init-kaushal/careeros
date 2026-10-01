# CareerOS

Scaffold a markdown-native job-search workspace for Claude Code or ChatGPT Projects.

Your profile, jobs, and activity log live in a plain-text directory you own — version-control it, back it up, hand it to any agent. No account. No cloud sync.

---

## How it works

One command scaffolds a workspace. On the first session, the agent interviews you to build your profile, scoring rubric, and (optionally) your resume. After that, you chat: "browse linkedin", "research Stripe", "prep for Stripe", "apply to Stripe", "connect with [recruiter]", "check follow-ups", "I have an interview at Stripe", "I got an offer from Stripe", "show pipeline" — covering discovery through offer, not just search.

```bash
careeros init ~/my-job-search                # Claude Code (recommended)
careeros init ~/my-job-search --runtime gpt  # ChatGPT Projects
```

Full setup: [Getting Started](getting-started.md) · [Cowork guides](cowork/index.md)

---

## Runtimes

| | Claude Code | GPT Work |
|---|---|---|
| **Command** | `careeros init ~/my-job-search` | `careeros init ~/my-job-search --runtime gpt` |
| **Browse method** | Real Chrome via Claude-in-Chrome | GPT web search + paste for authenticated boards |
| **File writes** | Direct | Code blocks for user to apply |
| **LinkedIn / Instahyre** | Full (real logged-in session) | Paste page content into chat |

---

## Command reference

| Command | What it does |
|---|---|
| `careeros init <path>` | Scaffold a Claude Cowork workspace |
| `careeros init <path> --runtime gpt` | Scaffold a GPT Work workspace |
| `careeros init <path> --refresh` | Update skill files without touching user data |

---

## Scope — what this is and isn't

CareerOS drives the whole job-search workflow — discovery, company/people research, resume tailoring, applying, outreach, follow-ups, interview prep, and offer negotiation — but it's a copilot you run, not a bot that job-hunts unattended:

- **It never submits anything without asking first.** Applications, LinkedIn messages, and emails are always shown to you for a yes before anything irreversible happens.
- **It doesn't generate your resume from nothing.** Give it a starting resume (at onboarding, or anytime by saying "add my resume") and it tailors copies per job — reordering and rewording what's already true, never inventing experience. If your real resume PDF comes from a LaTeX/Overleaf project, tell it during onboarding (or later, under `## Resume format` in `profile.md`) — `prep` will clone that project per application instead of approximating your format from scratch. It can also maintain a handful of resume *variants* (different emphases of the same true experience — e.g. platform/infra-heavy vs. backend-heavy) and pick the closest match per job, then tailor lightly on top.
- **Drafted text gets a humanize pass before you see it.** Cover letters (`apply`), LinkedIn connection notes and outreach emails (`outreach`), and follow-up messages (`follow-up`) all run through the `humanize` skill before the usual yes/regenerate/skip prompt — swapping stock AI phrasing and clichés for the specific, concrete language your actual resume and research already support. It's a writing-quality pass, not a claim about beating AI-detection tools (nothing reliably does that).
- **Working abroad is supported through markets.** During onboarding you can list other countries; `profile.md` gets a `## Markets` table with a minimum base and sponsorship need for each, and onboarding offers regional boards from a built-in catalog (Singapore, Thailand, Vietnam, Europe, UK and Gulf). Visa and salary rules are deliberately *not* shipped — they change — so the `market` skill ("research Singapore market") looks up current routes from official sources, checks them against your profile, and saves a dated, cited file under `markets/`. `browse` then flags whether each listing offers sponsorship. It's research to support your decision, not legal advice.
- **Browsing is scoped to boards in `boards.md`.** LinkedIn, Instahyre, Wellfound, and Naukri are set up by default; add any other board by giving its URL the first time you say "browse [board]".
- **It doesn't run on a schedule by itself.** Every skill fires from a chat trigger. If you want "browse linkedin" to run automatically (say, daily), wire that up with your agent runtime's own scheduling feature — CareerOS doesn't ship one.
- **Follow-up tracking is mostly log-based.** It checks your email for replies when an email tool is available in your session, but otherwise relies on what's recorded in `activity.md`.

---

## Known ATS quirks

Learned from running the `apply` skill against real applicant-tracking systems, not just LinkedIn Easy Apply:

- **Some ATS forms live inside an iframe** (iCIMS is one). If the agent's page-reading tool comes back empty or finds the wrong element, that's usually why.
- **A login wall can appear mid-form, not just at the start.** Typing your email into an ATS you've used before can trigger a "log in to your existing account" prompt partway through. CareerOS will stop and hand the tab back rather than touch a password field — it never authenticates on your behalf, even with a browser-saved password sitting right there. It'll name the exact tab and field so a password manager extension's autofill can do it in one click.
- **ATS sessions can time out fast.** A few minutes of inactivity was enough to silently log one candidate session out and reset the form mid-fill. There's no way around needing you to be logged in — CareerOS can't authenticate for you — but it front-loads all resume/cover-letter drafting *before* opening the application tab, so the tab isn't left idle for a long side-task once a session is live. If a timeout does happen, it's a one-line "please log back in" hand-off, not a debugging session.
- **Vendor-level quirks are remembered after the first time.** The first time an ATS vendor (iCIMS, Workday, Greenhouse, Lever, etc.) shows one of the behaviors above, CareerOS records it under `## Known ATS platforms` in `profile.md` — so the next application on the same vendor front-loads the check (e.g. "make sure you're logged in to iCIMS first") instead of hitting the wall live again.
- **Returning-candidate forms often arrive pre-filled** from a prior application (name, resume, even an old cover letter). CareerOS checks these against your files instead of blindly overwriting them, and flags anything that looks stale.
- **Consent questions are always yours.** AI-processing opt-outs, interview-recording consent, EEO/voluntary disclosures, sponsorship — CareerOS surfaces these and waits, even when the ATS has pre-selected a default.
- **One-time platform preferences (like LinkedIn's profile-sharing modal) get remembered.** The first time, CareerOS asks; your answer is saved to `profile.md` under `## Platform preferences` so it doesn't ask again on the next application.
- **File pickers are a hard wall, on purpose.** A resume/cover-letter upload is a native OS file dialog — there's no accessibility hook to drive it, especially nested inside an ATS's iframe — so CareerOS always hands this one back to you with the exact filename and folder, rather than attempting it.

---

## Speeding up a single application

A lot of the time on a first pass against any given company goes to one-time setup that later applications reuse automatically:

- **Company/people research is cached per company**, not per job — applying to a second role at a company you've already researched reuses `company.md`/`people.md` (with your say-so) instead of re-scraping.
- **Cover letters reuse `prep`'s "cover letter angle"** instead of drafting cold in `apply`, and a letter already approved for a job isn't redrafted on a second pass.
- **Resume variants (see above)** mean most applications are a light edit on an already-close-to-right starting point, not a from-scratch tailoring pass.
- **Every job gets one directory** (`jobs/discovered/[company]-[title]/`) holding everything — the job record, prep brief, tailored resume/cover letter, company and people research — so nothing is re-derived because it couldn't be found.

---

## Privacy

Your data never reaches a CareerOS server — there isn't one. The workspace is a folder on your machine. LLM calls go to whichever provider your agent runtime uses (Anthropic for Claude Code, OpenAI for GPT Work).

---

## Installation

```bash
git clone https://github.com/init-kaushal/careeros
cd careeros
python3.11 -m venv .venv && source .venv/bin/activate   # requires Python 3.11+
pip install -e .
```

See [Getting Started → Troubleshooting](getting-started.md#troubleshooting) if `careeros` stops working after a Python upgrade.

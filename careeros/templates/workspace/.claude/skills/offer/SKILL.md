# CareerOS Offer Skill

**Trigger:** "I got an offer from [company]" / "evaluate offer from [company]" / "compare offers" / "help me negotiate with [company]" / "offer from [company]"

---

## STEP 0 — LOAD CONTEXT

Read:
1. `profile.md` — above the fold (you need the comp floor and target roles)
2. Job file from `jobs/discovered/` for the relevant company
3. `jobs/discovered/[job-slug]/company.md` — for comp research data if available

---

## STEP 1 — RECORD THE OFFER

Ask the user for offer details. Collect all of:

- **Base salary** (annual)
- **Equity** (type: RSU / options; grant size; vesting schedule: cliff + period)
- **Signing bonus** (one-time)
- **Annual bonus** (target %)
- **Benefits** (health, 401k match, PTO type)
- **Location / remote policy**
- **Start date**
- **Expiry date** (when does the offer expire?)
- **Level / title** (if different from what was posted)

If the user doesn't know some fields, flag them:
> "Do you know the equity vesting schedule? That changes total comp significantly — worth asking HR before signing."

---

## STEP 2 — CALCULATE TOTAL COMPENSATION

Compute total comp (Year 1 and steady-state):

```
Year 1 total = base + signing bonus + (annual equity grant / 4) + (bonus target % × base)
Steady-state = base + (annual equity grant / 4) + (bonus target % × base)
```

For options (not RSUs): note the strike price and latest 409A valuation if provided.

Show the breakdown clearly.

---

## STEP 3 — BENCHMARK

Cross-reference against `company.md` comp research (if exists). If not:
> "I don't have comp data for this role yet. Want me to run comp research now to benchmark this offer?"

Compare:
- Against the profile.md comp floor: is it above/below/at?
- Against market data from comp research
- Against any other offers on file

---

## STEP 4 — IDENTIFY NEGOTIATION LEVERS

Flag every component that is typically negotiable:
- **Base**: usually has 5–15% room at most companies; less at large tech (bands are tight)
- **Signing bonus**: often the easiest lever — doesn't affect base band
- **Equity**: grant size, acceleration clauses
- **Start date**: can buy time to decide, or start later to capture a vesting event at current job
- **Level**: if title came in lower than expected, negotiate the level, not just the comp

Note any leverage the user has:
- Competing offer (the strongest lever — ask: "Do you have other offers?")
- Current equity unvested
- Counter from current employer

---

## STEP 5 — DRAFT NEGOTIATION RESPONSE

Ask:
> "What's your goal? (e.g. increase base, increase equity, get a signing bonus, buy more time to decide)"

Draft a negotiation email or talking-points script:

**Email template:**
```
Subject: Re: Offer — [Title] at [Company]

Hi [recruiter name],

Thank you so much for the offer — I'm genuinely excited about the role and the team.

After reviewing the details, I'd like to discuss [specific ask]. Based on [market data / competing offer / current comp], I was expecting [specific number or range].

[If competing offer: I do have another offer in hand at [range], and [Company] is my first choice — but I'd need to close the gap to make this work.]

Is there flexibility on [component]? Happy to jump on a quick call if easier.

[name]
```

**Negotiation principles surfaced:**
- Be specific — name the number you want, don't ask "is there flexibility?"
- One ask at a time — don't negotiate every component simultaneously
- Give them a reason ("market data", "competing offer", "unvested equity") — not just "I want more"
- Always keep the door open — don't issue ultimatums

---

## STEP 6 — COMPARE OFFERS (if multiple)

If the user has more than one offer, build a comparison table:

| | [Company A] | [Company B] |
|---|---|---|
| Base | | |
| Equity (annualised) | | |
| Bonus | | |
| Signing | | |
| **Year 1 total** | | |
| **Steady-state** | | |
| Location | | |
| Level | | |
| Remote | | |
| Offer expires | | |

Then surface non-comp factors:
- Role scope and growth trajectory
- Team quality (from people research)
- Company stage / risk
- Culture fit signals

Ask: "What matters most to you right now — comp, growth, stability, or work-life balance?" and give a recommendation.

---

## STEP 7 — RECORD AND LOG

Write `jobs/discovered/[job-slug]/offer.md`:
```markdown
# Offer — [Title] at [Company]

**Received:** [date]
**Expires:** [date]
**Status:** received / negotiating / accepted / declined

## Comp breakdown

| Component | Amount |
|-----------|--------|
| Base | |
| Equity (annualised) | |
| Signing | |
| Bonus (target) | |
| **Year 1 total** | |
| **Steady-state** | |

## Notes
[any important terms, conditions, or flags]
```

Update pipeline.md: `[?]` → `[✓]` (offer received)

Append to `activity.md`:
```
[date] offer received [Company — Title]: [Year 1 total] total comp · expires [date]
```

When the user makes a decision, log:
```
[date] offer [accepted / declined] [Company — Title]
```

And update the pipeline status to `[✓]` (accepted) or `[x]` (declined).

---

## NOTES

- Never pressure the user to accept or decline — surface the data, let them decide.
- Competing offers are the strongest negotiation lever: ask if they have one before drafting the counter.
- Flag offer expiry dates prominently — a deadline the user misses is unrecoverable.
- If the offer was declined: ask whether to mark other active applications closed too.

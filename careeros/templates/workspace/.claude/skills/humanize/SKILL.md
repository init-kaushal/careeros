# CareerOS Humanize Skill

**Trigger:** "humanize this" / "make this sound less AI-generated" / "check this for AI tells" — also invoked internally by `apply`, `outreach`, and `follow-up` before showing a drafted cover letter, connection note, email, or follow-up message to the user.

This isn't a detector-evasion tool. No edit reliably beats an AI-detection model, and a cover letter that technically "scores human" but reads like generic filler still won't land. The actual target is the same thing a good human editor would flag: clichéd phrasing, hedge-everything vagueness, and rhetorical patterns that are statistically overused by LLMs and have become a visible tell to the people reading them — a recruiter, hiring manager, or the connection on the other end of a LinkedIn note. Fixing those makes the writing better on its own terms, independent of what wrote it first.

Credit: the checklist below is adapted from the publicly documented voice rules and Humanizer skill in [sergebulaev/linkedin-skills](https://github.com/sergebulaev/linkedin-skills) (MIT), reworked for job-search writing (cover letters, outreach, follow-ups) instead of LinkedIn posts. CareerOS doesn't take on that repo's Publora/Apify/Pixfaro publishing machinery — just the portable checklist.

---

## STEP 1 — RUN THE CHECKLIST

Before showing any drafted cover letter, connection note, outreach email, or follow-up message, scan it against these:

**Stock AI vocabulary** — flag and replace: leverage, fundamentally, streamline, harness, delve, unlock, foster, robust, seamless, cutting-edge, game-changer, testament to, passionate about, thrilled to, dive into, navigate, landscape, ecosystem, synergy, holistic, elevate, empower, in today's [fast-paced/ever-evolving] world.

**Cover-letter-specific clichés** — flag and rewrite: "I am writing to express my interest in...", "I believe I would be a great fit...", "proven track record", "I am confident that my skills and experience...", a closer that's only "thank you for considering my application" with nothing else.

**Reveal bridges / staccato drama** — "Here's the thing:", "The result?", a one-sentence paragraph dropped for effect. These read as a LinkedIn-post tic; out of place and a visible tell in a cover letter or recruiter email.

**Stacked triads on autopilot** — "innovative, scalable, and efficient"-style three-part lists are fine once; a tell when every other sentence does it. Vary the construction.

**Performed sincerity** — "I'm incredibly excited", "I would be thrilled" with no concrete reason attached. Keep the enthusiasm only if a specific detail backs it up.

**Em dashes** — cap at roughly 1 per 100 words. Don't ban them outright, but don't let every third sentence lean on one.

**Vague claims where a real number exists** — "significant improvement" when `profile.md` or the tailored resume already has the actual figure (e.g. "cut latency by 20%") is a downgrade, not a style choice. Pull the real number instead. Never invent a number that isn't already sourced from the user's own materials.

**Uniform sentence rhythm** — three or four sentences in a row with the same length and structure reads mechanical. Vary it the way a person naturally would.

**Names** — capitalize correctly, always. Don't lowercase a name for stylistic effect.

---

## STEP 2 — REWRITE, DON'T JUST TRIM

Fix each hit by replacing it with something more specific and concrete pulled from the actual context (the job post, `company.md`, `profile.md`, the resume bullet it's referencing) — not by deleting the sentence and leaving a gap, and not by swapping one cliché for a different one.

Keep whatever length constraint the calling skill already set (300 characters for a LinkedIn note, 3–5 sentences for an outreach email, the cover letter's paragraph structure, etc.) — humanizing must not blow past it.

Do not fabricate a specific detail, number, or claim that isn't already grounded in the user's real materials. If a sentence is vague because there's genuinely nothing more specific to say yet, leave it understated rather than inventing specificity.

---

## STEP 3 — SHOW THE RESULT

Show the cleaned draft the same way the calling skill normally would (its existing "looks good? yes / regenerate / skip" prompt, character count, etc.) — this isn't a separate review stage, just a pass the draft goes through before that prompt appears. Only call out specific changes if the user asks what was changed or invoked this skill directly on pasted text.

For a direct "humanize this" request on pasted text, show the before and after so the user can see what moved.

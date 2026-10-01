# Boards catalog

Starting points for `boards.md`. Onboarding offers the boards for each market the user listed under `## Markets` in `profile.md`. Copy the entry into `boards.md` with a `**Market:**` line.

**Status key:** `checked` = loaded as a job search when this was written (2026-10-01) · `unchecked` = standard URL pattern, not yet confirmed · `browser-only` = blocks automated fetches but should work in the user's real Chrome. If a URL 404s or changes, fix it here and in `docs/lessons-learned.md`.

Replace `[role]`, `[city]`, `[country]` with the user's target role and market. These are search starting points, not endorsements; visa and pay rules are researched separately by the market skill and are never assumed from a board.

## Anywhere / relocation-focused

| Board | Browse URL | Notes | Status |
|---|---|---|---|
| LinkedIn | `https://www.linkedin.com/jobs/search/?keywords=[role]&location=[country or city]` | Works for every market. Needs a logged-in Chrome session. Add "visa sponsorship" or "relocation" to the keywords to bias results. | checked |
| Relocate.me | `https://relocate.me/search` | Jobs that offer relocation or visa support. Filters include the Netherlands, UK, Cyprus, Portugal, Spain and Japan. | checked |
| Wellfound | `https://wellfound.com/jobs?role=software-engineer&location=[city]` | Startups; many remote-friendly. | unchecked |
| Remote boards (We Work Remotely, RemoteOK, Himalayas) | `https://weworkremotely.com/`, `https://remoteok.com/`, `https://himalayas.app/jobs` | Remote roles; check the listing for country or time-zone limits before treating them as sponsorship-free. | unchecked |

## Singapore

| Board | Browse URL | Notes | Status |
|---|---|---|---|
| MyCareersFuture | `https://www.mycareersfuture.gov.sg/search?search=[role]` | Government portal; many employers advertise here. No visa filter found, so read the listing. | checked |
| JobStreet Singapore | `https://sg.jobstreet.com/[role-slug]-jobs` | General board. | unchecked |
| Glints | `https://glints.com/sg/opportunities/jobs/explore?keyword=[role]` | Startups and tech. | unchecked |
| LinkedIn | location = Singapore | Best for multinational and big-tech listings. | checked |

## Thailand

| Board | Browse URL | Notes | Status |
|---|---|---|---|
| JobsDB Thailand | `https://th.jobsdb.com/[role-slug]-jobs` | Largest local board. | browser-only |
| LinkedIn | location = Thailand (or Bangkok) | Multinational and regional-HQ roles. | checked |

## Vietnam

| Board | Browse URL | Notes | Status |
|---|---|---|---|
| ITviec | `https://itviec.com/it-jobs/[role-slug]` | IT-specific; shows jobs per city. | checked |
| TopCV | `https://www.topcv.vn/tim-viec-lam-[role-slug]` | Vietnamese-language interface, large local board. | unchecked |
| VietnamWorks | `https://www.vietnamworks.com/[role-slug]-jobs` | General board with an English interface. | unchecked |
| LinkedIn | location = Vietnam (Ho Chi Minh City, Hanoi) | Multinational roles. | checked |

## Europe

| Board | Browse URL | Notes | Status |
|---|---|---|---|
| Relocate.me | see above | Netherlands, UK, Portugal, Spain and others. | checked |
| Arbeitnow | `https://www.arbeitnow.com/` | Has visa-sponsorship and relocation filters; mainly Germany, with some UK and France. | checked |
| Landing.Jobs | `https://landing.jobs/jobs` | Tech jobs, strong on Portugal and Spain, with relocation tags. | unchecked |
| Berlin Startup Jobs | `https://berlinstartupjobs.com/` | Berlin startups. | unchecked |
| Welcome to the Jungle | `https://www.welcometothejungle.com/en/jobs` | France and wider EU; startups and scale-ups. | unchecked |
| EURES | `https://eures.europa.eu/` | Official EU job portal across member states. | unchecked |

## UK and Gulf

| Board | Browse URL | Notes | Status |
|---|---|---|---|
| LinkedIn | location = United Kingdom / UAE / Saudi Arabia | Default for both. | checked |
| Bayt | `https://www.bayt.com/en/international/jobs/[role-slug]-jobs/` | Gulf region. | unchecked |
| GulfTalent | `https://www.gulftalent.com/` | Gulf region. | unchecked |

## Not boards, but needed per market

Several countries publish lists of employers licensed to sponsor work visas (for example the UK and the Netherlands). The market skill checks those when it exists; do not assume a listing offers sponsorship because it appears on a relocation board.

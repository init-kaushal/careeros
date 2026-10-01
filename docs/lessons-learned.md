# Lessons learned

Things CareerOS missed or tripped over during live use, and what changed. Add a line whenever a live run hits something new.

## Browse

| What went wrong | Why | Fix |
|---|---|---|
| Jobs were scored and shown with compensation "unchecked" | The browse skill scored from title, company and location only; nothing required a comp lookup, and a failed Levels.fyi fetch was not retried | `browse` STEP 3 now has a required comp check against the comp floor in `profile.md`, with a source order and a "say unknown, never unchecked" rule. The GPT skill has the same step |
| Levels.fyi returned an empty page | `WebFetch` can't render it | Skill tells the agent to open it in a browser tab and read the page text |
| Instahyre capture returned only URLs | `read_network_requests` shows requests, not response bodies; tracking also starts only at its first call | Skill now says to call it first, reload, find the `candidate_matching` URL, then fetch it from the logged-in page with `javascript_tool` |
| Instahyre output looked cut off | `javascript_tool` truncates at roughly 600 characters | Print compact rows in slices of 10–15 |
| Instahyre's own score was useless | Most jobs sit at a flat 4.5 | Ignore it; score against `profile.md` |
| A "change your job search status" modal covered the page | Instahyre prompts on load; both buttons change an account setting | Don't click either; the API fetch works regardless |

## Browser connection

| What went wrong | Why | Fix |
|---|---|---|
| "Tab not in group" / "couldn't determine which page" on most calls | Several browsers with the extension were connected, and Arc was the OS default browser, so the agent was routed between instances and tab groups | Run `list_connected_browsers`; disable the extension in browsers you don't want driven; log in to the board in the one you do. Retry once or twice, since errors often alternate |
| `navigate` with no tab ID opened a different profile (not logged in) | It targets the first tab of whichever group it resolves to | Use an explicit tab ID from `tabs_context_mcp`, and verify the login state before extracting |

## Process

- A restart of Chrome did not clear the extension connection problems; disabling the extension in the other browser (Arc) is what fixed it.
- Close every tab the agent opened before finishing.

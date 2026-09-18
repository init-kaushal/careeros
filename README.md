# CareerOS

Privacy-first, agent-portable career automation platform.
Your resume, skills, and career data live in a directory you own — not in any cloud.

**[→ init-kaushal.github.io/careeros](https://init-kaushal.github.io/careeros/)** — project overview and getting started guide.

## Quickstart

```bash
# Install (Python 3.11+)
git clone https://github.com/init-kaushal/careeros
cd careeros
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Set your API key for whichever provider you want to use:
export ANTHROPIC_API_KEY=sk-ant-...   # Claude (default)
export OPENAI_API_KEY=sk-...          # OpenAI
# Ollama needs no key — just run `ollama serve` locally

# Optional: choose a model (default is claude-haiku-4-5-20251001)
export CAREEROS_MODEL=gpt-4o-mini       # OpenAI
export CAREEROS_MODEL=ollama/llama3.2   # Ollama (free, local)

# Run the onboarding wizard
careeros onboard
```

The wizard asks where to create your workspace, then extracts your profile from your resume using
whichever model you configured. Your data is written locally — nothing leaves your machine after onboarding.

## What it does

**`careeros onboard`** — interactive wizard that creates a workspace, asks for a resume or text
describing your background, calls Claude to extract structured profile data, and writes it to
`profile/profile.json`. Takes about 60 seconds.

**`careeros workspace status`** — shows workspace path, schema version, profile summary,
recent activity, and counts of skills and goals.

**`careeros workspace validate`** — validates workspace schema and data integrity. Zero output
means everything is clean.

**`careeros export`** — creates a portable zip of your entire workspace. Hand it to any agent
runtime that speaks the CareerOS manifest format.

**`careeros import <path>`** — imports a workspace zip, unpacks it, and makes it your active workspace.

## Workspace layout

CareerOS uses a two-directory model: the framework (this repo) and your workspace (a directory you own).

```
~/my-career/                  ← your workspace, never touched by framework updates
  manifest.json               ← entry point for any agent runtime
  config.json                 ← workspace-level settings
  profile/
    profile.json              ← structured profile data (skills, goals, preferences)
  activity/
    2026-01-15.jsonl          ← append-only activity log, one event per line
  exports/                    ← created by `careeros export`
```

The workspace is self-contained. You can copy it, version-control it, or hand it to
another tool without touching CareerOS.

## Agent portability

Every workspace has a `manifest.json` that any agent runtime can read:

```json
{
  "schema_version": 1,
  "careeros_version": "0.1.0",
  "created_at": "2026-01-15T10:00:00Z"
}
```

Any tool that reads the manifest can discover the workspace structure and work with
your career data without needing CareerOS installed.

## Requirements

- Python 3.11+
- An API key for your preferred LLM provider — used only during `careeros onboard` for profile
  extraction. Everything else runs offline. Supported providers (via [LiteLLM](https://docs.litellm.ai/)):
  Anthropic, OpenAI, Ollama (free, local), Groq, Mistral, Google, and 100+ more.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests run fully offline — no API calls, no network. 77 tests, 0 dependencies on
external services.

## Status

Phase 1 complete: onboarding wizard, profile extraction, workspace management,
export/import. All workspace I/O goes through the `StorageProvider` protocol,
so storage backends can be swapped without touching business logic.

## License

MIT — see [LICENSE](LICENSE).

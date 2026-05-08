# AGENTS.md

## Cursor Cloud specific instructions

This is a Python CLI-based AI agent suite (no web server, no database, no Docker). All agents are runnable scripts under `agents/` and the pipeline orchestrator is at `workflows/orchestrator.py`.

### Running agents locally without API keys

- **Customer Service agent** works fully offline (no API keys needed) for shipping and return queries — it uses keyword-based classification and template replies:
  ```
  python agents/customer_service.py --subject "Where is my order?" --body "..."
  ```
- **Orchestrator** supports `--dry-run` which skips all API calls and subprocess invocations:
  ```
  python -m workflows.orchestrator --product "Foo" --url "https://example.com" --dry-run
  ```

### Lint and syntax checks

No linter is configured in the repo. Use `ruff check .` (installed during setup) for quick lint validation. All files pass `ruff check` with default rules.

### Required secrets for full end-to-end runs

| Secret | Used by |
|--------|---------|
| `GROQ_API_KEY` | content_studio, customer_service (product questions/complaints), growth_report |
| `SHOPIFY_STORE` / `SHOPIFY_ACCESS_TOKEN` | growth_report, customer_service (order lookup), store_ops |
| `SLACK_BOT_TOKEN` | All agents (delivery channel) |
| `APIFY_TOKEN` | trend_scout |
| `LINEAR_API_KEY` / `LINEAR_TEAM_ID` | store_ops (optional) |

### Data directory

Agent outputs are written to `data/` (created at runtime). This directory is `.gitignore`d-style — it does not exist in a fresh clone. Agents create it automatically via `mkdir -p`.

# onestopconnect

A multi-agent toolkit. The first agent included is the **Content Agent (Publisher)** —
an agent that creates, formats, and schedules digital content for platforms.

## Agents

### Content Agent — Publisher

Generates and packages content across four primary surfaces:

| Surface | Description |
|---|---|
| Short-form video scripts | Hook + beats + CTA, optimized for 15–60s |
| Blog / SEO articles | Long-form posts with SEO metadata and outline |
| Social media copy | Platform-specific posts (X, LinkedIn, Instagram, Threads) |
| Newsletter drafts | Subject line, preheader, and body in newsletter-friendly layout |

See [`docs/content-agent.md`](docs/content-agent.md) for the full agent spec.

## Quickstart

```bash
python -m content_agent.cli generate \
  --type blog \
  --topic "How small teams ship faster"
```

Other content types: `video`, `social`, `newsletter`.

## Project layout

```
content_agent/
  __init__.py
  cli.py            # CLI entry point
  agent.py          # ContentAgent orchestrator
  generators/       # One generator per content type
    video.py
    blog.py
    social.py
    newsletter.py
  scheduler.py      # Schedules generated content for publishing
  formatters.py     # Platform-specific formatting helpers
  types.py          # Shared dataclasses / TypedDicts
docs/
  content-agent.md  # Agent specification
```

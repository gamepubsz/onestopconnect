# Content Agent — Publisher

## Role

Creates, formats, and schedules digital content for platforms.

## Capabilities

1. **Short-form video scripts** — 15–60 second scripts with a hook, three beats,
   and a call-to-action. Returns shot/voiceover breakdown when requested.
2. **Blog / SEO articles** — Long-form articles with title, meta description,
   target keywords, outline, and body. Aims for E-E-A-T-aligned structure.
3. **Social media copy** — Platform-aware posts for X, LinkedIn, Instagram, and
   Threads. Respects character limits and platform conventions.
4. **Newsletter drafts** — Subject line, preheader, and HTML/markdown body with
   sections suitable for ESPs (Resend, Mailchimp, Beehiiv, ConvertKit).

## Inputs

```jsonc
{
  "type": "video | blog | social | newsletter",
  "topic": "string, free-form brief",
  "audience": "string, optional",
  "tone": "string, optional",          // e.g. "concise", "playful"
  "platform": "string, optional",      // for social/video: x, linkedin, tiktok…
  "length": "short | medium | long",   // generator-specific defaults
  "schedule_at": "ISO-8601 datetime, optional"
}
```

## Outputs

A `ContentDraft` containing:

- `id` — stable identifier
- `type` — content type
- `title` — primary headline / hook
- `body` — main content
- `metadata` — generator-specific metadata (SEO fields, hashtags, etc.)
- `scheduled_for` — when the draft should be published, if any
- `status` — `draft | scheduled | published`

## Workflow

1. **Plan** — agent decides outline based on `type`, `topic`, and `audience`.
2. **Generate** — invokes the generator for the requested content type.
3. **Format** — applies platform-specific formatting (limits, hashtags, links).
4. **Schedule** — if `schedule_at` is set, hands the draft to the scheduler.

## Extension points

- **Generators** are pluggable: drop a new file in `content_agent/generators/`
  exposing `generate(brief: Brief) -> ContentDraft`.
- **Scheduler** currently logs scheduled items; replace with a queue, cron, or
  publishing API when wiring to a real backend.
- **LLM provider** is abstracted via `agent.LLMClient`. The default
  implementation is a deterministic stub so the package can be exercised
  without API keys.

## Roadmap

- Wire generators to a real LLM backend.
- Add a `publish` step with adapters for X, LinkedIn, Buffer, Resend, etc.
- Persist drafts to a datastore instead of the in-memory registry.
- Add evals for tone/voice consistency across surfaces.

# onestopconnect

## Weekly growth report agent

`agents/growth_report.py` pulls the last 7 days of Shopify activity (orders,
revenue, best-selling product, refund count), reads `data/cs_log.json` to count
auto-resolved vs escalated tickets, asks Groq for a short CEO briefing, posts
it to Slack `#reports`, and saves it to `data/reports/report_<date>.txt`.

### Local run

```bash
pip install -r requirements.txt
export SHOPIFY_STORE=my-shop.myshopify.com
export SHOPIFY_ACCESS_TOKEN=shpat_...
export GROQ_API_KEY=gsk_...
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_CHANNEL="#reports"
python agents/growth_report.py
```

### Schedule

`.github/workflows/growth_report.yml` runs every Sunday at 09:00 UTC+8
(01:00 UTC) and can also be triggered manually via `workflow_dispatch`. Set the
following GitHub Actions secrets: `SHOPIFY_STORE`, `SHOPIFY_ACCESS_TOKEN`,
optional `SHOPIFY_API_VERSION`, `GROQ_API_KEY`, optional `GROQ_MODEL`,
`SLACK_BOT_TOKEN`, optional `SLACK_CHANNEL`.

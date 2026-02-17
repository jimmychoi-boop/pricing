# Daily Strategy Recap

A 3-agent system that produces a ranked daily strategy recap by reading Google Docs, tracking metrics, and posting to Slack.

## Architecture

```
apps/runner/main.py          — pipeline orchestrator
packages/connectors/         — Google Drive, Google Docs, Slack, Metrics
packages/agents/
  docs_agent.py              — scans docs + comments for signals
  metrics_agent.py           — detects metric anomalies/trends
  leader_agent.py            — deduplicates, ranks, formats recap
packages/store/              — SQLite storage + shared schema
configs/                     — YAML configuration
context/operating_context.md — team priorities and watchlist
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Google service account

1. Create a service account in Google Cloud Console with Drive and Docs API access.
2. Download the JSON key file.
3. Share your target Google Drive folders with the service account email.
4. Set the environment variable:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

### 3. Configure Slack webhook

1. Create an incoming webhook in your Slack workspace (Apps > Incoming Webhooks).
2. Set the environment variable:

```bash
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx
```

### 4. Configure source folders

Edit `configs/sources.yaml` and replace the placeholder folder IDs with your actual Google Drive folder IDs:

```yaml
drive_folder_ids:
  - "1ABC..."
  - "2DEF..."
```

### 5. (Optional) Configure metrics

Edit `configs/metrics.yaml` to adjust stub metric values. When warehouse credentials become available, replace `stub_value` entries with `sql:` queries.

### 6. (Optional) Customize operating context

Edit `context/operating_context.md` to reflect your team's current priorities, stakeholders, and signals to watch.

## Run

```bash
python -m apps.runner.main
```

The pipeline will:
1. Fetch docs and comments from configured Google Drive folders.
2. Fetch metric snapshots (stub data for MVP).
3. Run the Docs Agent to detect document changes and comment activity.
4. Run the Metrics Agent to detect anomalies.
5. Run the Leader Agent to deduplicate, rank, and format the recap.
6. Post the recap to Slack (or print to stdout if `SLACK_WEBHOOK_URL` is not set).

All data is stored in a local SQLite database (default: `data/recap.db`).

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes (for real runs) | Path to service account JSON key |
| `SLACK_WEBHOOK_URL` | No | Slack incoming webhook URL |
| `DATABASE_PATH` | No | SQLite DB path (default: `data/recap.db`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

## Configuration Files

- `configs/sources.yaml` — Drive folder IDs, mime types, max files, lookback window
- `configs/thresholds.yaml` — severity/confidence filters, anomaly thresholds, dedup settings
- `configs/metrics.yaml` — metric definitions with stub values (swap to real SQL later)

#!/usr/bin/env bash
# Daily Strategy Recap — cron wrapper script
# Schedule with: crontab -e
# 0 7 * * 1-5 /home/user/pricing/scripts/run_daily_recap.sh

set -euo pipefail

REPO_DIR="/home/user/pricing"
LOG_DIR="${REPO_DIR}/logs"
LOG_FILE="${LOG_DIR}/recap_$(date +%Y-%m-%d).log"

cd "${REPO_DIR}"

# Load environment variables
set -a
source .env
set +a

echo "=== Daily Recap started at $(date) ===" >> "${LOG_FILE}"
python -m apps.runner.main >> "${LOG_FILE}" 2>&1
echo "=== Daily Recap finished at $(date) ===" >> "${LOG_FILE}"

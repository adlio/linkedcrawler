#!/usr/bin/env bash
# Feedback loop for iterating on crawl reach.
#
# Runs a raw (no-sync) crawl with HTML debug dumps enabled, then prints a
# one-line score so each change can be compared to the baseline. Raw mode is
# used because sync mode hides reach by skipping already-seen URNs.
#
# Usage: scripts/diag.sh [URL]
#   URL defaults to Simon Wardley's recent-activity feed.
#   Pass extra crawler flags via LINKEDCRAWLER_EXTRA_FLAGS.
#
# Each run writes to output/diag/<timestamp>/ (HTML snapshots, summary.json,
# raw.json) and appends one line to output/diag/scoreboard.tsv.

set -euo pipefail

cd "$(dirname "$0")/.."

URL="${1:-https://www.linkedin.com/in/simonwardley/recent-activity/all/}"
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
DIAG_DIR="output/diag/${STAMP}"
mkdir -p "${DIAG_DIR}"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv not on PATH — see README for install instructions" >&2
  exit 2
fi

STDOUT_LOG="${DIAG_DIR}/stdout.log"
RAW_OUT="${DIAG_DIR}/raw.json"
export LINKEDCRAWLER_DEBUG_DIR="${DIAG_DIR}"

# `uv run` auto-syncs .venv/ against uv.lock if needed, so this works on a
# freshly-cloned machine without a separate install step.
# shellcheck disable=SC2086
uv run linkedcrawler "${URL}" ${LINKEDCRAWLER_EXTRA_FLAGS:-} > "${STDOUT_LOG}"

# Botasaurus prints status lines before the JSON; slice from the first '{'.
uv run python -c '
import json, sys
text = open(sys.argv[1]).read()
idx = text.find("{")
payload = json.loads(text[idx:])
open(sys.argv[2], "w").write(json.dumps(payload, indent=2))
' "${STDOUT_LOG}" "${RAW_OUT}"

read -r posts rounds errors reached newest < <(uv run python -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(len(d["posts"]), d["rounds_scrolled"], len(d["extraction_errors"]),
      d["reached_last_saved_item"], d["newest_item_key"] or "")
' "${RAW_OUT}")

SCORE_LINE=$(printf '%s\tposts=%s\trounds=%s\terrors=%s\treached_last=%s\tnewest=%s\tdir=%s' \
  "${STAMP}" "${posts}" "${rounds}" "${errors}" "${reached}" "${newest}" "${DIAG_DIR}")

SCOREBOARD="output/diag/scoreboard.tsv"
echo "${SCORE_LINE}" >> "${SCOREBOARD}"

echo "${SCORE_LINE}"
echo
echo "recent runs:"
tail -n 5 "${SCOREBOARD}"

#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/nikitalienov/Documents/commentfront"
BACKEND_DIR="$ROOT/backend"

export LOCAL_API="${LOCAL_API:-http://127.0.0.1:8000}"
export PROD_API="${PROD_API:-https://commentbot-production.up.railway.app}"
export COMMENT_URL="${COMMENT_URL:-https://www.facebook.com/permalink.php?story_fbid=pfbid02M8r99ZESd75oL6deKBHb8n6hRPMu1u4G6S7B8ykxjyv1tDm8FHrtpQPYapQk8jnWl&id=61574636237654&comment_id=4418405568392620}"
export IMAGE_FILE="${IMAGE_FILE:-/Users/nikitalienov/Downloads/2-Campaigns/Nuora/nuora-feminine-gummies-ugc-photo.webp}"

STAMP="$(date +%Y%m%d_%H%M%S)"
EVIDENCE_DIR="$BACKEND_DIR/evidence_reply_image_dedupe_$STAMP"
mkdir -p "$EVIDENCE_DIR"

log() {
  echo "[$(date +%H:%M:%S)] $*"
}

json_get() {
  local file="$1"
  local expr="$2"
  python3 - <<PY "$file" "$expr"
import json, sys
file_path, expr = sys.argv[1], sys.argv[2]
with open(file_path, 'r') as f:
    data = json.load(f)
cur = data
for part in expr.split('.'):
    if part.isdigit():
        cur = cur[int(part)]
    else:
        cur = cur.get(part)
print(cur if cur is not None else "")
PY
}

require_env() {
  local var_name="$1"
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required env var: $var_name" >&2
    exit 1
  fi
}

log "Evidence directory: $EVIDENCE_DIR"

# ---------------------------------------------------------------------------
# 1) Static checks
# ---------------------------------------------------------------------------
log "Running backend pytest"
(
  cd "$BACKEND_DIR"
  python3 -m pytest -q
) | tee "$EVIDENCE_DIR/01_pytest.txt"

# ---------------------------------------------------------------------------
# 2) Start local API
# ---------------------------------------------------------------------------
require_env LOCAL_JWT

log "Starting local API"
(
  cd "$BACKEND_DIR"
  uvicorn main:app --reload > "$EVIDENCE_DIR/02_local_api.log" 2>&1 &
  echo $! > "$EVIDENCE_DIR/local_api.pid"
)
LOCAL_API_PID="$(cat "$EVIDENCE_DIR/local_api.pid")"
trap 'kill "$LOCAL_API_PID" >/dev/null 2>&1 || true' EXIT
sleep 4

# ---------------------------------------------------------------------------
# 3) Upload media locally
# ---------------------------------------------------------------------------
log "Uploading media to local API"
curl -s -X POST "$LOCAL_API/media/upload" \
  -H "Authorization: Bearer $LOCAL_JWT" \
  -F "file=@$IMAGE_FILE" \
  | tee "$EVIDENCE_DIR/03_local_media_upload.json"

LOCAL_IMAGE_ID="$(json_get "$EVIDENCE_DIR/03_local_media_upload.json" "image_id")"
if [[ -z "$LOCAL_IMAGE_ID" ]]; then
  echo "Local media upload did not return image_id" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 4) Local validation (positive)
# ---------------------------------------------------------------------------
log "Running local debug queue validation (positive)"
curl -s -X POST "$LOCAL_API/debug/queue/validate" \
  -H "Authorization: Bearer $LOCAL_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "url":"'"$COMMENT_URL"'",
    "jobs":[{"type":"reply_comment","text":"this is a unique lowercase reply for verification","target_comment_url":"'"$COMMENT_URL"'","image_id":"'"$LOCAL_IMAGE_ID"'"}]
  }' \
  | tee "$EVIDENCE_DIR/04_local_validate_positive.json"

# ---------------------------------------------------------------------------
# 5) Local validation (negative)
# ---------------------------------------------------------------------------
log "Running local debug queue validation (negative missing comment_id)"
curl -s -X POST "$LOCAL_API/debug/queue/validate" \
  -H "Authorization: Bearer $LOCAL_JWT" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.facebook.com/permalink.php?id=61574636237654","jobs":[{"type":"reply_comment","text":"x","target_comment_url":"https://www.facebook.com/permalink.php?id=61574636237654","image_id":"bad"}]}' \
  | tee "$EVIDENCE_DIR/05_local_validate_negative.json"

# ---------------------------------------------------------------------------
# 6) Local dedupe dry-run
# ---------------------------------------------------------------------------
log "Running local dedupe dry-run"
curl -s -X POST "$LOCAL_API/workflow/dedupe-profile-names" \
  -H "Authorization: Bearer $LOCAL_JWT" \
  -H "Content-Type: application/json" \
  -d '{"mode":"dry_run"}' \
  | tee "$EVIDENCE_DIR/06_local_dedupe_dry_run.json"

# ---------------------------------------------------------------------------
# Optional production phase
# ---------------------------------------------------------------------------
if [[ "${RUN_PROD:-0}" != "1" ]]; then
  log "RUN_PROD!=1, skipping production verification suite"
  exit 0
fi

require_env CLAUDE_API_KEY

log "Collecting Railway status/logs"
(
  cd "$ROOT"
  railway status || true
) | tee "$EVIDENCE_DIR/07_railway_status.txt"
(
  cd "$ROOT"
  railway logs --tail 200 || railway logs || true
) | tee "$EVIDENCE_DIR/08_railway_logs.txt"

log "Uploading production media"
curl -s -X POST "$PROD_API/media/upload" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -F "file=@$IMAGE_FILE" \
  | tee "$EVIDENCE_DIR/09_prod_media_upload.json"

PROD_IMAGE_ID="$(json_get "$EVIDENCE_DIR/09_prod_media_upload.json" "image_id")"
if [[ -z "$PROD_IMAGE_ID" ]]; then
  echo "Production media upload did not return image_id" >&2
  exit 1
fi

log "Enqueuing production reply+image job"
curl -s -X POST "$PROD_API/queue" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "X-Idempotency-Key: reply-image-e2e-001" \
  -H "Content-Type: application/json" \
  -d '{
    "url":"'"$COMMENT_URL"'",
    "duration_minutes":10,
    "jobs":[{"type":"reply_comment","text":"unique lowercase reply e2e check 4418405568392620","target_comment_url":"'"$COMMENT_URL"'","image_id":"'"$PROD_IMAGE_ID"'"}]
  }' \
  | tee "$EVIDENCE_DIR/10_prod_queue_enqueue.json"

log "Fetching production queue history"
curl -s "$PROD_API/queue/history?limit=20" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  | tee "$EVIDENCE_DIR/11_prod_queue_history.json"

log "Re-submitting duplicate reply text to trigger anti-duplicate guard"
curl -s -X POST "$PROD_API/queue" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "X-Idempotency-Key: reply-image-e2e-002" \
  -H "Content-Type: application/json" \
  -d '{
    "url":"'"$COMMENT_URL"'",
    "duration_minutes":10,
    "jobs":[{"type":"reply_comment","text":"unique lowercase reply e2e check 4418405568392620","target_comment_url":"'"$COMMENT_URL"'","image_id":"'"$PROD_IMAGE_ID"'"}]
  }' \
  | tee "$EVIDENCE_DIR/12_prod_duplicate_guard.json"

log "Running production dedupe dry-run"
curl -s -X POST "$PROD_API/workflow/dedupe-profile-names" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode":"dry_run"}' \
  | tee "$EVIDENCE_DIR/13_prod_dedupe_dry_run.json"

PLAN_ID="$(json_get "$EVIDENCE_DIR/13_prod_dedupe_dry_run.json" "plan_id")"

if [[ -n "$PLAN_ID" ]]; then
  log "Running production dedupe apply"
  curl -s -X POST "$PROD_API/workflow/dedupe-profile-names" \
    -H "X-API-Key: $CLAUDE_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"mode":"apply","plan_id":"'"$PLAN_ID"'"}' \
    | tee "$EVIDENCE_DIR/14_prod_dedupe_apply.json"
else
  log "No PLAN_ID returned from dry-run, skipping apply"
fi

log "Runbook complete"

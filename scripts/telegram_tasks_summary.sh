#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/crm_tsm/.env}"
LIMIT="${TELEGRAM_REPORT_LIMIT:-${TELEGRAM_TASKS_LIMIT:-10}}"
CRM_TIMEZONE="${CRM_TIMEZONE:-Europe/Istanbul}"
REPORT_DATE="${CRM_REPORT_DATE:-$(TZ="$CRM_TIMEZONE" date +%F)}"
DB_CONTAINER="${LEAN_DB_CONTAINER:-crm_tsm_leantime_db}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"
CHAT_ID="${TELEGRAM_NOTIFY_CHAT_ID:-${TELEGRAM_ALLOWED_CHAT_ID:-}}"
: "${CHAT_ID:?TELEGRAM_NOTIFY_CHAT_ID or TELEGRAM_ALLOWED_CHAT_ID is required}"

DB_USER="${TELEGRAM_DB_USER:-${LEAN_DB_USER:-}}"
DB_PASS="${TELEGRAM_DB_PASSWORD:-${LEAN_DB_PASSWORD:-}}"

: "${DB_USER:?TELEGRAM_DB_USER or LEAN_DB_USER is required}"
: "${DB_PASS:?TELEGRAM_DB_PASSWORD or LEAN_DB_PASSWORD is required}"
: "${LEAN_DB_DATABASE:?LEAN_DB_DATABASE is required}"

APP_URL="${LEAN_APP_URL:-https://crm.equippulse.com}"

DB_CLIENT="$(docker exec "$DB_CONTAINER" sh -c 'command -v mariadb || command -v mysql' | tr -d '\r')"
if [[ -z "$DB_CLIENT" ]]; then
  echo "ERROR: mariadb/mysql client not found inside $DB_CONTAINER" >&2
  exit 1
fi

run_sql() {
  local sql="$1"
  docker exec -i \
    -e MYSQL_PWD="$DB_PASS" \
    "$DB_CONTAINER" \
    "$DB_CLIENT" \
    -u"$DB_USER" \
    "$LEAN_DB_DATABASE" \
    --default-character-set=utf8mb4 \
    --batch --raw --skip-column-names <<< "$sql"
}

base_select() {
  cat <<SQL
SELECT
  t.id,
  COALESCE(NULLIF(t.headline, ''), '(без названия)') AS headline,
  COALESCE(NULLIF(p.name, ''), 'Без проекта') AS project_name,
  COALESCE(NULLIF(TRIM(CONCAT(COALESCE(u.firstname, ''), ' ', COALESCE(u.lastname, ''))), ''), 'Без ответственного') AS assignee,
  COALESCE(DATE_FORMAT(t.dateToFinish, '%d.%m.%Y'), 'без срока') AS due_date,
  CASE CAST(COALESCE(NULLIF(t.priority, ''), '0') AS UNSIGNED)
    WHEN 1 THEN 'Critical'
    WHEN 2 THEN 'High'
    WHEN 3 THEN 'Medium'
    WHEN 4 THEN 'Low'
    WHEN 5 THEN 'Lowest'
    ELSE '-'
  END AS priority
FROM zp_tickets t
LEFT JOIN zp_projects p ON p.id = t.projectId
LEFT JOIN zp_user u ON CAST(u.id AS CHAR) = CAST(t.editorId AS CHAR)
WHERE COALESCE(t.type, 'task') <> 'milestone'
  AND t.status NOT IN (0, -1)
  AND (p.state IS NULL OR p.state <> -1)
SQL
}

query_active() {
  run_sql "$(base_select)
ORDER BY
  t.dateToFinish IS NULL,
  t.dateToFinish ASC,
  t.priority ASC,
  t.id DESC
LIMIT $LIMIT;"
}

query_today() {
  run_sql "$(base_select)
  AND t.dateToFinish IS NOT NULL
  AND DATE(t.dateToFinish) = '$REPORT_DATE'
ORDER BY t.dateToFinish ASC, t.priority ASC, t.id DESC
LIMIT $LIMIT;"
}

query_overdue() {
  run_sql "$(base_select)
  AND t.dateToFinish IS NOT NULL
  AND DATE(t.dateToFinish) < '$REPORT_DATE'
ORDER BY t.dateToFinish ASC, t.priority ASC, t.id DESC
LIMIT $LIMIT;"
}

query_unassigned() {
  run_sql "$(base_select)
  AND (t.editorId IS NULL OR t.editorId = '' OR t.editorId = '0')
ORDER BY
  t.dateToFinish IS NULL,
  t.dateToFinish ASC,
  t.priority ASC,
  t.id DESC
LIMIT $LIMIT;"
}

count_rows() {
  local rows="$1"
  if [[ -z "${rows//[$'\n\r\t ']/}" ]]; then
    echo 0
  else
    printf '%s\n' "$rows" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' '
  fi
}

format_rows() {
  local rows="$1"
  local empty_text="$2"

  if [[ "$(count_rows "$rows")" == "0" ]]; then
    printf '%s\n' "$empty_text"
    return
  fi

  local i=0
  while IFS=$'\t' read -r id headline project assignee due_date priority; do
    [[ -z "${id:-}" ]] && continue
    i=$((i + 1))
    printf '%s. #%s %s | срок: %s\n' "$i" "$id" "$headline" "$due_date"
    printf '   Проект: %s\n' "$project"
    printf '   Отв.: %s | Приоритет: %s\n' "$assignee" "$priority"
    printf '   %s/tickets/showTicket/%s\n' "$APP_URL" "$id"
  done <<< "$rows"
}

format_active_rows() {
  local rows="$1"
  local empty_text="$2"

  if [[ "$(count_rows "$rows")" == "0" ]]; then
    printf '%s\n' "$empty_text"
    return
  fi

  local i=0
  while IFS=$'\t' read -r id headline project assignee due_date priority; do
    [[ -z "${id:-}" ]] && continue
    i=$((i + 1))
    printf '%s. %s | срок: %s\n' "$i" "$headline" "$due_date"
  done <<< "$rows"
}

send_telegram() {
  local text="$1"
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="$CHAT_ID" \
    -d disable_web_page_preview=true \
    --data-urlencode "text=$text" >/dev/null
}

ACTIVE_ROWS="$(query_active || true)"
TODAY_ROWS="$(query_today || true)"
OVERDUE_ROWS="$(query_overdue || true)"
UNASSIGNED_ROWS="$(query_unassigned || true)"

ACTIVE_COUNT="$(count_rows "$ACTIVE_ROWS")"
TODAY_COUNT="$(count_rows "$TODAY_ROWS")"
OVERDUE_COUNT="$(count_rows "$OVERDUE_ROWS")"
UNASSIGNED_COUNT="$(count_rows "$UNASSIGNED_ROWS")"

MESSAGE="📌 CRM: задачи на $REPORT_DATE

Итого:
• Активные: $ACTIVE_COUNT
• Сегодня: $TODAY_COUNT
• Просрочено: $OVERDUE_COUNT
• Без ответственного: $UNASSIGNED_COUNT

📋 Активные задачи:
$(format_active_rows "$ACTIVE_ROWS" "Нет активных задач.")

🗓 На сегодня:
$(format_rows "$TODAY_ROWS" "Нет задач на сегодня.")

🔥 Просроченные:
$(format_rows "$OVERDUE_ROWS" "Нет просроченных задач.")

👤 Без ответственного:
$(format_rows "$UNASSIGNED_ROWS" "Нет задач без ответственного.")

CRM: $APP_URL"

send_telegram "$MESSAGE"

printf 'Telegram task summary sent. active=%s today=%s overdue=%s unassigned=%s date=%s\n' \
  "$ACTIVE_COUNT" "$TODAY_COUNT" "$OVERDUE_COUNT" "$UNASSIGNED_COUNT" "$REPORT_DATE"

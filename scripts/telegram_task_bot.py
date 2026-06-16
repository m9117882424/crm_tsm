#!/usr/bin/env python3
"""Telegram command bot for Leantime CRM.

Features:
- /summary, /active, /today, /overdue, /unassigned reports.
- Sends first TELEGRAM_REPORT_LIMIT tasks in chat and CSV document when there are more.
- /addtask interactive task creation flow.

Runtime expects the bot to run on the Leantime host and access MariaDB through docker exec.
"""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ENV_FILE = Path(os.getenv("ENV_FILE", "/opt/crm_tsm/.env"))
STATE_FILE = Path(os.getenv("TELEGRAM_TASK_BOT_STATE", "/opt/crm_tsm/telegram_task_bot_state.json"))
DB_CONTAINER = os.getenv("LEAN_DB_CONTAINER", "crm_tsm_leantime_db")
TIMEZONE_DATE_FORMAT = "%Y-%m-%d"
TELEGRAM_MESSAGE_LIMIT = 3500


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


ENV = load_env(ENV_FILE)

BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = str(ENV.get("TELEGRAM_NOTIFY_CHAT_ID") or ENV.get("TELEGRAM_ALLOWED_CHAT_ID") or "")
ALLOWED_USER_IDS = {
    item.strip()
    for item in ENV.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if item.strip()
}

DB_USER = ENV.get("TELEGRAM_WRITE_DB_USER") or ENV.get("LEAN_DB_USER")
DB_PASS = ENV.get("TELEGRAM_WRITE_DB_PASSWORD") or ENV.get("LEAN_DB_PASSWORD")
DB_NAME = ENV.get("LEAN_DB_DATABASE")
APP_URL = ENV.get("LEAN_APP_URL", "https://crm.equippulse.com").rstrip("/")

DEFAULT_PROJECT = ENV.get("TELEGRAM_DEFAULT_PROJECT", "Monitoring")
DEFAULT_ASSIGNEE = ENV.get("TELEGRAM_DEFAULT_ASSIGNEE", "Diana Ruban")

if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN is missing")
if not ALLOWED_CHAT_ID:
    raise SystemExit("TELEGRAM_NOTIFY_CHAT_ID or TELEGRAM_ALLOWED_CHAT_ID is missing")
if not DB_USER or not DB_PASS or not DB_NAME:
    raise SystemExit("DB credentials are missing")


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {"offset": 0, "dialogs": {}}
    return {"offset": 0, "dialogs": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def api(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = None
    if params is not None:
        data = urllib.parse.urlencode(params).encode("utf-8")
    with urllib.request.urlopen(url, data=data, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def send_message(chat_id: str | int, text: str) -> None:
    chunks = split_message(text)
    for chunk in chunks:
        api(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": chunk,
                "disable_web_page_preview": "true",
            },
        )


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_document(chat_id: str | int, file_path: Path, caption: str = "") -> None:
    boundary = f"----CRMTaskBotBoundary{int(time.time() * 1000)}"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    def add_file(name: str, path: Path) -> None:
        filename = path.name
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        body.extend(b"Content-Type: text/csv; charset=utf-8\r\n\r\n")
        body.extend(path.read_bytes())
        body.extend(b"\r\n")

    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption)
    add_file("document", file_path)
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


def get_db_client() -> str:
    result = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "sh", "-c", "command -v mariadb || command -v mysql"],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


DB_CLIENT = get_db_client()


def run_sql(sql: str) -> str:
    cmd = [
        "docker",
        "exec",
        "-i",
        "-e",
        f"MYSQL_PWD={DB_PASS}",
        DB_CONTAINER,
        DB_CLIENT,
        f"-u{DB_USER}",
        DB_NAME,
        "--default-character-set=utf8mb4",
        "--batch",
        "--raw",
        "--skip-column-names",
    ]

    result = subprocess.run(cmd, input=sql, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def b64_sql(value: str) -> str:
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"CONVERT(FROM_BASE64('{encoded}') USING utf8mb4) COLLATE utf8mb4_unicode_ci"


def sql_date(value: str) -> str:
    return value.replace("'", "")


def one_value(sql: str) -> str:
    output = run_sql(sql)
    return output.splitlines()[0].strip() if output else ""


def report_limit() -> int:
    return int(ENV.get("TELEGRAM_REPORT_LIMIT", "10"))


def report_file_limit() -> int:
    return int(ENV.get("TELEGRAM_REPORT_FILE_LIMIT", "500"))


def rows_count(rows: str) -> int:
    return len([line for line in rows.splitlines() if line.strip()])


def report_date() -> str:
    return datetime.now().date().strftime("%Y-%m-%d")


def tasks_base_from_where() -> str:
    return """
FROM zp_tickets t
LEFT JOIN zp_projects p ON p.id = t.projectId
LEFT JOIN zp_user u ON CAST(u.id AS CHAR) = CAST(t.editorId AS CHAR)
WHERE COALESCE(t.type, 'task') <> 'milestone'
  AND t.status NOT IN (0, -1)
  AND (p.state IS NULL OR p.state <> -1)
"""


def tasks_base_select() -> str:
    return (
        """
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
"""
        + tasks_base_from_where()
    )


def task_where_order(kind: str) -> tuple[str, str]:
    today = report_date()

    if kind == "active":
        where = ""
        order = """
ORDER BY
  t.dateToFinish IS NULL,
  t.dateToFinish ASC,
  t.priority ASC,
  t.id DESC
"""
    elif kind == "today":
        where = f"AND t.dateToFinish IS NOT NULL AND DATE(t.dateToFinish) = '{today}'"
        order = "ORDER BY t.dateToFinish ASC, t.priority ASC, t.id DESC"
    elif kind == "overdue":
        where = f"AND t.dateToFinish IS NOT NULL AND DATE(t.dateToFinish) < '{today}'"
        order = "ORDER BY t.dateToFinish ASC, t.priority ASC, t.id DESC"
    elif kind == "unassigned":
        where = "AND (t.editorId IS NULL OR t.editorId = '' OR t.editorId = '0')"
        order = """
ORDER BY
  t.dateToFinish IS NULL,
  t.dateToFinish ASC,
  t.priority ASC,
  t.id DESC
"""
    else:
        raise ValueError(f"unknown task query kind: {kind}")

    return where, order


def count_tasks(kind: str) -> int:
    where, _ = task_where_order(kind)
    value = one_value(
        f"""
SELECT COUNT(*)
{tasks_base_from_where()}
{where};
"""
    )
    return int(value or 0)


def query_tasks(kind: str, limit: int | None = None) -> str:
    where, order = task_where_order(kind)
    limit_sql = f"LIMIT {int(limit)}" if limit is not None else ""
    return run_sql(
        f"""
{tasks_base_select()}
{where}
{order}
{limit_sql};
"""
    )


def format_task_rows(rows: str, empty_text: str, compact: bool = False) -> str:
    if rows_count(rows) == 0:
        return empty_text

    lines: list[str] = []
    for i, line in enumerate(rows.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        while len(parts) < 6:
            parts.append("")
        ticket_id, headline, project, assignee, due_date, priority = parts[:6]

        if compact:
            lines.append(f"{i}. #{ticket_id} {headline} | срок: {due_date}")
        else:
            lines.append(f"{i}. #{ticket_id} {headline} | срок: {due_date}")
            lines.append(f"   Проект: {project}")
            lines.append(f"   Отв.: {assignee} | Приоритет: {priority}")
            lines.append(f"   {APP_URL}/tickets/showTicket/{ticket_id}")

    return "\n".join(lines)


def make_tasks_csv(kind: str, rows: str, prefix: str = "crm_tasks") -> Path:
    filename = f"{prefix}_{kind}_{report_date()}.csv"
    path = Path(tempfile.gettempdir()) / filename

    section_titles = {
        "active": "Активные",
        "today": "Сегодня",
        "overdue": "Просроченные",
        "unassigned": "Без ответственного",
    }

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Раздел", "ID", "Название", "Проект", "Ответственный", "Срок", "Приоритет", "Ссылка"])

        for line in rows.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            while len(parts) < 6:
                parts.append("")
            ticket_id, headline, project, assignee, due_date, priority = parts[:6]
            writer.writerow(
                [
                    section_titles.get(kind, kind),
                    ticket_id,
                    headline,
                    project,
                    assignee,
                    due_date,
                    priority,
                    f"{APP_URL}/tickets/showTicket/{ticket_id}",
                ]
            )

    return path


def make_summary_csv(all_rows: dict[str, str]) -> Path:
    filename = f"crm_summary_{report_date()}.csv"
    path = Path(tempfile.gettempdir()) / filename
    section_titles = {
        "active": "Активные",
        "today": "Сегодня",
        "overdue": "Просроченные",
        "unassigned": "Без ответственного",
    }

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Раздел", "ID", "Название", "Проект", "Ответственный", "Срок", "Приоритет", "Ссылка"])

        for kind, rows in all_rows.items():
            for line in rows.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                while len(parts) < 6:
                    parts.append("")
                ticket_id, headline, project, assignee, due_date, priority = parts[:6]
                writer.writerow(
                    [
                        section_titles.get(kind, kind),
                        ticket_id,
                        headline,
                        project,
                        assignee,
                        due_date,
                        priority,
                        f"{APP_URL}/tickets/showTicket/{ticket_id}",
                    ]
                )

    return path


def build_tasks_message(kind: str, total: int, rows: str) -> str:
    titles = {
        "active": "📋 Активные задачи",
        "today": "🗓 Задачи на сегодня",
        "overdue": "🔥 Просроченные задачи",
        "unassigned": "👤 Задачи без ответственного",
    }
    empty = {
        "active": "Нет активных задач.",
        "today": "Нет задач на сегодня.",
        "overdue": "Нет просроченных задач.",
        "unassigned": "Нет задач без ответственного.",
    }

    limit = report_limit()
    shown = min(total, limit)
    compact = kind == "active"
    return (
        f"{titles[kind]}\n"
        f"Показано: {shown} из {total}\n\n"
        f"{format_task_rows(rows, empty[kind], compact=compact)}"
    )


def send_tasks_report(chat_id: str | int, kind: str) -> None:
    limit = report_limit()
    file_limit = report_file_limit()
    total = count_tasks(kind)
    rows = query_tasks(kind, limit=limit)

    send_message(chat_id, build_tasks_message(kind, total, rows))

    if total > limit:
        file_rows = query_tasks(kind, limit=file_limit)
        path = make_tasks_csv(kind, file_rows)
        try:
            caption = f"Полный список: {min(total, file_limit)} из {total}"
            if total > file_limit:
                caption += f"\nОграничение файла: первые {file_limit} задач."
            send_document(chat_id, path, caption)
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def send_summary_report(chat_id: str | int) -> None:
    limit = report_limit()
    file_limit = report_file_limit()
    kinds = ["active", "today", "overdue", "unassigned"]
    totals = {kind: count_tasks(kind) for kind in kinds}
    shown_rows = {kind: query_tasks(kind, limit=limit) for kind in kinds}

    message = (
        f"📌 CRM: сводка на {report_date()}\n\n"
        f"Итого:\n"
        f"• Активные: {totals['active']}\n"
        f"• Сегодня: {totals['today']}\n"
        f"• Просрочено: {totals['overdue']}\n"
        f"• Без ответственного: {totals['unassigned']}\n\n"
        f"📋 Активные задачи — показано {min(totals['active'], limit)} из {totals['active']}:\n"
        f"{format_task_rows(shown_rows['active'], 'Нет активных задач.', compact=True)}\n\n"
        f"🗓 На сегодня — показано {min(totals['today'], limit)} из {totals['today']}:\n"
        f"{format_task_rows(shown_rows['today'], 'Нет задач на сегодня.')}\n\n"
        f"🔥 Просроченные — показано {min(totals['overdue'], limit)} из {totals['overdue']}:\n"
        f"{format_task_rows(shown_rows['overdue'], 'Нет просроченных задач.')}\n\n"
        f"👤 Без ответственного — показано {min(totals['unassigned'], limit)} из {totals['unassigned']}:\n"
        f"{format_task_rows(shown_rows['unassigned'], 'Нет задач без ответственного.')}\n\n"
        f"CRM: {APP_URL}"
    )

    send_message(chat_id, message)

    if any(total > limit for total in totals.values()):
        all_rows = {kind: query_tasks(kind, limit=file_limit) for kind in kinds}
        path = make_summary_csv(all_rows)
        try:
            send_document(chat_id, path, "Полная сводка CRM в CSV")
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def build_help_message() -> str:
    return f"""Команды CRM:

/summary — общая сводка CRM
/active — активные задачи
/today — задачи на сегодня
/overdue — просроченные задачи
/unassigned — задачи без ответственного
/addtask — создать задачу
/cancel — отменить создание задачи

Если задач больше {report_limit()}, бот отправит первые {report_limit()} в чат и приложит CSV-файл со списком.

Создание задачи:
/addtask
1. название
2. описание
3. дата окончания

По умолчанию:
Проект: {DEFAULT_PROJECT}
Ответственный: {DEFAULT_ASSIGNEE}"""


def find_project_id() -> str:
    return one_value(
        f"""
SELECT id
FROM zp_projects
WHERE name = {b64_sql(DEFAULT_PROJECT)}
  AND (state IS NULL OR state <> -1)
ORDER BY id DESC
LIMIT 1;
"""
    )


def find_assignee_id() -> str:
    parts = DEFAULT_ASSIGNEE.split()
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""

    user_id = one_value(
        f"""
SELECT id
FROM zp_user
WHERE firstname = {b64_sql(first)}
  AND lastname = {b64_sql(last)}
  AND status = 'A'
ORDER BY id DESC
LIMIT 1;
"""
    )
    if user_id:
        return user_id

    return one_value(
        f"""
SELECT id
FROM zp_user
WHERE TRIM(CONCAT(firstname, ' ', lastname)) = {b64_sql(DEFAULT_ASSIGNEE)}
ORDER BY id DESC
LIMIT 1;
"""
    )


def parse_due_date(text: str) -> str | None:
    value = text.strip().lower()
    today = datetime.now().date()

    if value in {"сегодня", "today"}:
        return today.strftime(TIMEZONE_DATE_FORMAT)
    if value in {"завтра", "tomorrow"}:
        return (today + timedelta(days=1)).strftime(TIMEZONE_DATE_FORMAT)

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().strftime(TIMEZONE_DATE_FORMAT)
        except ValueError:
            return None

    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", value):
        try:
            return datetime.strptime(value, "%d.%m.%Y").date().strftime(TIMEZONE_DATE_FORMAT)
        except ValueError:
            return None

    if re.fullmatch(r"\d{1,2}\.\d{1,2}", value):
        try:
            return datetime.strptime(f"{value}.{today.year}", "%d.%m.%Y").date().strftime(TIMEZONE_DATE_FORMAT)
        except ValueError:
            return None

    return None


def create_task(title: str, description: str, due_date: str) -> str:
    project_id = find_project_id()
    if not project_id:
        raise RuntimeError(f"Проект не найден: {DEFAULT_PROJECT}")

    assignee_id = find_assignee_id()
    if not assignee_id:
        raise RuntimeError(f"Ответственный не найден: {DEFAULT_ASSIGNEE}")

    due_dt = f"{sql_date(due_date)} 23:59:59"

    sql = f"""
INSERT INTO zp_tickets
(
  projectId,
  headline,
  description,
  date,
  dateToFinish,
  priority,
  status,
  userId,
  editorId,
  type,
  sortindex,
  kanbanSortIndex,
  modified
)
VALUES
(
  {int(project_id)},
  {b64_sql(title)},
  {b64_sql(description)},
  NOW(),
  '{due_dt}',
  '3',
  3,
  {int(assignee_id)},
  '{int(assignee_id)}',
  'task',
  UNIX_TIMESTAMP() * 1000,
  UNIX_TIMESTAMP() * 1000,
  NOW()
);

SELECT LAST_INSERT_ID();
"""
    ticket_id = one_value(sql)
    if not ticket_id:
        raise RuntimeError("Задача не создана: пустой LAST_INSERT_ID()")
    return ticket_id


def is_allowed(message: dict[str, Any]) -> bool:
    chat_id = str(message.get("chat", {}).get("id", ""))
    from_id = str(message.get("from", {}).get("id", ""))

    if chat_id != ALLOWED_CHAT_ID:
        return False
    if ALLOWED_USER_IDS and from_id not in ALLOWED_USER_IDS:
        return False
    return True


def dialog_key(message: dict[str, Any]) -> str:
    chat_id = str(message["chat"]["id"])
    from_id = str(message["from"]["id"])
    return f"{chat_id}:{from_id}"


def handle_message(state: dict[str, Any], message: dict[str, Any]) -> None:
    if not is_allowed(message):
        return

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if not text:
        return

    key = dialog_key(message)
    dialogs = state.setdefault("dialogs", {})
    command = text.split()[0].split("@")[0].lower()

    if command in {"/cancel", "/отмена"}:
        dialogs.pop(key, None)
        send_message(chat_id, "Операция отменена.")
        return

    if command in {"/start", "/help"}:
        send_message(chat_id, build_help_message())
        return

    if command == "/summary":
        send_summary_report(chat_id)
        return

    if command == "/active":
        send_tasks_report(chat_id, "active")
        return

    if command == "/today":
        send_tasks_report(chat_id, "today")
        return

    if command == "/overdue":
        send_tasks_report(chat_id, "overdue")
        return

    if command == "/unassigned":
        send_tasks_report(chat_id, "unassigned")
        return

    if command in {"/addtask", "/newtask", "/task"}:
        dialogs[key] = {
            "step": "title",
            "data": {},
            "created_at": int(time.time()),
        }
        send_message(chat_id, "Введите название задачи:")
        return

    dialog = dialogs.get(key)
    if not dialog:
        return

    step = dialog.get("step")
    data = dialog.setdefault("data", {})

    if step == "title":
        if len(text) < 3:
            send_message(chat_id, "Название слишком короткое. Введите название задачи:")
            return
        if len(text) > 255:
            send_message(chat_id, "Название слишком длинное. Максимум 255 символов. Введите короче:")
            return
        data["title"] = text
        dialog["step"] = "description"
        send_message(chat_id, "Введите описание задачи:")
        return

    if step == "description":
        data["description"] = text
        dialog["step"] = "due_date"
        send_message(chat_id, "Введите дату окончания: 20.06.2026, 2026-06-20, сегодня или завтра")
        return

    if step == "due_date":
        due_date = parse_due_date(text)
        if not due_date:
            send_message(chat_id, "Не понял дату. Формат: 20.06.2026 или 2026-06-20. Повторите дату окончания:")
            return

        try:
            ticket_id = create_task(
                title=data["title"],
                description=data["description"],
                due_date=due_date,
            )
        except Exception as exc:
            send_message(chat_id, f"❌ Не удалось создать задачу:\n{exc}")
            return

        dialogs.pop(key, None)
        send_message(
            chat_id,
            "✅ Задача создана\n"
            f"#{ticket_id} {data['title']}\n"
            f"Проект: {DEFAULT_PROJECT}\n"
            f"Ответственный: {DEFAULT_ASSIGNEE}\n"
            f"Срок: {due_date}\n"
            f"{APP_URL}/tickets/showTicket/{ticket_id}",
        )


def main() -> None:
    state = load_state()
    state.setdefault("offset", 0)
    state.setdefault("dialogs", {})

    send_message(ALLOWED_CHAT_ID, "✅ Telegram task bot запущен. Команда: /help")

    while True:
        try:
            updates = api(
                "getUpdates",
                {
                    "offset": int(state.get("offset", 0)) + 1,
                    "timeout": 30,
                    "allowed_updates": json.dumps(["message"]),
                },
            )

            for update in updates.get("result", []):
                state["offset"] = max(int(state.get("offset", 0)), int(update["update_id"]))
                message = update.get("message")
                if message:
                    handle_message(state, message)

            save_state(state)

        except Exception as exc:
            try:
                send_message(ALLOWED_CHAT_ID, f"⚠️ Telegram task bot error:\n{exc}")
            except Exception:
                pass
            time.sleep(5)


if __name__ == "__main__":
    main()

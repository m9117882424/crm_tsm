# Telegram CRM integration

Current production bot: `@tsm_enerji_crm_bot`.

Production group: `TSM CRM Уведомления`.

Chat ID: `-5425439741`.

## Components

### Daily summary script

Path on server:

```bash
/opt/crm_tsm/telegram_tasks_summary.sh
```

Repository source:

```bash
scripts/telegram_tasks_summary.sh
```

Purpose:

- sends daily CRM task summary to Telegram;
- reads Leantime tasks directly from MariaDB;
- active task section is compact: task name and due date only;
- today, overdue, and unassigned sections include project, assignee, priority, and task link.

Recommended cron:

```cron
30 8 * * * /opt/crm_tsm/telegram_tasks_summary.sh >> /var/log/crm_tsm_telegram_tasks.log 2>&1
```

### Command bot service

Path on server:

```bash
/opt/crm_tsm/telegram_task_bot.py
```

Repository source:

```bash
scripts/telegram_task_bot.py
```

Systemd service:

```bash
/etc/systemd/system/crm-telegram-task-bot.service
```

Repository source:

```bash
systemd/crm-telegram-task-bot.service
```

The service uses the same Telegram bot token as the daily summary script. A second Telegram bot is not required.

## Telegram commands

```text
/help        list available commands
/summary     summary report
/active      active tasks
/today       tasks due today
/overdue     overdue tasks
/unassigned  tasks without assignee
/addtask     create task interactively
/cancel      cancel task creation
```

## Reports with more than 10 tasks

Default behavior:

- show first `TELEGRAM_REPORT_LIMIT=10` tasks in Telegram chat;
- if total count is greater than the limit, attach a CSV file with the full task list;
- CSV opens correctly in Excel and includes section, ID, title, project, assignee, due date, priority, and CRM link.

Default file limit:

```env
TELEGRAM_REPORT_FILE_LIMIT=500
```

## Task creation flow

Command:

```text
/addtask
```

Dialog:

1. title;
2. description;
3. due date.

Default task target:

```env
TELEGRAM_DEFAULT_PROJECT=Monitoring
TELEGRAM_DEFAULT_ASSIGNEE="Diana Ruban"
```

Task creation rules:

- `date` is set to current timestamp;
- `dateToFinish` is set from the Telegram dialog;
- Leantime project is `Monitoring`;
- assignee is `Diana Ruban`;
- status is `3`;
- type is `task`;
- priority is `3`.

Supported due date inputs:

```text
20.06.2026
2026-06-20
20.06
сегодня
завтра
```

## Database users

Recommended least-privilege users:

### Read-only user for report script

```sql
CREATE USER IF NOT EXISTS 'telegram_ro'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT ON `leantime`.* TO 'telegram_ro'@'%';
FLUSH PRIVILEGES;
```

Environment:

```env
TELEGRAM_DB_USER=telegram_ro
TELEGRAM_DB_PASSWORD=CHANGE_ME
```

### Write user for command bot

```sql
CREATE USER IF NOT EXISTS 'telegram_bot'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT ON `leantime`.`zp_projects` TO 'telegram_bot'@'%';
GRANT SELECT ON `leantime`.`zp_user` TO 'telegram_bot'@'%';
GRANT SELECT, INSERT ON `leantime`.`zp_tickets` TO 'telegram_bot'@'%';
FLUSH PRIVILEGES;
```

Environment:

```env
TELEGRAM_WRITE_DB_USER=telegram_bot
TELEGRAM_WRITE_DB_PASSWORD=CHANGE_ME
```

## Required Telegram bot setting

For group dialog mode, BotFather privacy must be disabled for the existing bot:

```text
@BotFather
/setprivacy
@tsm_enerji_crm_bot
Disable
```

Without this setting, the bot may see `/addtask` but not the ordinary text replies with title, description, and due date.

## Install/update commands on server

Copy repository files into runtime paths:

```bash
cd /opt/crm_tsm

install -m 755 scripts/telegram_task_bot.py /opt/crm_tsm/telegram_task_bot.py
install -m 755 scripts/telegram_tasks_summary.sh /opt/crm_tsm/telegram_tasks_summary.sh
install -m 644 systemd/crm-telegram-task-bot.service /etc/systemd/system/crm-telegram-task-bot.service

systemctl daemon-reload
systemctl enable --now crm-telegram-task-bot.service
systemctl restart crm-telegram-task-bot.service
```

Set Telegram menu commands:

```bash
cd /opt/crm_tsm
set -a
source .env
set +a

curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setMyCommands" \
  -H "Content-Type: application/json" \
  -d '{
    "commands": [
      {"command": "summary", "description": "Общая сводка CRM"},
      {"command": "active", "description": "Активные задачи"},
      {"command": "today", "description": "Задачи на сегодня"},
      {"command": "overdue", "description": "Просроченные задачи"},
      {"command": "unassigned", "description": "Без ответственного"},
      {"command": "addtask", "description": "Создать задачу"},
      {"command": "cancel", "description": "Отменить создание задачи"},
      {"command": "help", "description": "Помощь"}
    ]
  }'
```

## Timeout-noise fix

Telegram API long polling can sometimes return `The read operation timed out`. This is a transient network condition and should not be posted into the group.

Patch helper committed in repository:

```bash
scripts/apply_telegram_timeout_fix.py
```

Apply it in a repository checkout:

```bash
cd /opt/crm_tsm
python3 scripts/apply_telegram_timeout_fix.py
install -m 755 scripts/telegram_task_bot.py /opt/crm_tsm/telegram_task_bot.py
systemctl restart crm-telegram-task-bot.service
```

After the patch, read timeouts are written only to `/var/log/crm_tsm_telegram_task_bot.log`; non-timeout runtime errors are still sent to Telegram.

## Operations

Restart bot:

```bash
systemctl restart crm-telegram-task-bot.service
```

Status:

```bash
systemctl status crm-telegram-task-bot.service --no-pager
```

Logs:

```bash
tail -f /var/log/crm_tsm_telegram_task_bot.log
```

Daily summary manual test:

```bash
/opt/crm_tsm/telegram_tasks_summary.sh
```

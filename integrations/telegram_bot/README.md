# Telegram bot integration

Заготовка сервиса для связи OpenProject с Telegram-группой монтажников.

## Запуск

```bash
docker compose --profile bot up -d --build
```

Проверка:

```bash
curl http://localhost:8090/health
```

## Webhook endpoints

- `POST /openproject/webhook` — события из OpenProject.
- `POST /telegram/webhook` — события из Telegram.

## Следующие задачи разработки

- подключить Telegram Bot API webhook;
- разобрать формат OpenProject webhook;
- отправлять уведомления о новых задачах;
- реализовать `/done TASK_ID`;
- реализовать `/comment TASK_ID текст`;
- прикреплять фото к задаче через OpenProject API.

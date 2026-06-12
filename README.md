# crm_tsm

Локальный пилот замены Bitrix-задач для TSM: проекты, задачи, сроки, ответственные, роли, файлы, напоминания, календарь и будущая Telegram-интеграция для монтажников.

## Стек пилота

- **OpenProject** — ядро задач, проектов, сроков, ролей, вложений и календаря.
- **Telegram bot** — заготовка интеграции, по умолчанию не запускается.
- **Docker Compose** — локальный запуск.

## Быстрый запуск на Windows PowerShell

```powershell
git init crm_tsm
cd crm_tsm

# Скопировать файлы проекта в эту папку, затем:
Copy-Item .env.example .env

$bytes = New-Object byte[] 64
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$key = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
(Get-Content .env) -replace "CHANGE_ME_GENERATE_WITH_OPENSSL_RAND_HEX_64", $key | Set-Content .env

docker compose up -d
```

Открыть: http://localhost:8080

Первый вход OpenProject обычно: `admin` / `admin`. После входа сразу сменить пароль.

## Быстрый запуск на Linux/macOS

```bash
git init crm_tsm
cd crm_tsm
cp .env.example .env
sed -i "s/CHANGE_ME_GENERATE_WITH_OPENSSL_RAND_HEX_64/$(openssl rand -hex 64)/" .env
docker compose up -d
```

Открыть: http://localhost:8080

## Первый коммит

```bash
git add .
git commit -m "init local openproject crm_tsm"
```

## Команды

```bash
# старт
make up

# остановка
make down

# логи
make logs

# запуск с Telegram-заготовкой
make bot
```

## Что настраиваем в OpenProject первым этапом

1. Создать проекты: `Монтаж GPS`, `Сервис`, `АЗС`, `Пассажирский транспорт`, `Тестовый проект`.
2. Создать роли: `Администратор`, `Руководитель проекта`, `Координатор`, `Инженер`, `Монтажник`, `Наблюдатель`.
3. Настроить типы задач: `Монтаж`, `Демонтаж`, `Проверка`, `Дефект`, `Заявка`, `Документ`, `Согласование`.
4. Настроить статусы: `Новая`, `Назначена`, `В работе`, `Ожидает`, `На проверке`, `Закрыта`, `Отменена`.
5. Проверить вложения файлов в задачах.
6. Проверить календарь и сроки.
7. После этого подключать Telegram MVP.

## Важное ограничение пилота

Сейчас используется OpenProject all-in-one контейнер. Это удобно для локального теста, но для production надо перейти на официальный Docker Compose с раздельными сервисами, отдельным PostgreSQL, backup-процедурами и reverse proxy с HTTPS.

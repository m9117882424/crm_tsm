.PHONY: up down restart logs ps bot bot-logs

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

bot:
	docker compose --profile bot up -d --build

bot-logs:
	docker compose logs -f --tail=200 telegram_bot

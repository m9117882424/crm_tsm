from typing import Any

import httpx
from fastapi import FastAPI, Request
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str = "CHANGE_ME"
    telegram_allowed_chat_id: str = "CHANGE_ME"
    openproject_base_url: str = "http://openproject"
    openproject_api_token: str = "CHANGE_ME"


settings = Settings()
app = FastAPI(title="crm_tsm Telegram Integration", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/openproject/webhook")
async def openproject_webhook(request: Request) -> dict[str, Any]:
    """Receive OpenProject webhooks.

    MVP behavior: log payload and return OK.
    Next step: parse work package events and send Telegram messages.
    """
    payload = await request.json()
    print("OpenProject webhook payload:", payload)
    return {"status": "received"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, Any]:
    """Receive Telegram updates.

    MVP behavior: log payload and return OK.
    Next step: support /done, /comment, /today, /overdue.
    """
    payload = await request.json()
    print("Telegram webhook payload:", payload)
    return {"status": "received"}


async def send_telegram_message(text: str) -> None:
    if settings.telegram_bot_token == "CHANGE_ME":
        print("Telegram token is not configured. Message skipped:", text)
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            url,
            json={
                "chat_id": settings.telegram_allowed_chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
        response.raise_for_status()

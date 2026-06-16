#!/usr/bin/env python3
"""Apply Telegram task bot timeout-noise fix.

The patch changes scripts/telegram_task_bot.py so transient Telegram API
read timeouts are logged only and are not sent back into the Telegram group.
"""

from pathlib import Path

p = Path("scripts/telegram_task_bot.py")
s = p.read_text()

if "import socket\n" not in s:
    s = s.replace("import re\n", "import re\nimport socket\n")

if "import urllib.error\n" not in s:
    s = s.replace("import urllib.parse\n", "import urllib.error\nimport urllib.parse\n")

s = s.replace(
    "with urllib.request.urlopen(url, data=data, timeout=60) as response:",
    "with urllib.request.urlopen(url, data=data, timeout=120) as response:",
)

marker = """def load_state() -> dict[str, Any]:
"""
helpers = """def is_transient_timeout(exc: Exception) -> bool:
    error_text = str(exc).lower()
    return (
        isinstance(exc, TimeoutError)
        or isinstance(exc, socket.timeout)
        or "timed out" in error_text
        or "timeout" in error_text
    )


def log_error(prefix: str, exc: Exception) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {prefix}: {exc}", flush=True)


"""

if "def is_transient_timeout(" not in s:
    s = s.replace(marker, helpers + marker)

old_startup = """    send_message(ALLOWED_CHAT_ID, "✅ Telegram task bot запущен. Команда: /help")
"""
new_startup = """    try:
        send_message(ALLOWED_CHAT_ID, "✅ Telegram task bot запущен. Команда: /help")
    except Exception as exc:
        log_error("Telegram startup notification failed", exc)
"""

if old_startup in s:
    s = s.replace(old_startup, new_startup)

old_except = """        except Exception as exc:
            try:
                send_message(ALLOWED_CHAT_ID, f"⚠️ Telegram task bot error:\\n{exc}")
            except Exception:
                pass
            time.sleep(5)
"""

new_except = """        except Exception as exc:
            log_error("Telegram task bot error", exc)

            # Telegram read timeouts are transient network/API issues.
            # Keep them in the service log and do not spam the group.
            if not is_transient_timeout(exc):
                try:
                    send_message(ALLOWED_CHAT_ID, f"⚠️ Telegram task bot error:\\n{exc}")
                except Exception as notify_exc:
                    log_error("Telegram error notification failed", notify_exc)

            time.sleep(5)
"""

if old_except not in s:
    raise SystemExit("Expected main exception block was not found")

s = s.replace(old_except, new_except)
p.write_text(s)
print("telegram_task_bot.py timeout-noise fix applied")

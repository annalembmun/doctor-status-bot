"""
bot.py — Telegram-бот «Статус врача».

Что делает:
  • присылаешь фамилию (Иванова, Сидорова, Петров Пётр) — получаешь карточку статуса
  • /summary — вся сводка целиком
  • /list — список врачей, требующих внимания

Запуск:
  1) pip install -r requirements.txt
  2) положить токен в .env (BOT_TOKEN=...)
  3) python bot.py

Зависимость от Telegram — только здесь; db.py и report.py работают без сети.
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


def _load_dotenv(path: str = ".env") -> None:
    """Читаем BOT_TOKEN из .env, не притаскивая лишних зависимостей."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

import db      # noqa: E402
import report  # noqa: E402

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("doctor-status-bot")

WELCOME = (
    "Привет! Я показываю статус врачей из утренней сводки.\n\n"
    "Просто пришлите фамилию или имя — например:\n"
    "  • Иванова\n"
    "  • Сидорова Мария\n"
    "  • Петров Пётр Сергеевич\n\n"
    "Команды:\n"
    "  /list — врачи, требующие внимания\n"
    "  /summary — полная сводка за день\n"
    "  /help — эта справка"
)


def _chunks(text: str, size: int = 3800) -> list[str]:
    """Telegram режет сообщения на 4096 символах — бьём заранее, по строкам."""
    if len(text) <= size:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


async def _send_long(update: Update, text: str) -> None:
    for part in _chunks(text):
        await update.effective_message.reply_text(part)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    await _send_long(update, report.build_overview())


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    await _send_long(update, report.build_overview())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Любой текст = запрос по фамилии."""
    query = (update.message.text or "").strip()
    if not query:
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        answer = report.build_report(query)
    except Exception:  # noqa: BLE001 — бот не должен падать от одной ошибки
        log.exception("Ошибка при построении отчёта по запросу %r", query)
        answer = "Внутренняя ошибка при обработке запроса. Попробуйте другую формулировку."
    await _send_long(update, answer)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Необработанная ошибка", exc_info=context.error)


def main() -> None:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Не задан BOT_TOKEN.\n"
            "Создайте файл .env рядом с bot.py со строкой:\n"
            "    BOT_TOKEN=123456:AA...\n"
            "или выполните: export BOT_TOKEN=123456:AA..."
        )

    # RESET_DB=1 — пересоздать базу при старте (нужно, если поменяли данные в db.py,
    # а база уже лежит на постоянном диске Railway и содержит старую версию).
    reset = os.environ.get("RESET_DB", "").strip().lower() in {"1", "true", "yes"}
    db.init_db(force=reset)
    if reset:
        log.info("RESET_DB=1 → база пересоздана с нуля: %s", db.DB_PATH)
    else:
        log.info("База готова: %s", db.DB_PATH)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    log.info("Бот запущен. Жду сообщения…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

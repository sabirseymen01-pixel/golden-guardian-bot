#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal, sağlam Telegram bot (polling). 
Deploy on Render as Background Worker (no open port required).
Requirements: python-telegram-bot[job-queue]==21.6, aiohttp
Environment:
  TELEGRAM_BOT_TOKEN (required)
  USE_POLLING=1       (recommended)
"""
import logging
import os
import sys
import traceback
from datetime import datetime
from telegram import Update, ParseMode
from telegram.error import InvalidToken, NetworkError, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, Defaults

# Basic logging
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("MinimalBot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN ortam değişkeni tanımlı değil. Çıkılıyor.")
    sys.exit(1)

# Small helper to log exceptions to file for Render logs inspection
def log_startup_exception(exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.critical("Startup exception:\n%s", tb)
    try:
        with open("bot_startup_error.log", "a", encoding="utf-8") as f:
            f.write(f"\n\n=== {datetime.utcnow().isoformat()} UTC ===\n")
            f.write(tb)
    except Exception as e:
        logger.error("Could not write startup log file: %s", e)

# Simple command handlers
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        await update.message.reply_text(
            f"Merhaba {user.first_name or 'kullanıcı'}! Bot çalışıyor ✅",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        logger.warning("start_cmd reply failed: %s", e)

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("pong")
    except TelegramError as e:
        logger.warning("ping_cmd reply failed: %s", e)

# Global error handler for handlers
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error in handler: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Beklenmedik bir hata oluştu.")
    except Exception:
        logger.exception("Failed to notify user about handler error")

def main():
    try:
        defaults = Defaults(parse_mode=ParseMode.HTML)
        app = Application.builder().token(BOT_TOKEN).defaults(defaults).build()

        # Register minimal handlers
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("ping", ping_cmd))
        app.add_error_handler(global_error_handler)

        # Run polling (blocking). Use run_polling for simplicity and robustness.
        logger.info("Bot başlatılıyor: polling modunda çalışacak.")
        app.run_polling()
    except InvalidToken as e:
        log_startup_exception(e)
        logger.critical("Invalid TELEGRAM_BOT_TOKEN. Lütfen token'ı kontrol edin.")
        sys.exit(1)
    except NetworkError as e:
        log_startup_exception(e)
        logger.critical("Ağ hatası: %s", e)
        sys.exit(1)
    except Exception as e:
        log_startup_exception(e)
        logger.critical("Başlangıçta beklenmedik hata: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()



#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Geliştirilmiş hata ayıklama ile bot başlangıç dosyası.
- Hataları stdout ve bot_start_error.log dosyasına yazar.
- Render uyumlu health endpoint ve webhook/polling desteği içerir.
"""
import asyncio
import html
import logging
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Optional, Tuple

from aiohttp import web
from telegram import Update, ChatPermissions
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import Forbidden, InvalidToken, NetworkError, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, Defaults, MessageHandler, CallbackQueryHandler, filters

# -------------------------
# Basit logger (stdout + dosya)
# -------------------------
LOGFILE = "bot_start_error.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("RoseEnterprise")

def log_exception_and_exit(exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.critical("Başlangıç hatası:\n%s", tb)
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(f"\n\n=== {datetime.utcnow().isoformat()} UTC ===\n")
            f.write(tb)
    except Exception as e:
        logger.error("Log dosyasına yazılamadı: %s", e)
    # Exit with non-zero so Render gösterir
    sys.exit(1)

# -------------------------
# Tüm startup kodunu try/except içine alıyoruz
# -------------------------
try:
    # ============================================================
    # AYARLAR
    # ============================================================
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ortam değişkeni tanımlı değil.")

    USE_POLLING = bool(os.getenv("USE_POLLING"))
    PORT = int(os.getenv("PORT", "8443"))
    RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
    DB_PATH = os.getenv("DB_PATH", "rose_enterprise.db")

    # ============================================================
    # VERİTABANI (basit, güvenli açma)
    # ============================================================
    class Database:
        def __init__(self, path: str):
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            for attempt in range(3):
                try:
                    self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
                    break
                except sqlite3.OperationalError as e:
                    logger.warning("DB açılırken hata (deneme %d): %s", attempt + 1, e)
                    time.sleep(0.5 + attempt)
            else:
                raise RuntimeError("Veritabanı açılamadı")
            self.conn.row_factory = sqlite3.Row
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self.lock = threading.Lock()
            self._setup()

        def run(self, query: str, params: tuple = ()):
            with self.lock:
                cur = self.conn.execute(query, params)
                self.conn.commit()
                return cur

        def one(self, query: str, params: tuple = ()):
            with self.lock:
                return self.conn.execute(query, params).fetchone()

        def many(self, query: str, params: tuple = ()):
            with self.lock:
                return self.conn.execute(query, params).fetchall()

        def _setup(self):
            schema = [
                """CREATE TABLE IF NOT EXISTS federations (
                    fed_id TEXT PRIMARY KEY,
                    fed_name TEXT NOT NULL,
                    owner_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS fed_chats (
                    chat_id INTEGER PRIMARY KEY,
                    fed_id TEXT,
                    chat_name TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS fed_bans (
                    fed_id TEXT,
                    user_id INTEGER,
                    reason TEXT,
                    banned_by INTEGER,
                    date TEXT,
                    PRIMARY KEY (fed_id, user_id)
                )""",
                """CREATE TABLE IF NOT EXISTS warnings (
                    chat_id INTEGER,
                    user_id INTEGER,
                    warn_count INTEGER DEFAULT 0,
                    PRIMARY KEY (chat_id, user_id)
                )""",
                """CREATE TABLE IF NOT EXISTS settings (
                    chat_id INTEGER PRIMARY KEY,
                    welcome_text TEXT,
                    welcome_del INTEGER DEFAULT 0
                )""",
            ]
            for q in schema:
                self.run(q)
            logger.info("Veritabanı hazır.")

    db = Database(DB_PATH)

    # ============================================================
    # Yardımcılar (kısaltılmış, güvenli)
    # ============================================================
    _DUR = re.compile(r"(\d+)\s*(sn|dk|sa|hf|s|m|h|d|w|g)")
    _UNITS = {"s": 1, "sn": 1, "m": 60, "dk": 60, "h": 3600, "sa": 3600,
              "d": 86400, "g": 86400, "w": 604800, "hf": 604800}

    def parse_duration(text: str) -> Optional[int]:
        text = (text or "").strip().lower()
        pos, total = 0, 0
        for m in _DUR.finditer(text):
            if m.start() != pos:
                return None
            total += int(m.group(1)) * _UNITS[m.group(2)]
            pos = m.end()
        return total if text and pos == len(text) and total > 0 else None

    def mention(uid: int, name: str) -> str:
        return f'<a href="tg://user?id={uid}">{html.escape(name or str(uid))}</a>'

    def now_str() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    async def safe_reply(update: Update, text: str, **kw):
        try:
            if update and getattr(update, "effective_message", None):
                return await update.effective_message.reply_text(text, **kw)
        except TelegramError as e:
            logger.warning("reply failed: %s", e)

    def real_reply(msg):
        r = getattr(msg, "reply_to_message", None)
        if r is None or getattr(r, "forum_topic_created", False):
            return None
        return r

    async def is_admin(chat, user_id: int) -> bool:
        try:
            m = await chat.get_member(user_id)
            return m.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
        except TelegramError:
            return False

    def admin_only(bot_admin: bool = False):
        def deco(func):
            @wraps(func)
            async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
                msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
                if not msg or not chat:
                    return
                if chat.type == ChatType.PRIVATE:
                    return await safe_reply(update, "Bu komut sadece gruplarda çalışır.")
                anonymous = bool(getattr(msg, "sender_chat", None) and msg.sender_chat and msg.sender_chat.id == chat.id)
                if not anonymous and not (user and await is_admin(chat, user.id)):
                    return await safe_reply(update, "Bu komutu kullanmak için grup yöneticisi olmalısın.")
                if bot_admin:
                    try:
                        bm = await chat.get_member(context.bot.id)
                    except TelegramError:
                        return
                    if bm.status != ChatMemberStatus.ADMINISTRATOR:
                        return await safe_reply(update, "İşlem yapabilmem için beni grupta Yönetici yapmalısınız!")
                try:
                    return await func(update, context)
                except Exception as e:
                    logger.exception("Handler %s failed: %s", func.__name__, e)
                    await safe_reply(update, "Komut çalışırken beklenmedik bir hata oluştu.")
            return wrapper
        return deco

    # ============================================================
    # Basit komutlar (test için)
    # ============================================================
    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await safe_reply(update, "Bot çalışıyor ✅")

    # ============================================================
    # Application oluşturma ve handler kaydı
    # ============================================================
    defaults = Defaults(parse_mode=ParseMode.HTML)
    application = Application.builder().token(BOT_TOKEN).defaults(defaults).build()
    application.add_handler(CommandHandler("start", cmd_start))

    # Global error handler
    async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.exception("Unhandled error: %s", context.error)
        try:
            if isinstance(update, Update) and update.effective_message:
                await safe_reply(update, "Botta beklenmedik bir hata oluştu.")
        except Exception:
            logger.exception("Failed to notify user about error")

    application.add_error_handler(global_error_handler)

    # ============================================================
    # HTTP server (health + webhook)
    # ============================================================
    async def start_http_server(app_ref: Application):
        server_app = web.Application()

        async def health(request):
            return web.Response(text="ok")

        async def webhook_handler(request):
            try:
                data = await request.json()
                update = Update.de_json(data, app_ref.bot)
                await app_ref.update_queue.put(update)
                return web.Response(text="ok")
            except Exception as e:
                logger.exception("Webhook handler error: %s", e)
                return web.Response(status=500, text="error")

        server_app.add_routes([web.get("/", health)])
        server_app.add_routes([web.post(f"/webhook/{BOT_TOKEN}", webhook_handler)])
        runner = web.AppRunner(server_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info("HTTP server started on port %s", PORT)
        return runner

    # ============================================================
    # Run loop (robust)
    # ============================================================
    async def run():
        max_retries = 6
        delay = 1.0
        runner = None
        for attempt in range(max_retries):
            try:
                if not USE_POLLING and RENDER_EXTERNAL_URL:
                    webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook/{BOT_TOKEN}"
                    logger.info("Setting webhook: %s", webhook_url)
                    await application.bot.set_webhook(webhook_url)
                    runner = await start_http_server(application)
                    await application.initialize()
                    await application.start()
                    logger.info("Application started (webhook mode)")
                    await application.updater.wait_until_finished()
                    break
                else:
                    logger.info("Starting polling (attempt %d)", attempt + 1)
                    await application.initialize()
                    await application.start()
                    await application.updater.start_polling()
                    logger.info("Polling started")
                    await application.updater.wait_until_finished()
                    break
            except InvalidToken:
                raise RuntimeError("Invalid TELEGRAM_BOT_TOKEN. Lütfen token'ı kontrol edin.")
            except (NetworkError, OSError) as e:
                logger.warning("Ağ hatası: %s. Yeniden dene %s saniye sonra.", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            except Exception as e:
                logger.exception("Beklenmedik hata: %s", e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
        else:
            logger.critical("Bot başlatılamadı: maksimum yeniden deneme aşıldı.")
        try:
            if runner:
                await runner.cleanup()
        except Exception:
            pass

    def _shutdown():
        logger.info("Shutdown signal received, stopping...")
        try:
            asyncio.get_event_loop().create_task(application.stop())
        except Exception:
            pass

    def main():
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                pass
        try:
            loop.run_until_complete(run())
        finally:
            loop.run_until_complete(application.shutdown())
            loop.run_until_complete(application.stop())
            logger.info("Bot kapatıldı.")

    if __name__ == "__main__":
        main()

except Exception as exc:
    # Eğer startup sırasında herhangi bir hata olursa, ayrıntılı log yaz ve çık
    log_exception_and_exit(exc)



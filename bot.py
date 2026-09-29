#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rose Enterprise - Moderasyon, Federasyon, Geçiş Portalı ve Zamanlanmış İçerik Botu
Gereksinim: python-telegram-bot[job-queue]==21.6 , aiohttp
Ortam değişkenleri:
  TELEGRAM_BOT_TOKEN  (zorunlu)
  PORT                (Render otomatik verir)
  DB_PATH             (opsiyonel, örn: /var/data/rose.db -> Render Disk kullanırsan veri kalıcı olur)
  RENDER_EXTERNAL_URL (Render otomatik verir; webhook modu + uyku önleme için kullanılır)
  USE_POLLING=1       (opsiyonel, webhook yerine polling zorlar)
"""
import asyncio
import hashlib
import html
import logging
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

import aiohttp
from aiohttp import web
from telegram import (
    BotCommand,
    ChatPermissions,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, InvalidToken, NetworkError, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

# ============================================================
# 1. LOGLAMA
# ============================================================
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
for noisy in ("httpx", "httpcore", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("RoseEnterprise")


# ============================================================
# 2. VERİTABANI (tek bağlantı + kilit, thread-safe)
# ============================================================
class Database:
    def __init__(self, path: str):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.Lock()
        self._setup()

    def run(self, query, params=()):
        with self.lock:
            cur = self.conn.execute(query, params)
            self.conn.commit()
            return cur

    def one(self, query, params=()):
        with self.lock:
            return self.conn.execute(query, params).fetchone()

    def many(self, query, params=()):
        with self.lock:
            return self.conn.execute(query, params).fetchall()

    def _setup(self):
        schema = [
            """CREATE TABLE IF NOT EXISTS federations (
                fed_id TEXT PRIMARY KEY, fed_name TEXT NOT NULL,
                owner_id INTEGER NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS fed_chats (
                chat_id INTEGER PRIMARY KEY, fed_id TEXT, chat_name TEXT)""",
            """CREATE TABLE IF NOT EXISTS fed_bans (
                fed_id TEXT, user_id INTEGER, reason TEXT, banned_by INTEGER, date TEXT,
                PRIMARY KEY (fed_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS warnings (
                chat_id INTEGER, user_id INTEGER, warn_count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER PRIMARY KEY, welcome_text TEXT, welcome_del INTEGER DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS portals (
                chat_id INTEGER PRIMARY KEY, target TEXT, text TEXT, button TEXT,
                photo TEXT, interval INTEGER DEFAULT 0, last_msg_id INTEGER)""",
            """CREATE TABLE IF

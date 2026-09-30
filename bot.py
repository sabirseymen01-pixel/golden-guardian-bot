#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rose Enterprise - Stabil, Render ve Telegram uyumlu bot
Gereksinimleri:
  python-telegram-bot[job-queue]==21.6
  aiohttp
Ortam değişkenleri:
  TELEGRAM_BOT_TOKEN  (zorunlu)
  PORT                (Render otomatik verir)
  DB_PATH             (opsiyonel)
  RENDER_EXTERNAL_URL (opsiyonel, webhook modu için)
  USE_POLLING=1       (opsiyonel, polling zorlamak için)
Notlar:
  - Webhook kullanacaksanız RENDER_EXTERNAL_URL ayarlı olmalı.
  - Polling kullanacaksanız Render'da Background Worker seçin.
"""
import asyncio
import html
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Optional, Tuple

from aiohttp import web
from telegram import (
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    Update,
    ChatPermissions,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, TelegramError
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
# LOGGING
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
# AYARLAR
# ============================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN ortam değişkeni bulunamadı. Çıkılıyor.")
    sys.exit(1)

USE_POLLING = bool(os.getenv("USE_POLLING"))
PORT = int(os.getenv("PORT", "8443"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
DB_PATH = os.getenv("DB_PATH", "rose_enterprise.db")

# ============================================================
# VERİTABANI (thread-safe SQLite wrapper)
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
            """CREATE TABLE IF NOT EXISTS portals (
                chat_id INTEGER PRIMARY KEY,
                target TEXT,
                text TEXT,
                button TEXT,
                photo TEXT,
                interval INTEGER DEFAULT 0,
                last_msg_id INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                file_id TEXT,
                content TEXT,
                interval INTEGER NOT NULL,
                delete_after INTEGER DEFAULT 0,
                pin INTEGER DEFAULT 0,
                del_prev INTEGER DEFAULT 0,
                last_msg_id INTEGER,
                last_run INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )""",
        ]
        for q in schema:
            self.run(q)
        logger.info("✅ Veritabanı hazır.")

db = Database(DB_PATH)

# ============================================================
# YARDIMCILAR
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

def fmt_dur(sec: int) -> str:
    parts = []
    for name, n in (("gün", 86400), ("sa", 3600), ("dk", 60), ("sn", 1)):
        v, sec = divmod(int(sec), n)
        if v:
            parts.append(f"{v} {name}")
    return " ".join(parts) or "0 sn"

def mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{html.escape(name or str(uid))}</a>'

def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

async def



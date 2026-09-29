#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Geliştirilmiş Rose Enterprise - Hata dayanıklı sürüm
Gereksinimler:
  python-telegram-bot[job-queue]==21.6
  aiohttp
Ortam değişkenleri:
  TELEGRAM_BOT_TOKEN  (zorunlu)
  PORT                (Render otomatik verir)
  DB_PATH             (opsiyonel)
  RENDER_EXTERNAL_URL (opsiyonel, webhook modu için)
  USE_POLLING=1       (opsiyonel, polling zorlamak için)
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
from datetime import datetime, timedelta, timezone
from functools import wraps

import aiohttp
from aiohttp import web
from telegram import (
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    Update,
    ChatPermissions,
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

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("RoseEnterprise")

# ---------------------------
# Database (thread-safe wrapper)
# ---------------------------
class Database:
    def __init__(self, path: str):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        # retry loop for transient file locks
        for attempt in range(3):
            try:
                self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
                break
            except sqlite3.OperationalError as e:
                logger.warning("DB open failed (attempt %d): %s", attempt + 1, e)
                time.sleep(0.5 + attempt)
        else:
            raise
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
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
            """CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL, file_id TEXT, content TEXT,
                interval INTEGER NOT NULL, delete_after INTEGER DEFAULT 0,
                pin INTEGER DEFAULT 0, del_prev INTEGER DEFAULT 0,
                last_msg_id INTEGER, last_run INTEGER DEFAULT 0, active INTEGER DEFAULT 1)""",
        ]
        for q in schema:
            self.run(q)
        logger.info("✅ Veritabanı hazır.")

DB_PATH = os.getenv("DB_PATH", "rose_enterprise.db")
db = Database(DB_PATH)

# ---------------------------
# Helpers
# ---------------------------
_DUR = re.compile(r"(\d+)\s*(sn|dk|sa|hf|s|m|h|d|w|g)")
_UNITS = {"s": 1, "sn": 1, "m": 60, "dk": 60, "h": 3600, "sa": 3600,
          "d": 86400, "g": 86400, "w": 604800, "hf": 604800}

def parse_duration(text: str):
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

async def safe_reply(update: Update, text: str, **kw):
    try:
        return await update.effective_message.reply_text(text, **kw)
    except TelegramError as e:
        logger.warning("reply failed: %s", e)

def rest_html(msg, n: int) -> str:
    parts = (msg.text_html or "").split(None, n)
    return parts[n] if len(parts) > n else ""

def real_reply(msg):
    r = msg.reply_to_message
    if r is None or getattr(r, "forum_topic_created", False):
        return None
    return r

async def is_admin(chat, user_id: int) -> bool:
    try:
        m = await chat.get_member(user_id)
        return m.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except TelegramError:
        return False

async def is_owner(chat, user_id: int) -> bool:
    try:
        return (await chat.get_member(user_id)).status == ChatMemberStatus.OWNER
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
                return await safe_reply(update, "❌ Bu komut sadece gruplarda çalışır.")
            anonymous = bool(msg.sender_chat and msg.sender_chat.id == chat.id)
            if not anonymous and not (user and await is_admin(chat, user.id)):
                return await safe_reply(update, "⛔ Bu komutu kullanmak için grup yöneticisi olmalısın.")
            if bot_admin:
                try:
                    bm = await chat.get_member(context.bot.id)
                except TelegramError:
                    return
                if bm.status != ChatMemberStatus.ADMINISTRATOR:
                    return await safe_reply(update, "❌ İşlem yapabilmem için beni grupta Yönetici yapmalısınız!")
            try:
                return await func(update, context)
            except Exception as e:
                logger.exception("Handler %s failed: %s", func.__name__, e)
                await safe_reply(update, "❌ Komut çalışırken beklenmedik bir hata oluştu.")
        return wrapper
    return deco

async def get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    args = list(context.args or [])
    rr = real_reply(msg)
    if rr and rr.from_user:
        u = rr.from_user
        return u.id, u.full_name, " ".join(args)
    if args:
        first, rest = args[0], " ".join(args[1:])
        if first.lstrip("-").isdigit():
            uid, name = int(first), first
            try:
                ch = await context.bot.get_chat(uid)
                name = ch.full_name or ch.title or name
            except TelegramError:
                pass
            return uid, name, rest
        if first.startswith("@"):
            try:
                ch = await context.bot.get_chat(first)
                return ch.id, ch.full_name or first, rest
            except TelegramError:
                return None
    return None

# ---------------------------
# Basic commands (examples)
# ---------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_url = (f"https://t.me/{context.bot.username}?startgroup=true"
               "&admin=delete_messages+restrict_members+invite_users+pin_messages")
    kb = Markup([
        [Btn("➕ Beni Grubuna Ekle", url=add_url)],
        [Btn("📚 Komutlar (Yardım)", callback_data="help:main")],
    ])
    await safe_reply(update,
                f"Merhaba {html.escape(user.first_name)}! 🌹\n\n"
                "Ben moderasyon, federasyon, geçiş portalı ve zamanlanmış içerik botuyum.\n"
                "Beni gruba ekleyip yönetici yapman yeterli.", reply_markup=kb, parse_mode=ParseMode.HTML)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "⚙️ Yardım: /start, /help, /id, /ban, /unban, /mute, /unmute, /uyar"
    await safe_reply(update, text)

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    await safe_reply(update, f"👤 Kullanıcı ID: <code>{user.id}</code>\n💬 Sohbet ID: <code>{chat.id}</code>", parse_mode=ParseMode.HTML)

# Moderation example with robust error handling
@admin_only(bot_admin=True)
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ Kişiyi yanıtlayın veya ID/@kullanıcı yazın.")
    uid, name, reason = t
    if uid == context.bot.id:
        return await safe_reply(update, "😅 Kendimi yasaklayamam.")
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, uid)
    except Forbidden:
        return await safe_reply(update, "❌ Bu kullanıcıyı yasaklamaya yetkim yok.")
    except TelegramError as e:
        logger.warning("ban failed: %s", e)
        return await safe_reply(update, f"❌ Yasaklanamadı: {html.escape(str(e))}")
    await safe_reply(update, f"🔨 {mention(uid, name)} gruptan yasaklandı." + (f"\n📝 Sebep: {html.escape(reason)}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rose Enterprise - Stabil, Render ve Telegram uyumlu bot (düzeltilmiş tam sürüm)
Özellikler:
 - Moderasyon (ban, unban, mute, unmute, warn, unwarn, purge)
 - Federasyon (fkur, fbagla, fayril, fban, funban, fbanlist, fbilgi)
 - Yeni üye kontrolü (fedban kontrolü + hoş geldin)
 - Thread-safe SQLite veritabanı
 - Polling (varsayılan) ve webhook (RENDER_EXTERNAL_URL ayarlıysa) desteği
 - Sağlam hata yakalama, exponential backoff, Render health endpoint (webhook modunda)
Kullanım:
 - Ortam değişkenleri: TELEGRAM_BOT_TOKEN (zorunlu), USE_POLLING (opsiyonel),
   RENDER_EXTERNAL_URL (opsiyonel, webhook modu için), DB_PATH (opsiyonel), PORT (Render sağlar)
 - Polling için Render'da Background Worker seçin veya USE_POLLING=1 ayarlayın.
 - Webhook için Web Service seçin ve RENDER_EXTERNAL_URL ayarlayın.
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

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
for noisy in ("httpx", "httpcore", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("RoseEnterprise")

# -------------------------
# Config
# -------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN ortam değişkeni bulunamadı. Çıkılıyor.")
    sys.exit(1)

USE_POLLING = bool(os.getenv("USE_POLLING"))
PORT = int(os.getenv("PORT", "8443"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
DB_PATH = os.getenv("DB_PATH", "rose_enterprise.db")

# -------------------------
# Database (thread-safe)
# -------------------------
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

# -------------------------
# Helpers
# -------------------------
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

async def safe_reply(update: Update, text: str, **kw):
    try:
        if update and getattr(update, "effective_message", None):
            return await update.effective_message.reply_text(text, **kw)
    except TelegramError as e:
        logger.warning("reply failed: %s", e)

def rest_html(msg, n: int) -> str:
    parts = (msg.text_html or "").split(None, n)
    return parts[n] if len(parts) > n else ""

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
            anonymous = bool(getattr(msg, "sender_chat", None) and msg.sender_chat and msg.sender_chat.id == chat.id)
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

async def get_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[Tuple[int, str, str]]:
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
                name = getattr(ch, "full_name", None) or getattr(ch, "title", None) or name
            except TelegramError:
                pass
            return uid, name, rest
        if first.startswith("@"):
            try:
                ch = await context.bot.get_chat(first)
                return ch.id, getattr(ch, "full_name", None) or first, rest
            except TelegramError:
                return None
    return None

def reset_job(jq, name: str):
    for j in jq.get_jobs_by_name(name):
        j.schedule_removal()

async def delete_msg_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, msg_id = context.job.data
    try:
        await context.bot.delete_message(chat_id, msg_id)
    except TelegramError:
        pass

# -------------------------
# Help menu
# -------------------------
HELP_MAIN = "⚙️ <b>Yardım Menüsü</b>\n\nBir kategori seçin:"
HELP_PAGES = {
    "mod": (
        "🛡 <b>Moderasyon</b>\n\n"
        "<code>/ban</code> [yanıt/ID] [sebep] - Yasaklar\n"
        "<code>/unban</code> [yanıt/ID] - Yasağı kaldırır\n"
        "<code>/mute</code> [yanıt/ID] [süre] - Susturur\n"
        "<code>/unmute</code> [yanıt/ID] - Susturmayı kaldırır\n"
        "<code>/uyar</code> [yanıt/ID] [sebep] - Uyarı verir\n"
        "<code>/uyarisil</code> [yanıt/ID] - Uyarıları sıfırlar\n"
        "<code>/sil</code> - Yanıtlanan mesajdan sonuna kadar siler\n"
        "<code>/sil 50</code> - Son 50 mesajı siler\n"
        "<code>/id</code> - Grup ve kullanıcı ID'si"
    ),
    "fed": (
        "🌐 <b>Federasyon</b>\n\n"
        "<code>/fkur &lt;id&gt; &lt;ad&gt;</code> - Federasyon kurar\n"
        "<code>/fbagla &lt;id&gt;</code> - Grubu bağlar (grup kurucusu)\n"
        "<code>/fayril</code> - Grubu ayırır (grup kurucusu)\n"
        "<code>/fban</code> [yanıt/ID] [sebep] - Ağdaki tüm gruplardan yasaklar\n"
        "<code>/funban</code> [yanıt/ID] - Ağ yasağını kaldırır\n"
        "<code>/fbanlist</code> - Yasaklı listesi\n"
        "<code>/fbilgi</code> - Federasyon bilgisi\n\n"
        "ℹ️ FedBan'lı biri gruba girerse otomatik yasaklanır."
    ),
    "portal": (
        "🚪 <b>Geçiş Portalı</b>\n\n"
        "<code>/portal kur &lt;hedef&gt;</code> - Hedef: grup ID'si veya t.me davet linki\n"
        "<code>/portal metin &lt;metin&gt;</code> - Portal metni\n"
        "<code>/portal buton &lt;yazı&gt;</code> - Buton yazısı\n"
        "<code>/portal gorsel</code> - Bir fotoğrafı yanıtlayarak görsel ekler\n"
        "<code>/portal sure 6sa</code> - Portal her 6 saatte yenilenir\n"
        "<code>/portal yenile</code> - Şimdi yeniler\n"
        "<code>/portal bilgi</code> - Ayarları gösterir\n"
        "<code>/portal sil</code> - Portalı kaldırır"
    ),
    "oto": (
        "⏱ <b>Zamanlanmış Metin / Görsel</b>\n\n"
        "Göndermek istediğiniz mesajı yanıtlayıp:\n"
        "<code>/zamanla 6sa</code>\n\n"
        "Seçenekler:\n"
        "• <code>sil=30dk</code>\n"
        "• <code>ilk=5dk</code>\n"
        "• <code>sabitle</code>\n"
        "• <code>eskisil</code>\n\n"
        "<code>/zamanlar</code> - Listele, durdur/başlat, şimdi gönder, sil"
    ),
    "hos": (
        "👋 <b>Hoş Geldin Mesajı</b>\n\n"
        "<code>/hosgeldin &lt;metin&gt;</code> - Ayarlar. <code>{isim}</code> ve <code>{grup}</code> kullanılabilir\n"
        "<code>/hosgeldinsure 1dk</code> - Mesaj bu süre sonra silinir\n"
        "<code>/hosgeldinkapat</code> - Kapatır"
    ),
}

def help_main_kb() -> Markup:
    return Markup([
        [Btn("🛡 Moderasyon", callback_data="help:mod"), Btn("🌐 Federasyon", callback_data="help:fed")],
        [Btn("🚪 Portal", callback_data="help:portal"), Btn("⏱ Otomasyon", callback_data="help:oto")],
        [Btn("👋 Hoş Geldin", callback_data="help:hos")],
        [Btn("❌ Kapat", callback_data="help:close")],
    ])

# -------------------------
# Commands
# -------------------------
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
                     "Beni gruba ekleyip yönetici yapman yeterli.",
                     reply_markup=kb, parse_mode=ParseMode.HTML)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, HELP_MAIN, reply_markup=help_main_kb(), parse_mode=ParseMode.HTML)

async def help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    page = q.data.split(":", 1)[1]
    if page == "close":
        with suppress(TelegramError):
            await q.message.delete()
        return
    if page == "main":
        text, kb = HELP_MAIN, help_main_kb()
    else:
        text = HELP_PAGES.get(page, HELP_MAIN)
        kb = Markup([[Btn("🔙 Geri", callback_data="help:main")]])
    try:
        await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except BadRequest:
        pass

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    await safe_reply(update, f"👤 Kullanıcı ID: <code>{user.id}</code>\n💬 Sohbet ID: <code>{chat.id}</code>", parse_mode=ParseMode.HTML)

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
    reason_text = f"\n📝 Sebep: {html.escape(reason)}" if reason else ""
    await safe_reply(update, f"🔨 {mention(uid, name)} gruptan yasaklandı." + reason_text, parse_mode=ParseMode.HTML)

@admin_only(bot_admin=True)
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, uid, only_if_banned=True)
    except TelegramError as e:
        return await safe_reply(update, f"❌ Yasak kaldırılamadı: {html.escape(str(e))}")
    await safe_reply(update, f"✅ {mention(uid, name)} kullanıcısının yasağı kaldırıldı.", parse_mode=ParseMode.HTML)

@admin_only(bot_admin=True)
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın. Örn: /mute 2sa")
    uid, name, rest = t
    dur = parse_duration(rest.split()[0]) if rest else None
    until = datetime.now(timezone.utc) + timedelta(seconds=dur) if dur else None
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, uid, ChatPermissions(can_send_messages=False), until_date=until)
    except TelegramError as e:
        return await safe_reply(update, f"❌ Susturulamadı: {html.escape(str(e))}")
    await safe_reply(update, f"🔇 {mention(uid, name)} susturuldu " + (f"({fmt_dur(dur)})." if dur else "(süresiz)."), parse_mode=ParseMode.HTML)

@admin_only(bot_admin=True)
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    try:
        full = await context.bot.get_chat(update.effective_chat.id)
        perms = full.permissions or ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
            can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True)
        await context.bot.restrict_chat_member(update.effective_chat.id, uid, perms)
    except TelegramError as e:
        return await safe_reply(update, f"❌ Susturma kaldırılamadı: {html.escape(str(e))}")
    await safe_reply(update, f"🔊 {mention(uid, name)} artık konuşabilir.", parse_mode=ParseMode.HTML)

@admin_only()
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ Kimi uyaracağımı belirtmelisin.")
    uid, name, reason = t
    chat_id = update.effective_chat.id
    row = db.one("SELECT warn_count FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, uid))
    count = (row["warn_count"] if row else 0) + 1
    if count >= 3:
        db.run("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, uid))
        try:
            await context.bot.ban_chat_member(chat_id, uid)
            await safe_reply(update, f"🚫 {mention(uid, name)} 3 uyarıya ulaştı ve yasaklandı.", parse_mode=ParseMode.HTML)
        except TelegramError:
            await safe_reply(update, "❌ 3 uyarıya ulaştı ama yasaklamaya yetkim yetmedi.")
        return
    db.run("INSERT INTO warnings(chat_id,user_id,warn_count) VALUES(?,?,?) "
           "ON CONFLICT(chat_id,user_id) DO UPDATE SET warn_count=excluded.warn_count", (chat_id, uid, count))
    await safe_reply(update, f"⚠️ <b>Uyarı [{count}/3]</b>\nKullanıcı: {mention(uid, name)}\n"
                             f"Sebep: {html.escape(reason) if reason else 'Belirtilmedi'}", parse_mode=ParseMode.HTML)

@admin_only()
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    db.run("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (update.effective_chat.id, uid))
    await safe_reply(update, f"✅ {mention(uid, name)} kullanıcısının uyarıları sıfırlandı.", parse_mode=ParseMode.HTML)

@admin_only(bot_admin=True)
async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    rr = real_reply(msg)
    if rr:
        start = rr.message_id
    elif context.args and context.args[0].isdigit():
        start = msg.message_id - min(int(context.args[0]), 200)
    else:
        return await safe_reply(update, "⚠️ Silinecek ilk mesajı yanıtlayın veya /sil 50 yazın.")
    ids = list(range(max(start, 1), msg.message_id + 1))[-1000:]
    for i in range(0, len(ids), 100):
        try:
            await context.bot.delete_messages(chat.id, ids[i:i + 100])
        except TelegramError as e:
            logger.warning("Toplu silme hatası: %s", e)
    note = await context.bot.send_message(chat.id, f"🧹 Temizlik tamamlandı ({len(ids) - 1} mesaj tarandı).")
    context.job_queue.run_once(delete_msg_job, 5, data=(chat.id, note.message_id))

# -------------------------
# Federation handlers already defined above (cmd_fed_create etc.)
# For brevity, reuse earlier implementations (they are included below)
# -------------------------
def fed_of_chat(chat_id: int):
    return db.one("SELECT f.fed_id, f.fed_name, f.owner_id FROM fed_chats c "
                  "JOIN federations f ON f.fed_id = c.fed_id WHERE c.chat_id=?", (chat_id,))

async def cmd_fed_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await safe_reply(update, "ℹ️ Kullanım: /fkur <id> <ad>")
    fed_id = context.args[0].lower()
    if not re.fullmatch(r"[a-z0-9_-]{3,32}", fed_id):
        return await safe_reply(update, "❌ ID sadece harf, rakam, - ve _ içerebilir (3-32 karakter).")
    if db.one("SELECT 1 FROM federations WHERE fed_id=?", (fed_id,)):
        return await safe_reply(update, "❌ Bu ID'ye sahip bir federasyon zaten var.")
    name = " ".join(context.args[1:])
    db.run("INSERT INTO federations(fed_id,fed_name,owner_id,created_at) VALUES(?,?,?,?)",
           (fed_id, name, update.effective_user.id, now_str()))
    await safe_reply(update, f"👑 <b>Federasyon kuruldu!</b>\n\n<b>Ad:</b> {html.escape(name)}\n"
                             f"<b>ID:</b> <code>{fed_id}</code>\n\nGrubunuzu bağlamak için: /fbagla {fed_id}", parse_mode=ParseMode.HTML)

@admin_only()
async def cmd_fed_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if not context.args:
        return await safe_reply(update, "⚠️ Kullanım: /fbagla <id>")
    if not await is_owner(chat, user.id):
        return await safe_reply(update, "⛔ Grubu federasyona bağlamak için grup kurucusu olmalısınız.")
    fed = db.one("SELECT fed_name FROM federations WHERE fed_id=?", (context.args[0].lower(),))
    if not fed:
        return await safe_reply(update, "❌ Böyle bir federasyon bulunamadı.")
    db.run("INSERT INTO fed_chats(chat_id,fed_id,chat_name) VALUES(?,?,?) "
           "ON CONFLICT(chat_id) DO UPDATE SET fed_id=excluded.fed_id, chat_name=excluded.chat_name",
           (chat.id, context.args[0].lower(), chat.title))
    await safe_reply(update, f"🔗 Grup <b>{html.escape(fed['fed_name'])}</b> federasyonuna bağlandı!", parse_mode=ParseMode.HTML)

@admin_only()
async def cmd_fed_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_owner(chat, update.effective_user.id):
        return await safe_reply(update, "⛔ Sadece grup kurucusu ayırabilir.")
    if not fed_of_chat(chat.id):
        return await safe_reply(update, "❌ Bu grup zaten bir federasyona bağlı değil.")
    db.run("DELETE FROM fed_chats WHERE chat_id=?", (chat.id,))
    await safe_reply(update, "🔌 Grup federasyondan ayrıldı.")

async def cmd_fed_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        fed = db.one("SELECT * FROM federations WHERE fed_id=?", (context.args[0].lower(),))
    elif update.effective_chat.type != ChatType.PRIVATE:
        f = fed_of_chat(update.effective_chat.id)
        fed = db.one("SELECT * FROM federations WHERE fed_id=?", (f["fed_id"],)) if f else None
    else:
        fed = None
    if not fed:
        return await safe_reply(update, "❌ Federasyon bulunamadı. Gruba bağlı değil ya da ID hatalı.")
    chats = db.one("SELECT COUNT(*) c FROM fed_chats WHERE fed_id=?", (fed["fed_id"],))["c"]
    bans = db.one("SELECT COUNT(*) c FROM fed_bans WHERE fed_id=?", (fed["fed_id"],))["c"]
    await safe_reply(update, f"🌐 <b>{html.escape(fed['fed_name'])}</b>\n\nID: <code>{fed['fed_id']}</code>\n"
                             f"Sahip: <code>{fed['owner_id']}</code>\nBağlı grup: {chats}\nYasaklı kullanıcı: {bans}\n"
                             f"Kuruluş: {fed['created_at']} UTC", parse_mode=ParseMode.HTML)

@admin_only()
async def cmd_fban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    fed = fed_of_chat(chat.id)
    if not fed:
        return await safe_reply(update, "❌ Bu grup bir federasyona bağlı değil.")
    if fed["owner_id"] != user.id:
        return await safe_reply(update, "⛔ FedBan atmak için federasyon sahibi olmalısınız.")
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ FedBan atılacak kişiyi yanıtlayın veya ID yazın.")
    uid, name, reason = t
    if uid in (fed["owner_id"], context.bot.id):
        return await safe_reply(update, "❌ Bu kullanıcıya FedBan atılamaz.")
    reason = reason or "Federasyon kuralları ihlali."
    db.run("INSERT INTO fed_bans(fed_id,user_id,reason,banned_by,date) VALUES(?,?,?,?,?) "
           "ON CONFLICT(fed_id,user_id) DO UPDATE SET reason=excluded.reason, banned_by=excluded.banned_by, date=excluded.date",
           (fed["fed_id"], uid, reason, user.id, now_str()))
    chats = db.many("SELECT chat_id FROM fed_chats WHERE fed_id=?", (fed["fed_id"],))
    status = await safe_reply(update, "🔄 Global yasak ağa iletiliyor...")
    ok = 0
    for row in chats:
        try:
            await context.bot.ban_chat_member(row["chat_id"], uid)
            ok += 1
        except TelegramError:
            pass
        await asyncio.sleep(0.05)
    await status.edit_text(f"🛡 <b>KÜRESEL YASAK (FEDBAN)</b>\n\n👤 {mention(uid, name)} (<code>{uid}</code>)\n"
                           f"🌐 Federasyon: <code>{fed['fed_id']}</code>\n✅ Etkilenen grup: {ok}/{len(chats)}\n"
                           f"📝 Sebep: {html.escape(reason)}", parse_mode=ParseMode.HTML)

@admin_only()
async def cmd_funban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    fed = fed_of_chat(chat.id)
    if not fed or fed["owner_id"] != user.id:
        return await safe_reply(update, "⛔ Bu işlem için federasyon sahibi olmalısınız.")
    t = await get_target(update, context)
    if not t:
        return await safe_reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    db.run("DELETE FROM fed_bans WHERE fed_id=? AND user_id=?", (fed["fed_id"], uid))
    for row in db.many("SELECT chat_id FROM fed_chats WHERE fed_id=?", (fed["fed_id"],)):
        try:
            await context.bot.unban_chat_member(row["chat_id"], uid, only_if_banned=True)
        except TelegramError:
            pass
        await asyncio.sleep(0.05)
    await safe_reply(update, f"✅ {mention(uid, name)} FedBan listesinden çıkarıldı.", parse_mode=ParseMode.HTML)

@admin_only()
async def cmd_fbanlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fed = fed_of_chat(update.effective_chat.id)
    if not fed:
        return await safe_reply(update, "❌ Bu grup bir federasyona bağlı değil.")
    rows = db.many("SELECT user_id, reason FROM fed_bans WHERE fed_id=? ORDER BY date DESC LIMIT 25", (fed["fed_id"],))
    if not rows:
        return await safe_reply(update, "📭 FedBan listesi boş.")
    lines = [f"🚫 <code>{r['user_id']}</code> - {html.escape(r['reason'] or '-')}" for r in rows]
    await safe_reply(update, f"📋 <b>{html.escape(fed['fed_name'])}</b> - son {len(rows)} yasak:\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)

# -------------------------
# New member handler
# -------------------------
async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    fed = fed_of_chat(chat.id)
    settings = db.one("SELECT welcome_text, welcome_del FROM settings WHERE chat_id=?", (chat.id,))
    for user in msg.new_chat_members:
        if user.id == context.bot.id:
            try:
                await context.bot.send_message(chat.id, "🌹 Beni eklediğiniz için teşekkürler! Beni yönetici yapın ve /help yazın.")
            except TelegramError:
                pass
            continue
        if fed:
            ban = db.one("SELECT reason FROM fed_bans WHERE fed_id=? AND user_id=?", (fed["fed_id"], user.id))
            if ban:
                try:
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await context.bot.send_message(
                        chat.id, f"🛡 {mention(user.id, user.full_name)} federasyonda yasaklı olduğu için engellendi.\n"
                                 f"📝 {html.escape(ban['reason'] or '-')}", parse_mode=ParseMode.HTML)
                except TelegramError:
                    logger.warning("Could not ban fedbanned user %s in chat %s", user.id, chat.id)
                continue
        if settings and settings["welcome_text"]:
            text = settings["welcome_text"].replace("{isim}", html.escape(user.full_name)).replace("{grup}", html.escape(chat.title or ""))
            try:
                sent = await context.bot.send_message(chat.id, text, parse_mode=ParseMode.HTML)
                if settings["welcome_del"]:
                    context.job_queue.run_once(delete_msg_job, settings["welcome_del"], data=(chat.id, sent.message_id))
            except TelegramError:
                logger.warning("Welcome message failed in chat %s", chat.id)

# -------------------------
# Application bootstrap
# -------------------------
defaults = Defaults(parse_mode=ParseMode.HTML)
application = Application.builder().token(BOT_TOKEN).defaults(defaults).build()

# Register handlers
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("help", cmd_help))
application.add_handler(CallbackQueryHandler(help_cb, pattern=r"^help:"))
application.add_handler(CommandHandler("id", cmd_id))
application.add_handler(CommandHandler("ban", cmd_ban))
application.add_handler(CommandHandler("unban", cmd_unban))
application.add_handler(CommandHandler("mute", cmd_mute))
application.add_handler(CommandHandler("unmute", cmd_unmute))
application.add_handler(CommandHandler("uyar", cmd_warn))
application.add_handler(CommandHandler("uyarisil", cmd_unwarn))
application.add_handler(CommandHandler("sil", cmd_purge))
application.add_handler(CommandHandler("fkur", cmd_fed_create))
application.add_handler(CommandHandler("fbagla", cmd_fed_join))
application.add_handler(CommandHandler("fayril", cmd_fed_leave))
application.add_handler(CommandHandler("fbilgi", cmd_fed_info))
application.add_handler(CommandHandler("fban", cmd_fban))
application.add_handler(CommandHandler("funban", cmd_funban))
application.add_handler(CommandHandler("fbanlist", cmd_fbanlist))
application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))

# Global error handler
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await safe_reply(update, "❌ Botta beklenmedik bir hata oluştu.")
    except Exception:
        logger.exception("Failed to notify user about error")

application.add_error_handler(global_error_handler)

# -------------------------
# HTTP server for webhook & health
# -------------------------
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

# -------------------------
# Run loop with backoff
# -------------------------
async def run():
    max_retries = 10
    delay = 1.0
    runner = None
    for attempt in range(max_retries):
        try:
            if not USE_POLLING and RENDER_EXTERNAL_URL:
                webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook/{BOT_TOKEN}"
                logger.info("Webhook kuruluyor: %s", webhook_url)
                await application.bot.set_webhook(webhook_url)
                runner = await start_http_server(application)
                await application.initialize()
                await application.start()
                logger.info("Application started in webhook mode")
                await application.updater.wait_until_finished()
                break
            else:
                logger.info("Polling başlatılıyor (attempt %d)", attempt + 1)
                await application.initialize()
                await application.start()
                await application.updater.start_polling()
                logger.info("Polling started")
                await application.updater.wait_until_finished()
                break
        except InvalidToken:
            logger.critical("Invalid bot token. Lütfen TELEGRAM_BOT_TOKEN'ı kontrol edin.")
            raise
        except (NetworkError, OSError) as e:
            logger.warning("Ağ hatası: %s. Yeniden dene %s saniye sonra.", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        except Exception as e:
            logger.exception("Bot çalışırken beklenmedik hata: %s", e)
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



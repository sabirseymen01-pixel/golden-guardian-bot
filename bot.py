#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rose Enterprise - Moderasyon, Federasyon, Geçiş Portalı ve Zamanlanmış İçerik Botu
Gereksinim: python-telegram-bot[job-queue]==21.6 , aiohttp
Ortam değişkenleri:
  TELEGRAM_BOT_TOKEN  (zorunlu)
  PORT                (Render otomatik verir)
  DB_PATH             (opsiyonel, örn: /var/data/rose.db -> Render Disk kullanırsan veri kalıcı olur)
  RENDER_EXTERNAL_URL (Render otomatik verir, uyku modunu önlemek için self-ping'de kullanılır)
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
    ChatPermissions,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, NetworkError, TelegramError
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


db = Database(os.getenv("DB_PATH", "rose_enterprise.db"))


# ============================================================
# 3. YARDIMCILAR
# ============================================================
_DUR = re.compile(r"(\d+)\s*(sn|dk|sa|hf|s|m|h|d|w|g)")
_UNITS = {"s": 1, "sn": 1, "m": 60, "dk": 60, "h": 3600, "sa": 3600,
          "d": 86400, "g": 86400, "w": 604800, "hf": 604800}


def parse_duration(text: str):
    """'1sa30dk', '2h', '45sn', '1g' -> saniye. Geçersizse None."""
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


async def reply(update: Update, text: str, **kw):
    return await update.effective_message.reply_text(text, **kw)


def rest_html(msg, n: int) -> str:
    """Komuttan sonraki n. parçadan itibaren (biçimlendirmeyi koruyarak) metni döndürür."""
    parts = (msg.text_html or "").split(None, n)
    return parts[n] if len(parts) > n else ""


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
    """Sadece gruplarda, sadece yöneticiler (ve gerekirse bot yönetici ise) çalışır."""
    def deco(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
            if not msg or not chat:
                return
            if chat.type == ChatType.PRIVATE:
                return await reply(update, "❌ Bu komut sadece gruplarda çalışır.")
            anonymous = bool(msg.sender_chat and msg.sender_chat.id == chat.id)
            if not anonymous and not (user and await is_admin(chat, user.id)):
                return await reply(update, "⛔ Bu komutu kullanmak için grup yöneticisi olmalısın.")
            if bot_admin:
                try:
                    bm = await chat.get_member(context.bot.id)
                except TelegramError:
                    return
                if bm.status != ChatMemberStatus.ADMINISTRATOR:
                    return await reply(update, "❌ İşlem yapabilmem için beni grupta <b>Yönetici</b> yapmalısınız!")
            return await func(update, context)
        return wrapper
    return deco


async def get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(user_id, isim, kalan_metin) döndürür. Yanıttan, ID'den veya @kullanıcıdan."""
    msg = update.effective_message
    args = list(context.args or [])
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
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


def reset_job(jq, name: str):
    for j in jq.get_jobs_by_name(name):
        j.schedule_removal()


async def delete_msg_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, msg_id = context.job.data
    try:
        await context.bot.delete_message(chat_id, msg_id)
    except TelegramError:
        pass


# ============================================================
# 4. YARDIM MENÜSÜ
# ============================================================
HELP_MAIN = "⚙️ <b>Yardım Menüsü</b>\n\nBir kategori seçin:"
HELP_PAGES = {
    "mod": (
        "🛡 <b>Moderasyon</b>\n\n"
        "<code>/ban</code> [yanıt/ID] [sebep] - Yasaklar\n"
        "<code>/unban</code> [yanıt/ID] - Yasağı kaldırır\n"
        "<code>/mute</code> [yanıt/ID] [süre] - Susturur (örn: <code>/mute 2sa</code>)\n"
        "<code>/unmute</code> [yanıt/ID] - Susturmayı kaldırır\n"
        "<code>/uyar</code> [yanıt/ID] [sebep] - Uyarı verir (3 uyarıda ban)\n"
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
        "🚪 <b>Geçiş Portalı</b> (sohbet ↔ arayış grubu)\n\n"
        "Tek butonlu, sabitlenen bir mesajla üyeler diğer gruba geçer.\n\n"
        "<code>/portal kur &lt;hedef&gt;</code> - Hedef: grup ID'si (<code>-100...</code>) veya t.me davet linki\n"
        "<code>/portal metin &lt;metin&gt;</code> - Portal metni (HTML biçimlendirme desteklenir)\n"
        "<code>/portal buton &lt;yazı&gt;</code> - Buton yazısı\n"
        "<code>/portal gorsel</code> - Bir fotoğrafı yanıtlayarak görsel ekler (<code>/portal gorsel kaldir</code>)\n"
        "<code>/portal sure 6sa</code> - Portal her 6 saatte yenilenip sabitlenir (<code>kapat</code> ile iptal)\n"
        "<code>/portal yenile</code> - Şimdi yeniler\n"
        "<code>/portal bilgi</code> - Ayarları gösterir\n"
        "<code>/portal sil</code> - Portalı kaldırır\n\n"
        "💡 Hedef grubun ID'si için orada <code>/id</code> yazın. ID kullanırsanız bot hedef grupta "
        "yönetici (davet yetkili) olmalı; link kullanırsanız gerekmez."
    ),
    "oto": (
        "⏱ <b>Zamanlanmış Metin / Görsel</b>\n\n"
        "Göndermek istediğiniz mesajı (metin, fotoğraf, video, GIF) <b>yanıtlayıp</b>:\n"
        "<code>/zamanla 6sa</code>\n\n"
        "Seçenekler (isteğe bağlı):\n"
        "• <code>sil=30dk</code> - Gönderilen mesaj 30 dk sonra silinir\n"
        "• <code>ilk=5dk</code> - İlk gönderim 5 dk sonra\n"
        "• <code>sabitle</code> - Mesaj sabitlenir\n"
        "• <code>eskisil</code> - Yeni göndermeden önce eskisini siler\n\n"
        "Örnek: <code>/zamanla 1g sil=6sa sabitle</code>\n"
        "Süre birimleri: <code>sn dk sa g hf</code> (en az 60 sn)\n\n"
        "<code>/zamanlar</code> - Listele, durdur/başlat, şimdi gönder, sil"
    ),
    "hos": (
        "👋 <b>Hoş Geldin Mesajı</b>\n\n"
        "<code>/hosgeldin &lt;metin&gt;</code> - Ayarlar. <code>{isim}</code> ve <code>{grup}</code> kullanılabilir\n"
        "<code>/hosgeldinsure 1dk</code> - Mesaj bu süre sonra silinir (<code>kapat</code> = silinmez)\n"
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_url = (f"https://t.me/{context.bot.username}?startgroup=true"
               "&admin=delete_messages+restrict_members+invite_users+pin_messages")
    kb = Markup([
        [Btn("➕ Beni Grubuna Ekle", url=add_url)],
        [Btn("📚 Komutlar (Yardım)", callback_data="help:main")],
    ])
    await reply(update,
                f"Merhaba {html.escape(user.first_name)}! 🌹\n\n"
                "Ben moderasyon, federasyon, geçiş portalı ve zamanlanmış içerik botuyum.\n"
                "Beni gruba ekleyip <b>yönetici</b> yapman yeterli.", reply_markup=kb)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, HELP_MAIN, reply_markup=help_main_kb())


async def help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    page = q.data.split(":", 1)[1]
    if page == "close":
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return
    if page == "main":
        text, kb = HELP_MAIN, help_main_kb()
    else:
        text = HELP_PAGES.get(page, HELP_MAIN)
        kb = Markup([[Btn("🔙 Geri", callback_data="help:main")]])
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        pass


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    await reply(update, f"👤 Kullanıcı ID: <code>{user.id}</code>\n💬 Sohbet ID: <code>{chat.id}</code>")


# ============================================================
# 5. MODERASYON
# ============================================================
@admin_only(bot_admin=True)
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ Kişiyi yanıtlayın veya ID/@kullanıcı yazın.")
    uid, name, reason = t
    if uid == context.bot.id:
        return await reply(update, "😅 Kendimi yasaklayamam.")
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, uid)
    except TelegramError as e:
        return await reply(update, f"❌ Yasaklanamadı: <code>{html.escape(e.message)}</code>")
    await reply(update, f"🔨 {mention(uid, name)} gruptan yasaklandı."
                + (f"\n📝 Sebep: {html.escape(reason)}" if reason else ""))


@admin_only(bot_admin=True)
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, uid, only_if_banned=True)
    except TelegramError as e:
        return await reply(update, f"❌ Yasak kaldırılamadı: <code>{html.escape(e.message)}</code>")
    await reply(update, f"✅ {mention(uid, name)} kullanıcısının yasağı kaldırıldı.")


@admin_only(bot_admin=True)
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın. Örn: <code>/mute 2sa</code>")
    uid, name, rest = t
    dur = parse_duration(rest.split()[0]) if rest else None
    until = datetime.now(timezone.utc) + timedelta(seconds=dur) if dur else None
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, uid, ChatPermissions(can_send_messages=False), until_date=until)
    except TelegramError as e:
        return await reply(update, f"❌ Susturulamadı: <code>{html.escape(e.message)}</code>")
    await reply(update, f"🔇 {mention(uid, name)} susturuldu " + (f"({fmt_dur(dur)})." if dur else "(süresiz)."))


@admin_only(bot_admin=True)
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, uid, ChatPermissions.all_permissions())
    except TelegramError as e:
        return await reply(update, f"❌ Susturma kaldırılamadı: <code>{html.escape(e.message)}</code>")
    await reply(update, f"🔊 {mention(uid, name)} artık konuşabilir.")


@admin_only(bot_admin=True)
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ Kimi uyaracağımı belirtmelisin.")
    uid, name, reason = t
    chat_id = update.effective_chat.id
    row = db.one("SELECT warn_count FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, uid))
    count = (row["warn_count"] if row else 0) + 1
    if count >= 3:
        db.run("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, uid))
        try:
            await context.bot.ban_chat_member(chat_id, uid)
            await reply(update, f"🚫 {mention(uid, name)} 3 uyarıya ulaştı ve yasaklandı.")
        except TelegramError:
            await reply(update, "❌ 3 uyarıya ulaştı ama yasaklamaya yetkim yetmedi.")
        return
    db.run("INSERT INTO warnings(chat_id,user_id,warn_count) VALUES(?,?,?) "
           "ON CONFLICT(chat_id,user_id) DO UPDATE SET warn_count=excluded.warn_count", (chat_id, uid, count))
    await reply(update, f"⚠️ <b>Uyarı [{count}/3]</b>\nKullanıcı: {mention(uid, name)}\n"
                        f"Sebep: {html.escape(reason) if reason else 'Belirtilmedi'}")


@admin_only()
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    db.run("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (update.effective_chat.id, uid))
    await reply(update, f"✅ {mention(uid, name)} kullanıcısının uyarıları sıfırlandı.")


@admin_only(bot_admin=True)
async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    if msg.reply_to_message:
        start = msg.reply_to_message.message_id
    elif context.args and context.args[0].isdigit():
        start = msg.message_id - min(int(context.args[0]), 200)
    else:
        return await reply(update, "⚠️ Silinecek ilk mesajı yanıtlayın veya <code>/sil 50</code> yazın.")
    ids = list(range(max(start, 1), msg.message_id + 1))[-1000:]
    for i in range(0, len(ids), 100):
        try:
            await context.bot.delete_messages(chat.id, ids[i:i + 100])
        except TelegramError as e:
            logger.warning("Toplu silme hatası: %s", e)
    note = await context.bot.send_message(chat.id, f"🧹 Temizlik tamamlandı ({len(ids) - 1} mesaj tarandı).")
    context.job_queue.run_once(delete_msg_job, 5, data=(chat.id, note.message_id))


# ============================================================
# 6. FEDERASYON
# ============================================================
def fed_of_chat(chat_id: int):
    return db.one("SELECT f.fed_id, f.fed_name, f.owner_id FROM fed_chats c "
                  "JOIN federations f ON f.fed_id = c.fed_id WHERE c.chat_id=?", (chat_id,))


async def cmd_fed_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await reply(update, "ℹ️ Kullanım: <code>/fkur &lt;id&gt; &lt;ad&gt;</code>\nID: 3-32 karakter, harf/rakam/-/_")
    fed_id = context.args[0].lower()
    if not re.fullmatch(r"[a-z0-9_-]{3,32}", fed_id):
        return await reply(update, "❌ ID sadece harf, rakam, - ve _ içerebilir (3-32 karakter).")
    if db.one("SELECT 1 FROM federations WHERE fed_id=?", (fed_id,)):
        return await reply(update, "❌ Bu ID'ye sahip bir federasyon zaten var.")
    name = " ".join(context.args[1:])
    db.run("INSERT INTO federations(fed_id,fed_name,owner_id,created_at) VALUES(?,?,?,?)",
           (fed_id, name, update.effective_user.id, now_str()))
    await reply(update, f"👑 <b>Federasyon kuruldu!</b>\n\n<b>Ad:</b> {html.escape(name)}\n"
                        f"<b>ID:</b> <code>{fed_id}</code>\n\nGrubunuzu bağlamak için: <code>/fbagla {fed_id}</code>")


@admin_only()
async def cmd_fed_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if not context.args:
        return await reply(update, "⚠️ Kullanım: <code>/fbagla &lt;id&gt;</code>")
    if not await is_owner(chat, user.id):
        return await reply(update, "⛔ Grubu federasyona bağlamak için grup <b>kurucusu</b> olmalısınız.")
    fed = db.one("SELECT fed_name FROM federations WHERE fed_id=?", (context.args[0].lower(),))
    if not fed:
        return await reply(update, "❌ Böyle bir federasyon bulunamadı.")
    db.run("INSERT INTO fed_chats(chat_id,fed_id,chat_name) VALUES(?,?,?) "
           "ON CONFLICT(chat_id) DO UPDATE SET fed_id=excluded.fed_id, chat_name=excluded.chat_name",
           (chat.id, context.args[0].lower(), chat.title))
    await reply(update, f"🔗 Grup <b>{html.escape(fed['fed_name'])}</b> federasyonuna bağlandı!")


@admin_only()
async def cmd_fed_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_owner(chat, update.effective_user.id):
        return await reply(update, "⛔ Sadece grup kurucusu ayırabilir.")
    if not fed_of_chat(chat.id):
        return await reply(update, "❌ Bu grup zaten bir federasyona bağlı değil.")
    db.run("DELETE FROM fed_chats WHERE chat_id=?", (chat.id,))
    await reply(update, "🔌 Grup federasyondan ayrıldı.")


async def cmd_fed_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        fed = db.one("SELECT * FROM federations WHERE fed_id=?", (context.args[0].lower(),))
    elif update.effective_chat.type != ChatType.PRIVATE:
        f = fed_of_chat(update.effective_chat.id)
        fed = db.one("SELECT * FROM federations WHERE fed_id=?", (f["fed_id"],)) if f else None
    else:
        fed = None
    if not fed:
        return await reply(update, "❌ Federasyon bulunamadı. Gruba bağlı değil ya da ID hatalı.")
    chats = db.one("SELECT COUNT(*) c FROM fed_chats WHERE fed_id=?", (fed["fed_id"],))["c"]
    bans = db.one("SELECT COUNT(*) c FROM fed_bans WHERE fed_id=?", (fed["fed_id"],))["c"]
    await reply(update, f"🌐 <b>{html.escape(fed['fed_name'])}</b>\n\nID: <code>{fed['fed_id']}</code>\n"
                        f"Sahip: <code>{fed['owner_id']}</code>\nBağlı grup: {chats}\nYasaklı kullanıcı: {bans}\n"
                        f"Kuruluş: {fed['created_at']} UTC")


@admin_only()
async def cmd_fban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    fed = fed_of_chat(chat.id)
    if not fed:
        return await reply(update, "❌ Bu grup bir federasyona bağlı değil.")
    if fed["owner_id"] != user.id:
        return await reply(update, "⛔ FedBan atmak için federasyon sahibi olmalısınız.")
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ FedBan atılacak kişiyi yanıtlayın veya ID yazın.")
    uid, name, reason = t
    if uid in (fed["owner_id"], context.bot.id):
        return await reply(update, "❌ Bu kullanıcıya FedBan atılamaz.")
    reason = reason or "Federasyon kuralları ihlali."
    db.run("INSERT INTO fed_bans(fed_id,user_id,reason,banned_by,date) VALUES(?,?,?,?,?) "
           "ON CONFLICT(fed_id,user_id) DO UPDATE SET reason=excluded.reason, banned_by=excluded.banned_by, date=excluded.date",
           (fed["fed_id"], uid, reason, user.id, now_str()))
    chats = db.many("SELECT chat_id FROM fed_chats WHERE fed_id=?", (fed["fed_id"],))
    status = await reply(update, "🔄 Global yasak ağa iletiliyor...")
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
                           f"📝 Sebep: {html.escape(reason)}")


@admin_only()
async def cmd_funban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    fed = fed_of_chat(chat.id)
    if not fed or fed["owner_id"] != user.id:
        return await reply(update, "⛔ Bu işlem için federasyon sahibi olmalısınız.")
    t = await get_target(update, context)
    if not t:
        return await reply(update, "⚠️ Kişiyi yanıtlayın veya ID yazın.")
    uid, name, _ = t
    db.run("DELETE FROM fed_bans WHERE fed_id=? AND user_id=?", (fed["fed_id"], uid))
    for row in db.many("SELECT chat_id FROM fed_chats WHERE fed_id=?", (fed["fed_id"],)):
        try:
            await context.bot.unban_chat_member(row["chat_id"], uid, only_if_banned=True)
        except TelegramError:
            pass
        await asyncio.sleep(0.05)
    await reply(update, f"✅ {mention(uid, name)} FedBan listesinden çıkarıldı.")


@admin_only()
async def cmd_fbanlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fed = fed_of_chat(update.effective_chat.id)
    if not fed:
        return await reply(update, "❌ Bu grup bir federasyona bağlı değil.")
    rows = db.many("SELECT user_id, reason FROM fed_bans WHERE fed_id=? ORDER BY date DESC LIMIT 25", (fed["fed_id"],))
    if not rows:
        return await reply(update, "📭 FedBan listesi boş.")
    lines = [f"🚫 <code>{r['user_id']}</code> - {html.escape(r['reason'] or '-')}" for r in rows]
    await reply(update, f"📋 <b>{html.escape(fed['fed_name'])}</b> - son {len(rows)} yasak:\n\n" + "\n".join(lines))


# ============================================================
# 7. YENİ ÜYE (FedBan kontrolü + Hoş geldin)
# ============================================================
async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    fed = fed_of_chat(chat.id)
    settings = db.one("SELECT welcome_text, welcome_del FROM settings WHERE chat_id=?", (chat.id,))
    for user in msg.new_chat_members:
        if user.id == context.bot.id:
            await context.bot.send_message(chat.id, "🌹 Beni eklediğiniz için teşekkürler! Beni <b>yönetici</b> yapın ve "
                                                    "<code>/yardim</code> yazarak komutlara bakın.")
            continue
        if fed:
            ban = db.one("SELECT reason FROM fed_bans WHERE fed_id=? AND user_id=?", (fed["fed_id"], user.id))
            if ban:
                try:
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await context.bot.send_message(
                        chat.id, f"🛡 {mention(user.id, user.full_name)} federasyonda yasaklı olduğu için engellendi.\n"
                                 f"📝 {html.escape(ban['reason'] or '-')}")
                except TelegramError:
                    pass
                continue
        if settings and settings["welcome_text"] and not user.is_bot:
            text = (settings["welcome_text"].replace("{isim}", mention(user.id, user.full_name))
                    .replace("{grup}", html.escape(chat.title or "")))
            try:
                sent = await context.bot.send_message(chat.id, text)
                if settings["welcome_del"]:
                    context.job_queue.run_once(delete_msg_job, settings["welcome_del"], data=(chat.id, sent.message_id))
            except TelegramError:
                pass


@admin_only()
async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = rest_html(update.effective_message, 1)
    if not text:
        return await reply(update, "⚠️ Kullanım: <code>/hosgeldin Merhaba {isim}, {grup} grubuna hoş geldin!</code>")
    db.run("INSERT INTO settings(chat_id,welcome_text) VALUES(?,?) "
           "ON CONFLICT(chat_id) DO UPDATE SET welcome_text=excluded.welcome_text", (update.effective_chat.id, text))
    await reply(update, "✅ Hoş geldin mesajı ayarlandı.")


@admin_only()
async def cmd_welcome_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.run("UPDATE settings SET welcome_text=NULL WHERE chat_id=?", (update.effective_chat.id,))
    await reply(update, "✅ Hoş geldin mesajı kapatıldı.")


@admin_only()
async def cmd_welcome_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0] if context.args else "").lower()
    dur = 0 if arg == "kapat" else parse_duration(arg)
    if dur is None:
        return await reply(update, "⚠️ Kullanım: <code>/hosgeldinsure 1dk</code> veya <code>/hosgeldinsure kapat</code>")
    db.run("INSERT INTO settings(chat_id,welcome_del) VALUES(?,?) "
           "ON CONFLICT(chat_id) DO UPDATE SET welcome_del=excluded.welcome_del", (update.effective_chat.id, dur))
    await reply(update, f"✅ Hoş geldin mesajı {fmt_dur(dur)} sonra silinecek." if dur else "✅ Otomatik silme kapatıldı.")


# ============================================================
# 8. GEÇİŞ PORTALI
# ============================================================
PORTAL_DEFAULT = "🚪 <b>Geçiş Portalı</b>\n\nAşağıdaki butona dokunarak diğer gruba tek tıkla geçebilirsin."


async def send_portal(bot, chat_id: int):
    """Eski portalı siler, yenisini gönderir ve sabitler. (başarı, hata_metni) döner."""
    row = db.one("SELECT * FROM portals WHERE chat_id=?", (chat_id,))
    if not row or not row["target"]:
        return False, "Portal tanımlı değil."
    if row["last_msg_id"]:
        try:
            await bot.delete_message(chat_id, row["last_msg_id"])
        except TelegramError:
            pass
    kb = Markup([[Btn(row["button"] or "🚪 Gruba Geç", url=row["target"])]])
    text = row["text"] or PORTAL_DEFAULT
    try:
        if row["photo"]:
            m = await bot.send_photo(chat_id, row["photo"], caption=text, reply_markup=kb)
        else:
            m = await bot.send_message(chat_id, text, reply_markup=kb)
    except TelegramError as e:
        logger.warning("Portal gönderilemedi (%s): %s", chat_id, e)
        return False, e.message
    db.run("UPDATE portals SET last_msg_id=? WHERE chat_id=?", (m.message_id, chat_id))
    try:
        await bot.pin_chat_message(chat_id, m.message_id, disable_notification=True)
    except TelegramError:
        return True, "Portal gönderildi ama sabitlenemedi (botun 'Mesaj sabitle' yetkisi olmalı)."
    return True, None


async def portal_job(context: ContextTypes.DEFAULT_TYPE):
    await send_portal(context.bot, context.job.data)


def set_portal_job(jq, chat_id: int, interval: int):
    name = f"portal:{chat_id}"
    reset_job(jq, name)
    if interval and interval > 0:
        jq.run_repeating(portal_job, interval=interval, first=interval, data=chat_id, name=name)


@admin_only(bot_admin=True)
async def cmd_portal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    args = context.args or []
    if not args:
        return await reply(update, HELP_PAGES["portal"])
    sub = args[0].lower()
    row = db.one("SELECT * FROM portals WHERE chat_id=?", (chat.id,))

    if sub == "kur":
        if len(args) < 2:
            return await reply(update, "⚠️ Kullanım: <code>/portal kur &lt;grup_id veya t.me linki&gt;</code>")
        raw, link, title = args[1], None, None
        if raw.lstrip("-").isdigit():
            try:
                target = await context.bot.get_chat(int(raw))
                title = target.title
                link = (await context.bot.create_chat_invite_link(int(raw), name="Portal")).invite_link
            except TelegramError as e:
                return await reply(update, "❌ Hedef grupta davet linki oluşturamadım. Botun orada yönetici olması ve "
                                           "'Kullanıcı davet et' yetkisi bulunması gerekir.\n"
                                           f"<code>{html.escape(e.message)}</code>")
        elif re.match(r"^(https?://)?t\.me/", raw):
            link = raw if raw.startswith("http") else "https://" + raw
        else:
            return await reply(update, "❌ Hedef, grup ID'si (<code>-100...</code>) veya t.me linki olmalı.")
        button = f"🚪 {title} grubuna geç" if title else "🚪 Gruba Geç"
        db.run("INSERT INTO portals(chat_id,target,button) VALUES(?,?,?) "
               "ON CONFLICT(chat_id) DO UPDATE SET target=excluded.target, button=excluded.button",
               (chat.id, link, button))
        ok, err = await send_portal(context.bot, chat.id)
        return await reply(update, "✅ Portal kuruldu." + (f"\n⚠️ {html.escape(err)}" if err else "")
                           if ok else f"❌ Portal gönderilemedi: {html.escape(err or '')}")

    if not row:
        return await reply(update, "ℹ️ Önce portal kurun: <code>/portal kur &lt;hedef&gt;</code>")

    if sub == "metin":
        text = rest_html(msg, 2)
        if not text:
            return await reply(update, "⚠️ Kullanım: <code>/portal metin &lt;metin&gt;</code>")
        db.run("UPDATE portals SET text=? WHERE chat_id=?", (text, chat.id))
    elif sub == "buton":
        label = " ".join(args[1:])
        if not label:
            return await reply(update, "⚠️ Kullanım: <code>/portal buton &lt;yazı&gt;</code>")
        db.run("UPDATE portals SET button=? WHERE chat_id=?", (label[:60], chat.id))
    elif sub == "gorsel":
        if len(args) > 1 and args[1].lower() in ("kaldir", "kaldır", "sil"):
            db.run("UPDATE portals SET photo=NULL WHERE chat_id=?", (chat.id,))
        elif msg.reply_to_message and msg.reply_to_message.photo:
            db.run("UPDATE portals SET photo=? WHERE chat_id=?", (msg.reply_to_message.photo[-1].file_id, chat.id))
        else:
            return await reply(update, "⚠️ Bir <b>fotoğrafı yanıtlayarak</b> <code>/portal gorsel</code> yazın.")
    elif sub == "sure":
        arg = (args[1] if len(args) > 1 else "").lower()
        dur = 0 if arg in ("kapat", "0") else parse_duration(arg)
        if dur is None or (0 < dur < 60):
            return await reply(update, "⚠️ Örn: <code>/portal sure 6sa</code> (en az 60 sn) veya <code>/portal sure kapat</code>")
        db.run("UPDATE portals SET interval=? WHERE chat_id=?", (dur, chat.id))
        set_portal_job(context.job_queue, chat.id, dur)
        return await reply(update, f"✅ Portal her <b>{fmt_dur(dur)}</b> yenilenip sabitlenecek." if dur
                           else "✅ Otomatik yenileme kapatıldı.")
    elif sub == "yenile":
        pass
    elif sub == "bilgi":
        return await reply(update, "🚪 <b>Portal Ayarları</b>\n\n"
                                   f"Buton: {html.escape(row['button'] or '-')}\n"
                                   f"Görsel: {'var' if row['photo'] else 'yok'}\n"
                                   f"Yenileme: {fmt_dur(row['interval']) if row['interval'] else 'kapalı'}\n"
                                   f"Özel metin: {'var' if row['text'] else 'varsayılan'}")
    elif sub == "sil":
        set_portal_job(context.job_queue, chat.id, 0)
        if row["last_msg_id"]:
            try:
                await context.bot.unpin_chat_message(chat.id, row["last_msg_id"])
                await context.bot.delete_message(chat.id, row["last_msg_id"])
            except TelegramError:
                pass
        db.run("DELETE FROM portals WHERE chat_id=?", (chat.id,))
        return await reply(update, "🗑 Portal kaldırıldı.")
    else:
        return await reply(update, HELP_PAGES["portal"])

    ok, err = await send_portal(context.bot, chat.id)
    await reply(update, "✅ Portal güncellendi." + (f"\n⚠️ {html.escape(err)}" if err else "")
                if ok else f"❌ Güncellenemedi: {html.escape(err or '')}")


async def on_pinned_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botun kendi sabitleme bildirimini temizler."""
    msg = update.effective_message
    if msg.from_user and msg.from_user.id == context.bot.id:
        try:
            await msg.delete()
        except TelegramError:
            pass


# ============================================================
# 9. ZAMANLANMIŞ METİN / GÖRSEL OTOMASYONU
# ============================================================
KIND_TR = {"text": "📝 Metin", "photo": "🖼 Görsel", "video": "🎬 Video", "animation": "🎞 GIF"}


def add_sched_job(jq, sid: int, interval: int, first=None):
    name = f"sched:{sid}"
    reset_job(jq, name)
    jq.run_repeating(sched_job, interval=interval, first=first or interval, data=sid, name=name)


async def run_schedule(bot, jq, sid: int) -> bool:
    s = db.one("SELECT * FROM schedules WHERE id=?", (sid,))
    if not s or not s["active"]:
        return False
    cid = s["chat_id"]
    if s["del_prev"] and s["last_msg_id"]:
        try:
            await bot.delete_message(cid, s["last_msg_id"])
        except TelegramError:
            pass
    try:
        kind = s["kind"]
        if kind == "photo":
            m = await bot.send_photo(cid, s["file_id"], caption=s["content"] or None)
        elif kind == "video":
            m = await bot.send_video(cid, s["file_id"], caption=s["content"] or None)
        elif kind == "animation":
            m = await bot.send_animation(cid, s["file_id"], caption=s["content"] or None)
        else:
            m = await bot.send_message(cid, s["content"])
    except Forbidden:
        logger.warning("Zamanlama #%s: bot gruptan çıkarıldı, pasifleştirildi.", sid)
        db.run("UPDATE schedules SET active=0 WHERE id=?", (sid,))
        return False
    except TelegramError as e:
        logger.warning("Zamanlama #%s gönderilemedi: %s", sid, e)
        return False
    db.run("UPDATE schedules SET last_msg_id=?, last_run=? WHERE id=?", (m.message_id, int(time.time()), sid))
    if s["pin"]:
        try:
            await bot.pin_chat_message(cid, m.message_id, disable_notification=True)
        except TelegramError:
            pass
    if s["delete_after"]:
        jq.run_once(delete_msg_job, s["delete_after"], data=(cid, m.message_id))
    return True


async def sched_job(context: ContextTypes.DEFAULT_TYPE):
    sid = context.job.data
    s = db.one("SELECT active FROM schedules WHERE id=?", (sid,))
    if not s or not s["active"]:
        context.job.schedule_removal()
        return
    await run_schedule(context.bot, context.job_queue, sid)


def parse_opts(tokens):
    o = {"sil": 0, "ilk": None, "sabitle": 0, "eskisil": 0}
    for tok in tokens:
        t = tok.lower()
        if t == "sabitle":
            o["sabitle"] = 1
        elif t == "eskisil":
            o["eskisil"] = 1
        elif t.startswith("sil=") or t.startswith("ilk="):
            d = parse_duration(t[4:])
            if d is None:
                raise ValueError(tok)
            o[t[:3]] = d
        else:
            raise ValueError(tok)
    return o


@admin_only()
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    src = msg.reply_to_message
    if not src or not context.args:
        return await reply(update, HELP_PAGES["oto"])
    interval = parse_duration(context.args[0])
    if not interval or interval < 60:
        return await reply(update, "⚠️ Aralık en az 60 saniye olmalı. Örn: <code>6sa</code>, <code>1g</code>, <code>90dk</code>")
    try:
        o = parse_opts(context.args[1:])
    except ValueError as e:
        return await reply(update, f"⚠️ Geçersiz seçenek: <code>{html.escape(str(e))}</code>")
    if src.photo:
        kind, file_id, content = "photo", src.photo[-1].file_id, src.caption_html or ""
    elif src.video:
        kind, file_id, content = "video", src.video.file_id, src.caption_html or ""
    elif src.animation:
        kind, file_id, content = "animation", src.animation.file_id, src.caption_html or ""
    elif src.text_html:
        kind, file_id, content = "text", None, src.text_html
    else:
        return await reply(update, "⚠️ Sadece metin, fotoğraf, video veya GIF zamanlanabilir.")
    cur = db.run("INSERT INTO schedules(chat_id,kind,file_id,content,interval,delete_after,pin,del_prev) "
                 "VALUES(?,?,?,?,?,?,?,?)",
                 (chat.id, kind, file_id, content, interval, o["sil"], o["sabitle"], o["eskisil"]))
    sid = cur.lastrowid
    add_sched_job(context.job_queue, sid, interval, o["ilk"] or interval)
    await reply(update, f"✅ Zamanlama <b>#{sid}</b> oluşturuldu.\n{KIND_TR[kind]} • her <b>{fmt_dur(interval)}</b>"
                        f"\nİlk gönderim: {fmt_dur(o['ilk'] or interval)} sonra"
                        + (f"\n🗑 {fmt_dur(o['sil'])} sonra silinir" if o["sil"] else "")
                        + ("\n📌 Sabitlenir" if o["sabitle"] else "")
                        + ("\n♻️ Eskisi silinir" if o["eskisil"] else "")
                        + "\n\nYönetmek için: /zamanlar")


def render_schedules(chat_id: int):
    rows = db.many("SELECT * FROM schedules WHERE chat_id=? ORDER BY id", (chat_id,))
    if not rows:
        return ("📭 Bu grupta zamanlanmış içerik yok.\n\nBir mesajı yanıtlayıp "
                "<code>/zamanla 6sa</code> yazarak oluşturabilirsiniz."), Markup([[Btn("❌ Kapat", callback_data="sch:close:0")]])
    lines, kb = ["⏱ <b>Zamanlanmış İçerikler</b>\n"], []
    for r in rows:
        extra = []
        if r["delete_after"]:
            extra.append(f"🗑{fmt_dur(r['delete_after'])}")
        if r["pin"]:
            extra.append("📌")
        if r["del_prev"]:
            extra.append("♻️")
        lines.append(f"{'▶️' if r['active'] else '⏸'} <b>#{r['id']}</b> · {KIND_TR[r['kind']]} · her {fmt_dur(r['interval'])} "
                     + " ".join(extra))
        kb.append([
            Btn(("⏸ Durdur" if r["active"] else "▶️ Başlat") + f" #{r['id']}", callback_data=f"sch:tog:{r['id']}"),
            Btn("📤 Şimdi", callback_data=f"sch:now:{r['id']}"),
            Btn("🗑 Sil", callback_data=f"sch:del:{r['id']}"),
        ])
    kb.append([Btn("❌ Kapat", callback_data="sch:close:0")])
    return "\n".join(lines), Markup(kb)


@admin_only()
async def cmd_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = render_schedules(update.effective_chat.id)
    await reply(update, text, reply_markup=kb)


async def schedule_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, sid_s = q.data.split(":")
    chat = q.message.chat
    if not await is_admin(chat, q.from_user.id):
        return await q.answer("⛔ Sadece yöneticiler kullanabilir.", show_alert=True)
    if action == "close":
        await q.answer()
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return
    sid = int(sid_s)
    s = db.one("SELECT * FROM schedules WHERE id=? AND chat_id=?", (sid, chat.id))
    note = None
    if not s:
        note = "Kayıt bulunamadı."
    elif action == "del":
        db.run("DELETE FROM schedules WHERE id=?", (sid,))
        reset_job(context.job_queue, f"sched:{sid}")
        note = "🗑 Silindi."
    elif action == "tog":
        new = 0 if s["active"] else 1
        db.run("UPDATE schedules SET active=? WHERE id=?", (new, sid))
        if new:
            add_sched_job(context.job_queue, sid, s["interval"])
        else:
            reset_job(context.job_queue, f"sched:{sid}")
        note = "▶️ Başlatıldı." if new else "⏸ Durduruldu."
    elif action == "now":
        ok = await run_schedule(context.bot, context.job_queue, sid)
        note = "📤 Gönderildi." if ok else "❌ Gönderilemedi (pasif olabilir)."
    await q.answer(note)
    text, kb = render_schedules(chat.id)
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        pass


# ============================================================
# 10. HATA YAKALAYICI, SAĞLIK SUNUCUSU, KEEP-ALIVE
# ============================================================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        logger.error("⚠️ CONFLICT: Aynı token ile BAŞKA bir bot örneği çalışıyor "
                     "(eski deploy, yerel bilgisayar veya ikinci servis). Sadece birini bırakın.")
    elif isinstance(err, NetworkError):  # TimedOut dahil
        logger.warning("Ağ hatası (geçici): %s", err)
    else:
        logger.error("Güncelleme işlenirken hata:", exc_info=err)


async def health(_request):
    return web.Response(text="OK - bot calisiyor", status=200)


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("🌐 Sağlık sunucusu %s portunda aktif.", port)
    return runner


async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url):
                pass
    except Exception as e:
        logger.debug("Keep-alive başarısız: %s", e)


def load_jobs(app: Application):
    """Yeniden başlatmadan sonra portal ve zamanlama işlerini geri yükler."""
    jq, now = app.job_queue, int(time.time())
    n = 0
    for s in db.many("SELECT id, interval, last_run FROM schedules WHERE active=1"):
        first = max(5, s["last_run"] + s["interval"] - now) if s["last_run"] else s["interval"]
        add_sched_job(jq, s["id"], s["interval"], first)
        n += 1
    for p in db.many("SELECT chat_id, interval FROM portals WHERE interval>0"):
        set_portal_job(jq, p["chat_id"], p["interval"])
        n += 1
    if os.getenv("RENDER_EXTERNAL_URL"):
        jq.run_repeating(keepalive_job, interval=600, first=120, name="keepalive")
    logger.info("⏱ %s zamanlanmış iş geri yüklendi.", n)


# ============================================================
# 11. BAŞLATICI
# ============================================================
def build_application(token: str) -> Application:
    app = Application.builder().token(token).defaults(Defaults(parse_mode=ParseMode.HTML)).build()
    app.add_error_handler(on_error)

    commands = {
        ("start",): cmd_start,
        ("yardim", "help"): cmd_help,
        ("id",): cmd_id,
        ("ban",): cmd_ban,
        ("unban",): cmd_unban,
        ("mute",): cmd_mute,
        ("unmute",): cmd_unmute,
        ("uyar",): cmd_warn,
        ("uyarisil",): cmd_unwarn,
        ("sil",): cmd_purge,
        ("fkur",): cmd_fed_create,
        ("fbagla",): cmd_fed_join,
        ("fayril",): cmd_fed_leave,
        ("fbilgi",): cmd_fed_info,
        ("fban",): cmd_fban,
        ("funban",): cmd_funban,
        ("fbanlist",): cmd_fbanlist,
        ("portal",): cmd_portal,
        ("zamanla",): cmd_schedule,
        ("zamanlar",): cmd_schedules,
        ("hosgeldin",): cmd_welcome,
        ("hosgeldinkapat",): cmd_welcome_off,
        ("hosgeldinsure",): cmd_welcome_del,
    }
    for names, fn in commands.items():
        app.add_handler(CommandHandler(list(names), fn))

    app.add_handler(CallbackQueryHandler(help_cb, pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(schedule_cb, pattern=r"^sch:"))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.PINNED_MESSAGE, on_pinned_service))
    return app


async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.critical("TELEGRAM_BOT_TOKEN bulunamadı. Environment Variables ayarını kontrol edin.")
        sys.exit(1)

    app = build_application(token)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    runner = await start_health_server()  # Render portu önce açılsın
    try:
        async with app:  # initialize + shutdown
            await app.start()
            load_jobs(app)
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )
            me = await app.bot.get_me()
            logger.info("🚀 @%s çalışıyor. Dinleme modunda...", me.username)
            await stop.wait()
            logger.info("Kapatılıyor...")
            await app.updater.stop()
            await app.stop()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot kapatıldı.")

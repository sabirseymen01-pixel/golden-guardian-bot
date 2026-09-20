import logging
import sqlite3
import os
import asyncio
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Telegram Kütüphaneleri
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- PORT VE RENDER SAĞLIK SUNUCUSU ---
PORT = int(os.environ.get("PORT", 10000))
TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("GoldenGuardianUltimate")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Golden Guardian Ultimate Engine is Live!")
    def log_message(self, format, *args):
        pass

def start_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"Sunucu hatası: {e}")

# --- VERİTABANI BAĞLANTISI ---
DB_NAME = "guardian_pro.db"

def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS uyarilar (user_id INTEGER PRIMARY KEY, uyari_sayisi INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS fed_banlar (user_id INTEGER PRIMARY KEY, sebep TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS federasyon_gruplar (chat_id INTEGER PRIMARY KEY, grup_adi TEXT, grup_linki TEXT)")
    conn.commit()
    conn.close()

init_db()

# --- YETKİ VE HEDEF KONTROLÜ ---
async def is_admin(update: Update, user_id: int) -> bool:
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return None, "Mesaj yok"
    if msg.reply_to_message:
        u = msg.reply_to_message.from_user
        return u.id, f"Yanıtlanan: {u.full_name}"
    if context.args:
        arg = context.args[0]
        if arg.lstrip("-").isdigit():
            return int(arg), f"ID: {arg}"
    return None, "Hedef bulunamadı"

# --- GÜVENLİK FİLTRESİ ---
YASAKLI_KELIMELER = ["amina", "orospu", "piç", "sik", "sikerim", "göt", "kahpe", "salak"]

async def text_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.message.from_user
    chat = update.effective_chat

    if not user or user.id == context.bot.id or chat.type == "private":
        return

    # Federasyon Ban Kontrolü
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM fed_banlar WHERE user_id = ?", (user.id,))
    banned = cursor.fetchone()
    conn.close()

    if banned:
        try:
            await update.message.delete()
            await chat.ban_member(user.id)
        except Exception:
            pass
        return

    if await is_admin(update, user.id):
        return

    text = update.message.text.lower()
    for word in YASAKLI_KELIMELER:
        if word in text:
            try:
                await update.message.delete()
                await chat.restrict_member(
                    user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=timedelta(minutes=10)
                )
                await update.message.reply_text(f"⚠️ {user.full_name}, yasaklı kelime nedeniyle 10 dakika susturuldu.")
            except Exception:
                pass
            break

# --- KOMUTLAR VE ARAYÜZ ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if not await is_admin(update, user.id):
        return

    keyboard = [
        [InlineKeyboardButton("📊 İstatistikler", callback_data="stats"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="portal_menu")],
        [InlineKeyboardButton("🛠️ Yardım", callback_data="help_menu")]
    ]
    
    await context.bot.send_message(
        chat.id,
        f"🛡️ **Golden Guardian Pro**\n\nSelam {user.full_name}, sistem tamamen aktif ve çalışır durumda.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def portal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_admin(update, update.message.from_user.id):
        return
    link = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    kb = [[InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl", url=link)]]
    await update.message.reply_text("👑 **Canlı Sohbet Portalı**", reply_markup=InlineKeyboardMarkup(kb))

async def fedbagla_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_admin(update, update.message.from_user.id):
        return
    link = f"https://t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO federasyon_gruplar (chat_id, grup_adi, grup_linki) VALUES (?, ?, ?)", (chat.id, chat.title, link))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Grup federasyon ağına başarıyla bağlandı!")

async def defol_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id):
        return
    target_id, info = await resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("⚠️ Lütfen kullanıcının mesajına yanıt verin veya ID yazın.")
        return
    try:
        await update.effective_chat.ban_member(target_id)
        await update.message.reply_text(f"🔨 Kullanıcı gruptan atıldı. ({info})")
    except Exception as e:
        await update.message.reply_text(f"Hata: {e}")

async def itaat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id):
        return
    target_id, info = await resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("⚠️ Lütfen susturulacak kullanıcının mesajına yanıt verin.")
        return
    try:
        await update.effective_chat.restrict_member(target_id, permissions=ChatPermissions(can_send_messages=False), until_date=timedelta(minutes=10))
        await update.message.reply_text(f"🤐 Kullanıcı 10 dakika susturuldu. ({info})")
    except Exception as e:
        await update.message.reply_text(f"Hata: {e}")

# --- BUTON YÖNETİCİSİ ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat = update.effective_chat

    if data == "stats":
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM federasyon_gruplar")
        g = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fed_banlar")
        b = cursor.fetchone()[0]
        conn.close()
        
        text = f"📊 **İstatistikler:**\n• Bağlı Grup: `{g}`\n• Küresel Ban: `{b}`"
        kb = [[InlineKeyboardButton("🔙 Geri", callback_data="back")]]
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "portal_menu":
        link = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
        kb = [[InlineKeyboardButton("💬 Sohbete Katıl", url=link)]]
        await context.bot.send_message(chat.id, "👑 **Canlı Sohbet Portalı**", reply_markup=InlineKeyboardMarkup(kb))
        await query.answer("Portal gönderildi!")

    elif data == "help_menu":
        text = "🛠️ **Komutlar:**\n• `/portal` - Sohbet butonu\n• `/fedbagla` - Ağa bağla\n• `/defol` - Banla\n• `/itaat` - Sustur"
        kb = [[InlineKeyboardButton("🔙 Geri", callback_data="back")]]
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "back":
        keyboard = [
            [InlineKeyboardButton("📊 İstatistikler", callback_data="stats"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="portal_menu")],
            [InlineKeyboardButton("🛠️ Yardım", callback_data="help_menu")]
        ]
        await query.edit_message_text(text="🛡️ **Golden Guardian Pro**", reply_markup=InlineKeyboardMarkup(keyboard))

# --- HATA YAKALAYICI ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Güncelleme işlenirken hata oluştu: {context.error}", exc_info=context.error)

# --- ANA BAŞLATICI ---
def main():
    if not TOKEN:
        logger.critical("BOT_TOKEN bulunamadı!")
        return

    # Sağlık sunucusunu arka planda başlat
    threading.Thread(target=start_server, daemon=True).start()

    # Application Builder ile temiz yapı
    app = ApplicationBuilder().token(TOKEN).build()

    # Komut Kayıtları
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("portal", portal_cmd))
    app.add_handler(CommandHandler("fedbagla", fedbagla_cmd))
    app.add_handler(CommandHandler("defol", defol_cmd))
    app.add_handler(CommandHandler("itaat", itaat_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_filter))
    
    app.add_error_handler(error_handler)

    logger.info("Bot polling modunda başlatılıyor...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

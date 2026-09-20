
import logging
import sqlite3
import os
import threading
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram Kütüphaneleri
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- 1. SİSTEM YAPILANDIRMASI VE LOGLAMA ---
PORT = int(os.environ.get("PORT", 10000))
TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("GoldenGuardianUltra")

# --- 2. RENDER SAĞLIK SUNUCUSU ---
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Golden Guardian Pro V6: Stable Core is Online!")
    def log_message(self, format, *args):
        pass

def run_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), PingHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"Sağlık sunucusu hatası: {e}")

# --- 3. VERİTABANI MOTORU ---
DB_NAME = "guardian_pro.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS uyarilar (user_id INTEGER PRIMARY KEY, uyari_sayisi INTEGER DEFAULT 0)")
        cursor.execute("CREATE TABLE IF NOT EXISTS fed_banlar (user_id INTEGER PRIMARY KEY, sebep TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS federasyon_gruplar (chat_id INTEGER PRIMARY KEY, grup_adi TEXT, grup_linki TEXT)")
        conn.commit()
        conn.close()
        logger.info("Veritabanı başarıyla doğrulandı.")
    except Exception as e:
        logger.error(f"Veritabanı Kurulum Hatası: {e}")

init_db()

# --- 4. GLOBAL HATA YAKALAYICI (Kritik Çökme Önleyici) ---
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Telegram Güncelleme Hatası: {context.error}", exc_info=context.error)

# --- 5. YETKİ VE HEDEF KONTROL YARDIMCILARI ---
async def is_admin(update: Update, user_id: int) -> bool:
    try:
        chat = update.effective_chat
        if not chat or chat.type == "private":
            return True
        member = await chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return None, "Mesaj yok"

    # Yöntem A: Yanıt verilen mesaj
    if msg.reply_to_message:
        target_user = msg.reply_to_message.from_user
        return target_user.id, f"Yanıtlanan: {target_user.full_name}"

    # Yöntem B: ID argümanı
    if context.args:
        arg = context.args[0]
        if arg.lstrip("-").isdigit():
            return int(arg), f"ID: {arg}"

    return None, "Hedef bulunamadı"

# --- 6. GÜVENLİK VE KÜFÜR FİLTRESİ ---
YASAKLI_KELIMELER = ["amina", "orospu", "piç", "sik", "sikerim", "göt", "kahpe", "salak"]

async def security_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    user = update.message.from_user
    chat = update.effective_chat

    if not user or user.id == context.bot.id or chat.type == "private":
        return

    # Federasyon Banlı mı?
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM fed_banlar WHERE user_id = ?", (user.id,))
        banned = cursor.fetchone()
        conn.close()
        if banned:
            await update.message.delete()
            await chat.ban_member(user.id)
            return
    except Exception:
        pass

    if await is_admin(update, user.id):
        return

    # Küfür Taraması
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
                warning_msg = await update.message.reply_text(
                    f"⚠️ {user.full_name}, yasaklı kelime nedeniyle 10 dakika susturuldu."
                )
                if context.job_queue:
                    context.job_queue.run_once(
                        lambda ctx: ctx.job.data.delete() if ctx.job.data else None,
                        10.0,
                        data=warning_msg
                    )
            except Exception as e:
                logger.error(f"Filtre uygulama hatası: {e}")
            break

# --- 7. KOMUTLAR VE ARAYÜZ ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if not await is_admin(update, user.id):
        return

    keyboard = [
        [InlineKeyboardButton("📊 İstatistikler", callback_data="menu_stats"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="menu_portal")],
        [InlineKeyboardButton("🛠️ Yardım Kılavuzu", callback_data="menu_help")]
    ]
    
    await context.bot.send_message(
        chat.id,
        f"🛡️ **Golden Guardian Pro**\n\nSelam {user.full_name}, yönetim paneli aktif.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def portal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_admin(update, update.message.from_user.id):
        return
        
    chat_id_str = str(chat.id).replace("-100", "")
    link = f"https://t.me/c/{chat_id_str}/1"
    keyboard = [[InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl", url=link)]]
    
    await update.message.reply_text(
        "👑 **Canlı Sohbet Portalı**\n\nAşağıdaki butona tıklayarak doğrudan sohbete katılabilirsiniz.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def fedbagla_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_admin(update, update.message.from_user.id):
        return

    link = f"https://t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO federasyon_gruplar (chat_id, grup_adi, grup_linki) VALUES (?, ?, ?)", (chat.id, chat.title, link))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ Bu grup başarıyla Federasyon Ağına entegre edildi!")
    except Exception as e:
        await update.message.reply_text(f"Entegrasyon hatası: {e}")

async def defol_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id):
        return
        
    target_id, info = await resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("⚠️ Lütfen kullanıcının mesajına **yanıt verin** veya **ID yazın**.")
        return

    try:
        await update.effective_chat.ban_member(target_id)
        await update.message.reply_text(f"🔨 İşlem başarılı: Kullanıcı gruptan atıldı. ({info})")
    except Exception as e:
        await update.message.reply_text(f"Banlama başarısız: {e}")

async def itaat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id):
        return
        
    target_id, info = await resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("⚠️ Lütfen susturmak istediğiniz kullanıcının mesajına **yanıt verin**.")
        return

    try:
        await update.effective_chat.restrict_member(
            target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=timedelta(minutes=10)
        )
        await update.message.reply_text(f"🤐 Kullanıcı 10 dakika süreyle susturuldu. ({info})")
    except Exception as e:
        await update.message.reply_text(f"Susturma başarısız: {e}")

# --- 8. BUTON YÖNETİCİSİ ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat = update.effective_chat

    if data == "menu_stats":
        try:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM federasyon_gruplar")
            g_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM fed_banlar")
            b_count = cursor.fetchone()[0]
            conn.close()
        except Exception:
            g_count, b_count = 0, 0

        text = f"📊 **Sistem İstatistikleri:**\n\n• Bağlı Federasyon Grubu: `{g_count}`\n• Küresel Yasaklı Üye: `{b_count}`"
        back_kb = [[InlineKeyboardButton("🔙 Geri Dön", callback_data="menu_back")]]
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(back_kb))

    elif data == "menu_portal":
        chat_id_str = str(chat.id).replace("-100", "")
        link = f"https://t.me/c/{chat_id_str}/1"
        keyboard = [[InlineKeyboardButton("💬 Sohbete Katıl", url=link)]]
        await context.bot.send_message(chat.id, "👑 **Canlı Sohbet Portalı**", reply_markup=InlineKeyboardMarkup(keyboard))
        await query.answer("Portal gönderildi!")

    elif data == "menu_help":
        help_text = "🛠️ **Hızlı Komut Kılavuzu:**\n\n• `/portal` - Sohbet butonu üretir\n• `/fedbagla` - Grubu ağa bağlar\n• `/defol [Yanıtla/ID]` - Ban atar\n• `/itaat [Yanıtla]` - 10 dk mute atar"
        back_kb = [[InlineKeyboardButton("🔙 Geri Dön", callback_data="menu_back")]]
        await query.edit_message_text(text=help_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(back_kb))

    elif data == "menu_back":
        keyboard = [
            [InlineKeyboardButton("📊 İstatistikler", callback_data="menu_stats"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="menu_portal")],
            [InlineKeyboardButton("🛠️ Yardım Kılavuzu", callback_data="menu_help")]
        ]
        await query.edit_message_text(text="🛡️ **Golden Guardian Pro**\n\nYönetim paneli ana menüsü:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# --- 9. ANA ÇALIŞTIRICI ---
def main():
    if not TOKEN:
        logger.critical("HATA: BOT_TOKEN bulunamadı!")
        return

    threading.Thread(target=run_health_server, daemon=True).start()

    application = Application.builder().token(TOKEN).build()

    # İşleyiciler
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("portal", portal_command))
    application.add_handler(CommandHandler("fedbagla", fedbagla_command))
    application.add_handler(CommandHandler("defol", defol_command))
    application.add_handler(CommandHandler("itaat", itaat_command))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), security_guard))
    
    # Hata Yakalayıcı Kaydı
    application.add_error_handler(global_error_handler)

    logger.info("Golden Guardian Pro V6 çalışmaya başlıyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

import os
import logging
import threading
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, ContextTypes

# --- 1. LOGLAMA AYARLARI ---
logging.basicConfig(
    stream=sys.stdout,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("RoseFedBot")

# --- 2. BELLEK YAPISI ---
federation_links = {}
timed_automations = {}

# --- 3. RENDER HEALTH CHECK SUNUCUSU ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server_address = ('0.0.0.0', port)
    try:
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info(f"Sağlık kontrolü sunucusu {port} portunda başlatıldı.")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Sağlık sunucusu hatası: {e}")

# --- 4. YETKİ KONTROLÜ ---
async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        return True
    try:
        member = await chat.get_member(user.id)
        return member.status in ["creator", "administrator"]
    except Exception as e:
        logger.error(f"Admin yetki kontrolü hatası: {e}")
        return False

# --- 5. FEDERASYON VE PORTAL SİSTEMİ ---
async def bind_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("Bu komutu kullanmak için yetkiniz yok.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Lütfen bağlanacak grubun kullanıcı adını girin. Örnek: /bagla @arayis_grubu")
        return

    current_chat_id = update.effective_chat.id
    target_group = args[0]
    federation_links[current_chat_id] = target_group
    
    clean_current = str(current_chat_id).replace("-100", "")
    clean_target = target_group.replace("@", "")

    keyboard = [
        [
            InlineKeyboardButton("💬 Chat Grubuna Git", url=f"https://t.me/{clean_current}"),
            InlineKeyboardButton("🔍 Arayış Grubuna Git", url=f"https://t.me/{clean_target}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    sent_message = await update.message.reply_text(
        f"✅ Federasyon başarıyla kuruldu!\nBağlanan Grup: {target_group}",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    try:
        await sent_message.pin()
    except Exception as e:
        logger.error(f"Mesaj sabitleme hatası: {e}")

async def unbind_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("Bu komutu kullanmak için yetkiniz yok.")
        return

    current_chat_id = update.effective_chat.id
    if current_chat_id in federation_links:
        del federation_links[current_chat_id]
        await update.message.reply_text("❌ Federasyon bağlantısı bu grup için kaldırıldı.")
    else:
        await update.message.reply_text("⚠️ Bu gruba tanımlı aktif bir federasyon bulunamadı.")

# --- 6. SÜRE VE OTOMASYON ---
async def set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("Yetkiniz yok.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Lütfen geçerli bir dakika süresi girin. Örnek: /sure 10")
        return

    duration = int(args[0])
    chat_id = update.effective_chat.id
    timed_automations[chat_id] = duration
    
    await update.message.reply_text(f"⏱️️ Bu grup için yayın ve metin süresi {duration} dakika olarak ayarlandı.")

# --- 7. MODERASYON ARAÇLARI ---
async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user.id
    if context.args:
        arg = context.args[0].replace("@", "")
        if arg.isdigit() or (arg.startswith("-") and arg[1:].isdigit()):
            return int(arg)
        try:
            member = await update.effective_chat.get_member(arg)
            return member.user.id
        except Exception:
            return arg
    return None

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("⚠️ Hedef kullanıcı belirtilmedi.")
        return
    try:
        await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=target)
        await update.message.reply_text("🔨 Kullanıcı banlandı.")
    except Exception as e:
        await update.message.reply_text(f"❌ İşlem başarısız: {e}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    target = await resolve_target(update, context)
    if not target: return
    try:
        await context.bot.unban_chat_member(chat_id=update.effective_chat.id, user_id=target, only_if_banned=True)
        await update.message.reply_text("🔓 Ban kaldırıldı.")
    except Exception as e:
        await update.message.reply_text(f"❌ İşlem başarısız: {e}")

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    target = await resolve_target(update, context)
    if not target: return
    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target,
            permissions=ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text("🔇 Kullanıcı susturuldu.")
    except Exception as e:
        await update.message.reply_text(f"❌ İşlem başarısız: {e}")

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    target = await resolve_target(update, context)
    if not target: return
    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True
            )
        )
        await update.message.reply_text("🔊 Susturma kaldırıldı.")
    except Exception as e:
        await update.message.reply_text(f"❌ İşlem başarısız: {e}")

async def handle_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    cmd = update.message.text.split()[0].replace('/', '')
    target_name = "Bilinmeyen Hedef"
    if update.message.reply_to_message:
        target_name = update.message.reply_to_message.from_user.first_name
    elif context.args:
        target_name = context.args[0]
    
    await update.message.reply_text(f"👑 '{cmd}' komutu başarıyla uygulandı!\nHedef: {target_name}")

# --- 8. KULLANIM KILAVUZU ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **Bot Komut Kılavuzu**\n\n"
        "• `/bagla @grup` - Federasyon portalı kurar ve sabitler.\n"
        "• `/ayir` - Portalı kaldırır.\n"
        "• `/sure [dakika]` - Süre otomasyonunu ayarlar.\n"
        "• `/ban`, `/unban`, `/mute`, `/unmute` - Moderasyon komutları.\n"
        "• `/itaat`, `/kralice` - Özel protokoller."
    )
    await update.message.reply_text(help_text, parse_message="Markdown")

# --- 9. MAIN ---
def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN çevresel değişkeni bulunamadı!")
        sys.exit(1)
    
    # Sağlık sunucusunu başlat
    server_thread = threading.Thread(target=run_health_server, daemon=True)
    server_thread.start()

    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler(["yardim", "help"], help_command))
    application.add_handler(CommandHandler("bagla", bind_group))
    application.add_handler(CommandHandler("ayir", unbind_group))
    application.add_handler(CommandHandler("sure", set_duration))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("unmute", unmute_user))
    application.add_handler(CommandHandler(["itaat", "kralice", "krallice"], handle_custom_command))

    logger.info("Bot polling başlatılıyor...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

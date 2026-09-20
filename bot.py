import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# Loglama ayarlarını detaylı (DEBUG) moda alalım
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Bellek Yapısı
federation_links = {}  
timed_messages = {}     

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    logger.info(f"Sağlık kontrolü sunucusu {port} portunda başlatılıyor...")
    httpd.serve_forever()

async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        return True
    member = await chat.get_member(user.id)
    return member.status in ["creator", "administrator"]

async def bind_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("Bu komutu kullanmak için yetkiniz yok.")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Lütfen bağlanacak grubun kullanıcı adını veya ID'sini girin. Örnek: /bagla @arayis_grubu")
        return

    current_chat_id = update.effective_chat.id
    target_group = args[0]
    federation_links[current_chat_id] = target_group
    
    keyboard = [
        [
            InlineKeyboardButton("💬 Chat Grubuna Git", url=f"https://t.me/{str(current_chat_id).replace('-100', '')}"),
            InlineKeyboardButton("🔍 Arayış Grubuna Git", url=f"https://t.me/{target_group.replace('@', '')}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    sent_message = await update.message.reply_text(
        f"✅ Federasyon başarıyla kuruldu!\nBağlanan Grup: {target_group}",
        reply_markup=reply_markup
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

async def set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("Yetkiniz yok.")
        return

    args = context.args
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_text("Lütfen geçerli bir dakika süresi girin. Örnek: /sure 10")
        return

    duration = int(args[0])
    chat_id = update.effective_chat.id
    timed_messages[chat_id] = duration
    
    await update.message.reply_text(f"⏱️ Bu grup için yayın ve metin süresi {duration} dakika olarak ayarlandı.")

async def handle_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("Bu komutu kullanmak için yetkiniz yok.")
        return

    target_user = None
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user.username or update.message.reply_to_message.from_user.first_name
    elif context.args:
        target_input = context.args[0]
        target_user = target_input
    else:
        await update.message.reply_text("⚠️ Lütfen bir kullanıcıyı yanıtlayın ya da kullanıcı adı/ID belirtin.")
        return

    command_name = update.message.text.split()[0].replace('/', '')
    await update.message.reply_text(f"👑 '{command_name}' komutu başarıyla uygulandı!\nHedef: {target_user}")

def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "BURAYA_BOT_TOKEN_Gelecek")
    
    # Sağlık sunucusunu başlat
    server_thread = threading.Thread(target=run_health_server, daemon=True)
    server_thread.start()

    application = Application.builder().token(TOKEN).build()

    # Komutlar
    application.add_handler(CommandHandler("bagla", bind_group))
    application.add_handler(CommandHandler("ayir", unbind_group))
    application.add_handler(CommandHandler("sure", set_duration))
    application.add_handler(CommandHandler("itaat", handle_target_user))
    application.add_handler(CommandHandler("krallice", handle_target_user))
    application.add_handler(CommandHandler("kralice", handle_target_user))

    logger.info("Bot poling başlatılıyor...")
    # drop_pending_updates=True ile birikmiş eski takılı kalmış istekleri temizliyoruz
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

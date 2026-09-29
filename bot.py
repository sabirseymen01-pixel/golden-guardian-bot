import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, ContextTypes

# --- 1. DETAYLI LOGLAMA SİSTEMİ ---
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("RoseFedBot")

# --- 2. RENDER HEALTH CHECK SUNUCUSU ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Rose-Style Fed Bot is active and running smoothly!")
    
    def log_message(self, format, *args):
        return # Terminalin gereksiz HTTP loglarıyla kirlenmesini önler

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    logger.info(f"Sağlık kontrolü sunucusu {port} portunda başarıyla başlatıldı.")
    httpd.serve_forever()

# --- 3. BELLEK VE VERİ YÖNETİMİ ---
federation_links = {}  # { chat_id: target_group }
timed_automations = {} # { chat_id: duration_in_minutes }

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

# --- 5. ROSE BOT TARZI FEDERASYON VE PORTAL SİSTEMİ ---
async def bind_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("⚠️ Bu komutu kullanmak için yönetici yetkiniz olmalıdır.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("📌 Kullanım: /bagla @hedef_arama_grubu")
        return

    current_chat_id = update.effective_chat.id
    target_group = args[0]
    federation_links[current_chat_id] = target_group

    clean_current = str(current_chat_id).replace("-100", "")
    clean_target = target_group.replace("@", "")
    
    keyboard = [
        [
            InlineKeyboardButton("💬 Chat Grubuna Git", url=f"https://t.me/{clean_current}"),
            InlineKeyboardButton("🔍 Arayış / Portal Grubu", url=f"https://t.me/{clean_target}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    portal_message = (
        f"🌐 **Federasyon Portalı Aktifleştirildi!**\n\n"
        f"• **Bağlı Sohbet:** `{current_chat_id}`\n"
        f"• **Portal Ağı:** `{target_group}`\n\n"
        f"Tüm üyeler yukarıdaki butonlar aracılığıyla ağlar arası geçiş yapabilir."
    )

    sent_message = await update.message.reply_text(portal_message, reply_markup=reply_markup, parse_mode="Markdown")
    
    try:
        await sent_message.pin()
        logger.info(f"Federasyon portalı başarıyla sabitlendi: Chat ID {current_chat_id}")
    except Exception as e:
        logger.error(f"Portal sabitleme hatası: {e}")

async def unbind_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("⚠️ Yetkiniz yok.")
        return

    current_chat_id = update.effective_chat.id
    if current_chat_id in federation_links:
        del federation_links[current_chat_id]
        await update.message.reply_text("❌ Bu grup için federasyon bağlantısı ve portal kaldırıldı.")
        logger.info(f"Federasyon kaldırıldı: Chat ID {current_chat_id}")
    else:
        await update.message.reply_text("⚠️ Bu gruba tanımlı aktif bir federasyon portalı bulunamadı.")

# --- 6. AKILLI SÜRE VE OTOMASYON YÖNETİMİ ---
async def set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("⚠️ Yetkiniz yok.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("⏱️ Lütfen geçerli bir süre belirtin. Örnek: `/sure 15` (15 dakika)", parse_mode="Markdown")
        return

    duration = int(args[0])
    chat_id = update.effective_chat.id
    timed_automations[chat_id] = duration

    await update.message.reply_text(
        f"✅ Otomasyon süresi güncellendi!\n"
        f"⏱️ Bu gruptaki metin/görsel akış süreleri: **{duration} dakika** olarak ayarlandı.",
        parse_mode="Markdown"
    )
    logger.info(f"Süre ayarlandı -> Chat: {chat_id}, Süre: {duration} dk")

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
        await update.message.reply_text("⚠️ Hedef kullanıcı belirtilmedi (Yanıtlayın veya ID/Username yazın).")
        return
    try:
        await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=target)
        await update.message.reply_text("🔨 Kullanıcı federasyon kuralları gereğince banlandı.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ban işlemi başarısız: {e}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    target = await resolve_target(update, context)
    if not target: return
    try:
        await context.bot.unban_chat_member(chat_id=update.effective_chat.id, user_id=target, only_if_banned=True)
        await update.message.reply_text("🔓 Kullanıcının banı kaldırıldı.")
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
        await update.message.reply_text(f"❌ Mute hatası: {e}")

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
        await update.message.reply_text("🔊 Kullanıcının susturması kaldırıldı.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute hatası: {e}")

# --- 8. ÖZEL YÖNETİM KOMUTLARI ---
async def handle_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    cmd = update.message.text.split()[0].replace('/', '')
    target_name = "Bilinmeyen Hedef"
    if update.message.reply_to_message:
        target_name = update.message.reply_to_message.from_user.first_name
    elif context.args:
        target_name = context.args[0]
    
    await update.message.reply_text(f"👑 **{cmd.capitalize()}** protokolü uygulandı!\nHedef: `{target_name}`", parse_mode="Markdown")

# --- 9. KULLANIM KILAVUZU (YARDIM MENÜSÜ) ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **Rose Fed Bot - Kullanım Kılavuzu**\n\n"
        "🌐 **Federasyon & Portal Komutları:**\n"
        "• `/bagla @grup_adi` - Gruplar arası portal kurar ve sabite (pin) çeker.\n"
        "• `/ayir` - Mevcut gruptaki federasyon portalını kaldırır.\n\n"
        "⏱️ **Süre & Otomasyon Komutları:**\n"
        "• `/sure [dakika]` - Grup içi metin/görsel akış süresini ayarlar (Örn: `/sure 15`).\n\n"
        "🔨 **Moderasyon Araçları:**\n"
        "• `/ban` - Kullanıcıyı banlar (Yanıtlayarak veya @kullanıcı yazarak).\n"
        "• `/unban` - Kullanıcının banını kaldırır.\n"
        "• `/mute` - Kullanıcıyı susturur.\n"
        "• `/unmute` - Kullanıcının susturmasını kaldırır.\n\n"
        "👑 **Özel Protokoller:**\n"
        "• `/itaat`, `/kralice`, `/krallice` - Özel yönetim komutları."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

# --- 10. ANA ÇALIŞTIRMA VE POLLING ---
def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN çevresel değişkeni bulunamadı!")
        return

    # Arka planda Render Health Check sunucusunu başlat
    server_thread = threading.Thread(target=run_health_server, daemon=True)
    server_thread.start()

    # Telegram Application Yapılandırması
    application = Application.builder().token(TOKEN).build()

    # Komut Yönlendiricileri (Handlers)
    application.add_handler(CommandHandler("yardim", help_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("bagla", bind_group))
    application.add_handler(CommandHandler("ayir", unbind_group))
    application.add_handler(CommandHandler("sure", set_duration))
    
    # Moderasyon
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("unmute", unmute_user))
    
    # Özel Protokoller
    application.add_handler(CommandHandler(["itaat", "kralice", "krallice"], handle_custom_command))

    logger.info("Bot Long-Polling mekanizması ile başlatılıyor...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

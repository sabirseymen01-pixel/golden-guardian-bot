import os
import asyncio
import logging
from aiohttp import web
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import config
import database
import filters as custom_filters

# Loglama Konfigürasyonu
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Render'ın ücretsiz Web Service kontrolü için dummy (sahte) HTTP sunucusu
async def handle_ping(request):
    return web.Response(text="Bot 7/24 Aktif!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render'ın atadığı PORT değişkenini dinler, yoksa 8080 kullanır
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web sunucusu {port} portunda başlatıldı (Render ücretsiz plan uyumu).")

# Botun kendi gönderdiği bilgilendirme mesajlarını otomatik silme fonksiyonu
async def send_auto_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, delay: int = 10):
    try:
        sent_message = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=sent_message.message_id)
    except Exception as e:
        logger.error(f"Otomatik mesaj silme hatası: {e}")

# Hedef kullanıcıyı ID veya Reply üzerinden tespit etme
async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user

    if context.args:
        arg = context.args[0]
        if arg.isdigit():
            user_id = int(arg)
            try:
                chat_member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
                return chat_member.user
            except Exception:
                return None
    return None

# /start & /help Komutu
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 **Grup Yönetim & Moderasyon Botu**\n\n"
        "Grup düzenini sağlamak, küfür/argo içerikleri filtrelemek ve yetkili komutlarını yürütmek için aktifim.\n\n"
        "🛠 **Temel Özellikler:**\n"
        "• **Otomatik Küfür Filtresi:** Yasaklı kelimeler anında silinir ve kullanıcı uyarılır.\n"
        "• **Uyarı Sistemi:** 3 uyarı alan kullanıcı gruptan otomatik engellenir.\n"
        "• **Temiz Sohbet:** Botun attığı bilgilendirme mesajları 10 saniye sonra otomatik silinir.\n\n"
        "📜 **Yönetici Komutları:**\n"
        "• `/defol` - Kullanıcıyı gruptan banlar.\n"
        "• `/undefol` - Kullanıcının engelini kaldırır.\n"
        "• `/itaat` - Kullanıcıyı susturur (Mute).\n"
        "• `/unitaat` - Kullanıcının susturmasını kaldırır (Unmute).\n"
        "• `/warn` - Kullanıcıya manuel uyarı verir.\n"
        "• `/unwarn` - Kullanıcının uyarılarını sıfırlar.\n\n"
        "💡 *Komutları bir mesajı yanıtlayarak veya yanına ID yazarak kullanabilirsiniz.*"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

# /defol -> Banlama
async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ İşlem yapılacak kullanıcıyı yanıtlayarak veya ID belirterek yazın.")
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        logger.info(f"BAN: {user.full_name} ({user.id}) banlandı.")
        await send_auto_delete_message(context, chat_id, f"🚫 {user.full_name} gruptan engellendi.")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı banlanırken yetki hatası oluştu.")

# /undefol -> Ban Kaldırma
async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id

    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Banı kaldırılacak kullanıcı ID'sini girin.")
        return

    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        logger.info(f"UNBAN: {user.full_name} ({user.id}) engeli kaldırıldı.")
        await send_auto_delete_message(context, chat_id, f"✅ {user.full_name} kullanıcısının engeli kaldırıldı.")
    except Exception as e:
        logger.error(f"Unban Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Ban kaldırılırken hata oluştu.")

# /itaat -> Mute (Susturma)
async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id

    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Susturulacak kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    try:
        no_permissions = ChatPermissions(can_send_messages=False)
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=no_permissions)
        logger.info(f"MUTE: {user.full_name} ({user.id}) susturuldu.")
        await send_auto_delete_message(context, chat_id, f"🔇 {user.full_name} susturuldu.")
    except Exception as e:
        logger.error(f"Mute Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı susturulurken yetki hatası oluştu.")

# /unitaat -> Unmute (Susturma Kaldırma)
async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id

    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Susturması kaldırılacak kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    try:
        full_permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=full_permissions)
        logger.info(f"UNMUTE: {user.full_name} ({user.id}) susturması kaldırıldı.")
        await send_auto_delete_message(context, chat_id, f"🔊 {user.full_name} kullanıcısının susturması kaldırıldı.")
    except Exception as e:
        logger.error(f"Unmute Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Susturma kaldırılırken hata oluştu.")

# /warn -> Uyarı Verme
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id

    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Uyarı verilecek kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    count = database.add_warning(chat_id, user.id)
    logger.info(f"WARN: {user.full_name} ({user.id}) uyarıldı ({count}/3).")

    if count >= 3:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            database.reset_warnings(chat_id, user.id)
            await send_auto_delete_message(context, chat_id, f"🚫 {user.full_name} 3 uyarı sınırına ulaştığı için gruptan engellendi!")
        except Exception as e:
            logger.error(f"Warn-Ban Hatası: {e}")
            await send_auto_delete_message(context, chat_id, "❌ Kullanıcı 3 uyarıya ulaştı fakat banlanırken yetki hatası oluştu.")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ {user.full_name} uyarıldı! Toplam Uyarı: {count}/3")

# /unwarn -> Uyarı Sıfırlama
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id

    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Uyarısı sıfırlanacak kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    database.reset_warnings(chat_id, user.id)
    await send_auto_delete_message(context, chat_id, f"✅ {user.full_name} kullanıcısının tüm uyarıları sıfırlandı.")

# Otomatik Küfür Silme ve Otomatik Silinen Uyarı Mesajı
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    chat_id = update.effective_chat.id
    user = update.effective_user

    if custom_filters.contains_profanity(text):
        try:
            await update.message.delete()
            logger.info(f"KÜFÜR SİLİNDİ: {user.full_name} mesajı silindi.")
            
            count = database.add_warning(chat_id, user.id)
            
            if count >= 3:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                database.reset_warnings(chat_id, user.id)
                await send_auto_delete_message(context, chat_id, f"🚫 {user.full_name} yasaklı kelime kullanımı ve 3 uyarı sınırı nedeniyle engellendi.")
            else:
                await send_auto_delete_message(context, chat_id, f"⚠️ {user.full_name}, yasaklı kelime kullandığınız için mesajınız silindi! Uyarı: {count}/3")
        except Exception as e:
            logger.error(f"Küfür Silme Hatası: {e}")

async def post_init(application):
    # Bot başlayınca arka planda sahte web sunucusunu açar
    asyncio.create_task(start_web_server())

def main():
    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()

    # Komutlar
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("defol", cmd_defol))
    app.add_handler(CommandHandler("undefol", cmd_undefol))
    app.add_handler(CommandHandler("itaat", cmd_itaat))
    app.add_handler(CommandHandler("unitaat", cmd_unitaat))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))

    # Mesaj Dinleyicisi
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    logger.info("Bot başlatılıyor...")
    app.run_polling()

if __name__ == "__main__":
    main()

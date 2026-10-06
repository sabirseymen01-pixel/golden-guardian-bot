import logging
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

# Loglama Arayüzü Konfigürasyonu
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Hedef kullanıcıyı ID, Yanıtlama (Reply) veya Kullanıcı Adı ile tespit etme
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
        elif arg.startswith("@"):
            username_target = arg[1:].lower()
            # Bot API kullanıcı adından direkt ID bulamaz, bu yüzden mesaj yanıtı veya ID kullanımı tavsiye edilir.
            await update.message.reply_text("⚠️ Kullanıcı adı ile işlem Telegram API kısıtlaması nedeniyle sadece yanıtlayarak veya Kullanıcı ID ile kesin çalışır.")
            return None
    return None

# /defol -> Banlama
async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    if not user:
        await update.message.reply_text("❌ İşlem yapılacak kullanıcıyı yanıtlayarak veya ID belirterek yazın. Örn: /defol 12345678")
        return

    try:
        await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=user.id)
        logger.info(f"BAN: {user.full_name} ({user.id}) banlandı. İşlemi yapan: {update.effective_user.id}")
        await update.message.reply_text(f"🚫 {user.full_name} gruptan engellendi (Ban).")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")
        await update.message.reply_text("❌ Kullanıcı banlanırken bir hata oluştu. Yetkilerimi kontrol edin.")

# /undefol -> Ban Kaldırma
async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    if not user:
        await update.message.reply_text("❌ Banı kaldırılacak kullanıcı ID'sini girin. Örn: /undefol 12345678")
        return

    try:
        await context.bot.unban_chat_member(chat_id=update.effective_chat.id, user_id=user.id, only_if_banned=True)
        logger.info(f"UNBAN: {user.full_name} ({user.id}) banı kaldırıldı.")
        await update.message.reply_text(f"✅ {user.full_name} kullanıcısının engeli kaldırıldı.")
    except Exception as e:
        logger.error(f"Unban Hatası: {e}")
        await update.message.reply_text("❌ Ban kaldırılırken hata oluştu.")

# /itaat -> Mute (Susturma)
async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    if not user:
        await update.message.reply_text("❌ Susturulacak kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    try:
        no_permissions = ChatPermissions(can_send_messages=False)
        await context.bot.restrict_chat_member(chat_id=update.effective_chat.id, user_id=user.id, permissions=no_permissions)
        logger.info(f"MUTE: {user.full_name} ({user.id}) susturuldu.")
        await update.message.reply_text(f"🔇 {user.full_name} susturuldu (Mute).")
    except Exception as e:
        logger.error(f"Mute Hatası: {e}")
        await update.message.reply_text("❌ Kullanıcı susturulurken bir hata oluştu.")

# /unitaat -> Unmute (Susturma Kaldırma)
async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    if not user:
        await update.message.reply_text("❌ Susturması kaldırılacak kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    try:
        full_permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        await context.bot.restrict_chat_member(chat_id=update.effective_chat.id, user_id=user.id, permissions=full_permissions)
        logger.info(f"UNMUTE: {user.full_name} ({user.id}) susturması kaldırıldı.")
        await update.message.reply_text(f"🔊 {user.full_name} kullanıcısının susturması kaldırıldı.")
    except Exception as e:
        logger.error(f"Unmute Hatası: {e}")
        await update.message.reply_text("❌ Susturma kaldırılırken hata oluştu.")

# /warn -> Uyarı Verme (3 Uyarı = Ban)
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    if not user:
        await update.message.reply_text("❌ Uyarı verilecek kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    chat_id = update.effective_chat.id
    count = database.add_warning(chat_id, user.id)
    logger.info(f"WARN: {user.full_name} ({user.id}) uyarılması ({count}/3).")

    if count >= 3:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            database.reset_warnings(chat_id, user.id)
            await update.message.reply_text(f"🚫 {user.full_name} 3 uyarı sınırına ulaştığı için gruptan engellendi!")
        except Exception as e:
            logger.error(f"Warn-Ban Hatası: {e}")
            await update.message.reply_text("❌ Kullanıcı 3 uyarıya ulaştı fakat banlanırken yetki hatası oluştu.")
    else:
        await update.message.reply_text(f"⚠️ {user.full_name} uyarıldı! Toplam Uyarı: {count}/3")

# /unwarn -> Uyarısını Temizleme
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    if not user:
        await update.message.reply_text("❌ Uyarısı sıfırlanacak kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    database.reset_warnings(update.effective_chat.id, user.id)
    await update.message.reply_text(f"✅ {user.full_name} kullanıcısının uyarıları sıfırlandı.")

# /start & /help -> Kullanma Kılavuzu
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **Grup Yönetim Botı Kullanım Kılavuzu**\n\n"
        "**Yönetim Komutları:**\n"
        "• `/defol` - Kullanıcıyı gruptan banlar.\n"
        "• `/undefol` - Kullanıcının banını kaldırır.\n"
        "• `/itaat` - Kullanıcıyı susturur (Mute).\n"
        "• `/unitaat` - Kullanıcının susturmasını kaldırır.\n"
        "• `/warn` - Kullanıcıya uyarı verir (3 uyarıda otomatik banlar).\n"
        "• `/unwarn` - Kullanıcının tüm uyarılarını sıfırlar.\n"
        "• `/help` - Bu kılavuzu gösterir.\n\n"
        "ℹ️ *Komutlar bir mesaja yanıt verilerek veya yanına Kullanıcı ID yazılarak çalıştırılabilir.*"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

# Küfür Filtreleme Kontrolü
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    if custom_filters.contains_profanity(text):
        try:
            await update.message.delete()
            logger.info(f"KÜFÜR SİLİNDİ: {update.effective_user.full_name} mesajı silindi.")
            user = update.effective_user
            chat_id = update.effective_chat.id
            count = database.add_warning(chat_id, user.id)
            
            if count >= 3:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                database.reset_warnings(chat_id, user.id)
                await context.bot.send_message(chat_id, f"🚫 {user.full_name} yasaklı kelime kullanımı ve 3 uyarı sınırı nedeniyle engellendi.")
            else:
                await context.bot.send_message(chat_id, f"⚠️ {user.full_name}, yasaklı kelime kullandığınız için mesajınız silindi! Uyarı: {count}/3")
        except Exception as e:
            logger.error(f"Küfür Silme Hatası: {e}")

def main():
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # Komut Yöneticileri
    app.add_handler(CommandHandler("defol", cmd_defol))
    app.add_handler(CommandHandler("undefol", cmd_undefol))
    app.add_handler(CommandHandler("itaat", cmd_itaat))
    app.add_handler(CommandHandler("unitaat", cmd_unitaat))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))

    # Mesaj Yöneticisi (Küfür / Argo kontrolü)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    logger.info("Bot başlatılıyor...")
    app.run_polling()

if __name__ == "__main__":
    main()

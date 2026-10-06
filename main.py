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

# Render ücretsiz plan port uyumu için dummy HTTP sunucusu
async def handle_ping(request):
    return web.Response(text="Bot 7/24 Aktif!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web sunucusu {port} portunda başlatıldı.")

# Otomatik silinen mesaj fonksiyonu (10 saniye delay)
async def send_auto_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, delay: int = 10):
    try:
        sent_message = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=sent_message.message_id)
    except Exception as e:
        logger.error(f"Otomatik mesaj silme hatası: {e}")

# Hedef kullanıcıyı bulma (Reply veya ID)
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

# /start & /help Kılavuz Arayüzü
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 **Grup Yönetim & Moderasyon Botu**\n\n"
        "Grup düzenini sağlamak, küfür/argo içerikleri filtrelemek ve yetkili komutlarını yürütmek için aktifim.\n\n"
        "🛠 **Gelişmiş Özellikler:**\n"
        "• **Dinamik Hoş Geldin Mesajı:** Özelleştirilebilir karşılama mesajı.\n"
        "• **Otomatik Küfür Filtresi:** Yasaklı kelimeler anında silinir.\n"
        "• **Sohbet Kilidi:** Yönetici komutuyla grubu tamamen kapatma/açma.\n"
        "• **3 Uyarı Sistemi:** 3 uyarı alan kullanıcı gruptan otomatik engellenir.\n"
        "• **Temiz Sohbet:** Bot mesajları 10 saniye sonra otomatik silinir.\n\n"
        "📜 **Yönetici Komutları:**\n"
        "• `/welcome <mesaj>` - Hoş geldin mesajını değiştirir. (Metin içinde `{user}` kullanabilirsiniz)\n"
        "• `/kilit kapat` - Gruba mesaj yazmayı kapatır.\n"
        "• `/kilit ac` - Gruba mesaj yazmayı açar.\n"
        "• `/defol` - Kullanıcıyı banlar.\n"
        "• `/undefol` - Kullanıcının engeli kaldırır.\n"
        "• `/itaat` - Kullanıcıyı susturur (Mute).\n"
        "• `/unitaat` - Kullanıcının susturmasını kaldırır.\n"
        "• `/warn` - Kullanıcıya manuel uyarı verir.\n"
        "• `/unwarn` - Kullanıcının uyarılarını sıfırlar."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

# /welcome -> Hoş Geldin Mesajını Değiştirme
async def cmd_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        current_msg = database.get_welcome_message(chat_id)
        info_msg = (
            "⚠️ **Kullanım:** `/welcome <yeni mesajınız>`\n\n"
            "Yeni katılan kişiyi etiketlemek istediğiniz yere `{user}` yazabilirsiniz.\n\n"
            f"📌 **Mevcut Hoş Geldin Mesajı:**\n{current_msg}"
        )
        await send_auto_delete_message(context, chat_id, info_msg, delay=15)
        return

    new_welcome_text = " ".join(context.args)
    database.set_welcome_message(chat_id, new_welcome_text)
    await send_auto_delete_message(context, chat_id, "✅ **Hoş geldin mesajı başarıyla güncellendi!**")

# Hoş Geldin Mesajı Gönderme (Yeni Üye Katılınca)
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    raw_template = database.get_welcome_message(chat_id)

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        
        custom_msg = raw_template.replace("{user}", member.mention_markdown())
        await send_auto_delete_message(context, chat_id, custom_msg, delay=15)

# /kilit -> Sohbet Kilitleme / Açma
async def cmd_kilit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await send_auto_delete_message(context, chat_id, "⚠ Lütfen bir durum belirtin: `/kilit kapat` veya `/kilit ac`")
        return

    durum = context.args[0].lower()

    try:
        if durum in ["kapat", "lock", "kapali"]:
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
            await send_auto_delete_message(context, chat_id, "🔒 **Sohbet kilitlendi.**")
        elif durum in ["ac", "aç", "unlock", "acik"]:
            permissions = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
            await send_auto_delete_message(context, chat_id, "🔓 **Sohbet kilitleri kaldırıldı.**")
        else:
            await send_auto_delete_message(context, chat_id, "❌ Geçersiz komut. Kullanım: `/kilit kapat` veya `/kilit ac`")
    except Exception as e:
        logger.error(f"Kilit Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Sohbet kilit durumu değiştirilemedi.")

# /defol -> Banlama
async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcıyı yanıtlayın veya ID belirtin.")
        return
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        await send_auto_delete_message(context, chat_id, f"🚫 {user.full_name} gruptan engellendi.")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")

# /undefol -> Ban Kaldırma
async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı ID'si girin.")
        return
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        await send_auto_delete_message(context, chat_id, f"✅ {user.full_name} engeli kaldırıldı.")
    except Exception as e:
        logger.error(f"Unban Hatası: {e}")

# /itaat -> Mute
async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcıyı yanıtlayın veya ID belirtin.")
        return
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False))
        await send_auto_delete_message(context, chat_id, f"🔇 {user.full_name} susturuldu.")
    except Exception as e:
        logger.error(f"Mute Hatası: {e}")

# /unitaat -> Unmute
async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcıyı yanıtlayın veya ID belirtin.")
        return
    try:
        full_permissions = ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=full_permissions)
        await send_auto_delete_message(context, chat_id, f"🔊 {user.full_name} susturması kaldırıldı.")
    except Exception as e:
        logger.error(f"Unmute Hatası: {e}")

# /warn -> Uyarı
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    count = database.add_warning(chat_id, user.id)
    if count >= 3:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            database.reset_warnings(chat_id, user.id)
            await send_auto_delete_message(context, chat_id, f"🚫 {user.full_name} 3 uyarıya ulaştığı için engellendi!")
        except Exception as e:
            logger.error(f"Warn-Ban Hatası: {e}")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ {user.full_name} uyarıldı! Uyarı: {count}/3")

# /unwarn -> Uyarı Sıfırlama
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcıyı yanıtlayın veya ID belirtin.")
        return

    database.reset_warnings(chat_id, user.id)
    await send_auto_delete_message(context, chat_id, f"✅ {user.full_name} tüm uyarıları sıfırlandı.")

# Mesaj Dinleyicisi (Küfür Filtresi)
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    chat_id = update.effective_chat.id
    user = update.effective_user

    if custom_filters.contains_profanity(text):
        try:
            await update.message.delete()
            count = database.add_warning(chat_id, user.id)
            if count >= 3:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                database.reset_warnings(chat_id, user.id)
                await send_auto_delete_message(context, chat_id, f"🚫 {user.full_name} 3 uyarı sınırından engellendi.")
            else:
                await send_auto_delete_message(context, chat_id, f"⚠️ {user.full_name}, yasaklı kelime kullandınız! Uyarı: {count}/3")
        except Exception as e:
            logger.error(f"Küfür Silme Hatası: {e}")

# Uygulama başladığında web sunucusunu arka planda ayağa kaldırır
async def post_init(application):
    asyncio.create_task(start_web_server())

def main():
    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()

    # Komutlar
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("welcome", cmd_set_welcome))
    app.add_handler(CommandHandler("defol", cmd_defol))
    app.add_handler(CommandHandler("undefol", cmd_undefol))
    app.add_handler(CommandHandler("itaat", cmd_itaat))
    app.add_handler(CommandHandler("unitaat", cmd_unitaat))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("kilit", cmd_kilit))

    # Dinleyiciler
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    logger.info("Bot ve HTTP sunucusu başlatılıyor...")
    app.run_polling()

if __name__ == "__main__":
    main()

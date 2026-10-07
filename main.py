import os
import asyncio
import logging
from aiohttp import web
from telegram import Update, ChatPermissions
from telegram.helpers import escape_markdown
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.request import HTTPXRequest
from telegram.error import NetworkError, TimedOut
import config
import database
import filters as custom_filters

# Loglama Konfigürasyonu
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Geçici Kullanıcı Önbelleği (Username -> User Objesi eşleşmesi için)
USER_CACHE = {}

# Render için Dummy HTTP Sunucusu
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

# Otomatik silinen mesaj fonksiyonu (10 saniye delay, Markdown çökme korumalı)
async def send_auto_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, delay: int = 10, parse_mode: str = "Markdown"):
    try:
        sent_message = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=sent_message.message_id)
    except Exception as e:
        logger.error(f"Otomatik mesaj silme hatası (Markdown): {e}. Düz metin deneniyor...")
        try:
            sent_message = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
            await asyncio.sleep(delay)
            await context.bot.delete_message(chat_id=chat_id, message_id=sent_message.message_id)
        except Exception as err:
            logger.error(f"Mesaj tamamen gönderilemedi: {err}")

# Kullanıcının Admin olup olmadığını kontrol eden yardımcı fonksiyon
async def is_user_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logger.error(f"Admin kontrol hatası: {e}")
        return False

# Hedef kullanıcıyı bulma (Reply, Mention, @username veya ID)
async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # 1. YÖNTEM: Mesaj yanıtlanmışsa (Reply)
    if update.message and update.message.reply_to_message:
        return update.message.reply_to_message.from_user

    # 2. YÖNTEM: Telegram Metin Etiketlemesi (Entity Mention / Açılır listeden seçme)
    if update.message and update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention":
                return entity.user

    # 3. YÖNTEM: Komutun yanında parametre varsa (@username veya ID)
    if context.args:
        arg = context.args[0].strip()

        # Sayısal ID girildiyse
        if arg.isdigit():
            user_id = int(arg)
            try:
                chat_member = await context.bot.get_chat_member(chat_id, user_id)
                return chat_member.user
            except Exception:
                return None

        # @kullanici_adi veya doğrudan kullanici_adi girildiyse
        elif arg.startswith("@") or not arg.isdigit():
            username = arg.lstrip("@").lower()

            # A) Önce Veritabanından Kullanıcı ID'sini sorgula
            db_user_id = database.get_user_id_by_username(chat_id, username)
            if db_user_id:
                try:
                    chat_member = await context.bot.get_chat_member(chat_id, db_user_id)
                    return chat_member.user
                except Exception as e:
                    logger.error(f"Veritabanından bulunan kullanıcı verisi çekilemedi: {e}")

            # B) Veritabanında yoksa gruptaki yöneticilerde ara
            try:
                administrators = await context.bot.get_chat_administrators(chat_id)
                for admin in administrators:
                    if admin.user.username and admin.user.username.lower() == username:
                        database.save_user(chat_id, admin.user.id, admin.user.username)
                        return admin.user
            except Exception as e:
                logger.error(f"Yönetici arama hatası: {e}")

            # C) Önbellekte (USER_CACHE) kaydı var mı kontrol et
            if username in USER_CACHE:
                return USER_CACHE[username]

    return None

# Global Hata Yakalayıcı (Bad Gateway ve Network hataları için)
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f"Geçici ağ hatası yakalandı (Sistem çalışmaya devam ediyor): {context.error}")
    else:
        logger.error("İşlenmeyen hata meydana geldi:", exc_info=context.error)

# /start & /help Kılavuz Arayüzü
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 *Grup Yönetim & Moderasyon Botu*\n\n"
        "Grup düzenini sağlamak, küfür/argo içerikleri filtrelemek ve yetkili komutlarını yürütmek için aktifim.\n\n"
        "🛠 *Gelişmiş Özellikler:*\n"
        "• *Dinamik Hoş Geldin Mesajı:* Özelleştirilebilir karşılama mesajı.\n"
        "• *Otomatik Küfür Filtresi:* Yasaklı kelimeler anında silinir.\n"
        "• *Sohbet Kilidi:* Yönetici komutuyla grubu tamamen kapatma/açma.\n"
        "• *3 Uyarı Sistemi:* 3 uyarı alan kullanıcı gruptan otomatik engellenir.\n"
        "• *Temiz Sohbet:* Bot mesajları 10 saniye sonra otomatik silinir.\n\n"
        "📜 *Yönetici Komutları:*\n"
        "• `/welcome <mesaj>` - Hoş geldin mesajını değiştirir. (`{user}` etiketini kullanabilirsiniz)\n"
        "• `/kilit kapat` - Gruba mesaj yazmayı kapatır.\n"
        "• `/kilit ac` - Gruba mesaj yazmayı açar.\n"
        "• `/defol <@kullanici|ID|yanıt>` - Kullanıcıyı banlar.\n"
        "• `/undefol <ID|yanıt>` - Kullanıcının engelini kaldırır.\n"
        "• `/itaat <@kullanici|ID|yanıt>` - Kullanıcıyı susturur (Mute).\n"
        "• `/unitaat <@kullanici|ID|yanıt>` - Kullanıcının susturmasını kaldırır.\n"
        "• `/warn <@kullanici|ID|yanıt>` - Kullanıcıya manuel uyarı verir.\n"
        "• `/unwarn <@kullanici|ID|yanıt>` - Kullanıcının uyarılarını sıfırlar."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

# /welcome -> Hoş Geldin Mesajını Değiştirme
async def cmd_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_user_admin(chat_id, user_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    if not context.args:
        current_msg = database.get_welcome_message(chat_id)
        info_msg = (
            "⚠️ Kullanım: /welcome <yeni mesajınız>\n\n"
            "Yeni katılan kişiyi etiketlemek istediğiniz yere {user} yazabilirsiniz.\n\n"
            f"📌 Mevcut Hoş Geldin Mesajı:\n{current_msg}"
        )
        await send_auto_delete_message(context, chat_id, info_msg, delay=15, parse_mode=None)
        return

    new_welcome_text = " ".join(context.args)
    database.set_welcome_message(chat_id, new_welcome_text)
    await send_auto_delete_message(context, chat_id, "✅ Hoş geldin mesajı başarıyla güncellendi!", parse_mode=None)

# Hoş Geldin Mesajı Gönderme (Yeni Üye Katılınca)
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    raw_template = database.get_welcome_message(chat_id)

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        
        if member.username:
            USER_CACHE[member.username.lower()] = member
            database.save_user(chat_id, member.id, member.username)
            
        user_mention = member.mention_markdown()
        custom_msg = raw_template.replace("{user}", user_mention)
        await send_auto_delete_message(context, chat_id, custom_msg, delay=15, parse_mode="Markdown")

# /kilit -> Sohbet Kilitleme / Açma (v20+ Uyumlu)
async def cmd_kilit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_user_admin(chat_id, user_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    if not context.args:
        await send_auto_delete_message(context, chat_id, "⚠ Lütfen bir durum belirtin: /kilit kapat veya /kilit ac", parse_mode=None)
        return

    durum = context.args[0].lower()

    try:
        if durum in ["kapat", "lock", "kapali"]:
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
            await send_auto_delete_message(context, chat_id, "🔒 Sohbet kilitlendi.", parse_mode=None)
        elif durum in ["ac", "aç", "unlock", "acik"]:
            permissions = ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
            await send_auto_delete_message(context, chat_id, "🔓 Sohbet kilitleri kaldırıldı.", parse_mode=None)
        else:
            await send_auto_delete_message(context, chat_id, "❌ Geçersiz komut. Kullanım: /kilit kapat veya /kilit ac", parse_mode=None)
    except Exception as e:
        logger.error(f"Kilit Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Sohbet kilit durumu değiştirilemedi.", parse_mode=None)

# /defol -> Banlama
async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı. Lütfen yanıtlayın, ID yazın veya @kullanici_adi kullanın.", parse_mode=None)
        return

    if await is_user_admin(chat_id, user.id, context):
        await send_auto_delete_message(context, chat_id, "⚠ Başka bir yöneticiyi gruptan engelleyemezsiniz.", parse_mode=None)
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"🚫 {safe_name} gruptan engellendi.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı engellenirken bir hata oluştu.", parse_mode=None)

# /undefol -> Ban Kaldırma
async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı. Lütfen ID girin veya yanıtlayarak yazın.", parse_mode=None)
        return

    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"✅ {safe_name} engeli kaldırıldı.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Unban Hatası: {e}")

# /itaat -> Mute
async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı. Lütfen yanıtlayın, ID yazın veya @kullanici_adi kullanın.", parse_mode=None)
        return

    if await is_user_admin(chat_id, user.id, context):
        await send_auto_delete_message(context, chat_id, "⚠ Başka bir yöneticiyi susturamazsınız.", parse_mode=None)
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id, 
            user_id=user.id, 
            permissions=ChatPermissions(can_send_messages=False)
        )
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"🔇 {safe_name} susturuldu.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Mute Hatası: {e}")

# /unitaat -> Unmute (v20+ Uyumlu)
async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı. Lütfen yanıtlayın, ID yazın veya @kullanici_adi kullanın.", parse_mode=None)
        return

    try:
        full_permissions = ChatPermissions(
            can_send_messages=True, 
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True, 
            can_add_web_page_previews=True
        )
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=full_permissions)
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"🔊 {safe_name} susturması kaldırıldı.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Unmute Hatası: {e}")

# /warn -> Uyarı
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı. Lütfen yanıtlayın, ID yazın veya @kullanici_adi kullanın.", parse_mode=None)
        return

    if await is_user_admin(chat_id, user.id, context):
        await send_auto_delete_message(context, chat_id, "⚠️ Yöneticiye uyarı verilemez.", parse_mode=None)
        return

    count = database.add_warning(chat_id, user.id)
    safe_name = escape_markdown(user.full_name, version=1)
    if count >= 3:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            database.reset_warnings(chat_id, user.id)
            await send_auto_delete_message(context, chat_id, f"🚫 {safe_name} 3 uyarıya ulaştığı için engellendi!", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Warn-Ban Hatası: {e}")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ {safe_name} uyarıldı! Uyarı: {count}/3", parse_mode="Markdown")

# /unwarn -> Uyarı Sıfırlama
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı. Lütfen yanıtlayın, ID yazın veya @kullanici_adi kullanın.", parse_mode=None)
        return

    database.reset_warnings(chat_id, user.id)
    safe_name = escape_markdown(user.full_name, version=1)
    await send_auto_delete_message(context, chat_id, f"✅ {safe_name} tüm uyarıları sıfırlandı.", parse_mode="Markdown")

# Mesaj Dinleyicisi (Küfür Filtresi & Kullanıcı Kaydetme)
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id

    # Grupta işlem yapan HER kullanıcıyı hem veritabanına hem önbelleğe kaydet
    if user and user.username:
        USER_CACHE[user.username.lower()] = user
        database.save_user(chat_id, user.id, user.username)

    if not update.message.text:
        return

    text = update.message.text

    # Yöneticileri küfür filtresinden muaf tut
    if await is_user_admin(chat_id, user.id, context):
        return

    if custom_filters.contains_profanity(text):
        try:
            await update.message.delete()
            count = database.add_warning(chat_id, user.id)
            safe_name = escape_markdown(user.full_name, version=1)
            if count >= 3:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                database.reset_warnings(chat_id, user.id)
                await send_auto_delete_message(context, chat_id, f"🚫 {safe_name} 3 uyarı sınırından engellendi.", parse_mode="Markdown")
            else:
                await send_auto_delete_message(context, chat_id, f"⚠️ {safe_name}, yasaklı kelime kullandınız! Uyarı: {count}/3", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Küfür filtresi hatası: {e}")

async def post_init(application):
    database.init_db()
    asyncio.create_task(start_web_server())
    logger.info("Veritabanı kuruldu ve Web Sunucusu başlatıldı.")

def main():
    # Telegram API İstekleri için Gelişmiş Timeout Değerleri
    request_config = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .request(request_config)
        .post_init(post_init)
        .build()
    )

    # Global Hata İşleyici Kaydı
    app.add_error_handler(global_error_handler)

    # Handler Bağlantıları
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("welcome", cmd_set_welcome))
    app.add_handler(CommandHandler("kilit", cmd_kilit))
    app.add_handler(CommandHandler("defol", cmd_defol))
    app.add_handler(CommandHandler("undefol", cmd_undefol))
    app.add_handler(CommandHandler("itaat", cmd_itaat))
    app.add_handler(CommandHandler("unitaat", cmd_unitaat))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))

    # Yeni Katılan Üye Handler
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

    # Tüm Mesaj Tipleri İçin Önbellek ve Küfür Filtresi
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

    app.run_polling()

if __name__ == "__main__":
    main()

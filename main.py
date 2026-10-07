import os
import json
import asyncio
import logging
from aiohttp import web
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.request import HTTPXRequest
from telegram.error import NetworkError, TimedOut, TelegramError
import config
import database
import filters as custom_filters

# Loglama Konfigürasyonu
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Geçici Kullanıcı Önbelleği
USER_CACHE = {}

# --- BUTON VE AYAR YÖNETİMİ (config.json) ---
CONFIG_FILE = "config.json"

def get_button_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Config okuma hatası: {e}")
    return {"metin": "Diğer Gruba Geç 🚀", "url": "https://t.me/"}

def save_button_config(data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Config kaydetme hatası: {e}")

# Render / PaaS için Web Sunucusu
async def handle_ping(request):
    return web.Response(text="Bot 7/24 Aktif!")

async def start_web_server(app_context):
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web sunucusu {port} portunda başlatıldı.")

# Otomatik silinen mesaj fonksiyonu
async def _delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"Mesaj silinemedi: {e}")

async def send_auto_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, delay: int = 10, parse_mode: str = "Markdown"):
    try:
        sent_message = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        asyncio.create_task(_delete_after_delay(context, chat_id, sent_message.message_id, delay))
    except Exception as e:
        logger.error(f"Otomatik mesaj gönderme hatası ({parse_mode}): {e}. Düz metin deneniyor...")
        try:
            sent_message = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
            asyncio.create_task(_delete_after_delay(context, chat_id, sent_message.message_id, delay))
        except Exception as err:
            logger.error(f"Mesaj tamamen gönderilemedi: {err}")

# Mesaj silme yardımcısı
async def safe_delete_user_message(update: Update):
    if update.message:
        try:
            await update.message.delete()
        except Exception as e:
            logger.debug(f"Kullanıcı mesajı silinemedi: {e}")

# Admin Kontrolü
async def is_user_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logger.error(f"Admin kontrol hatası: {e}")
        return False

# Hedef kullanıcıyı bulma
async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if update.message and update.message.reply_to_message:
        return update.message.reply_to_message.from_user

    if update.message and update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention" and entity.user:
                return entity.user

    if context.args:
        arg = context.args[0].strip()
        if arg.isdigit():
            user_id = int(arg)
            try:
                chat_member = await context.bot.get_chat_member(chat_id, user_id)
                return chat_member.user
            except Exception:
                return None
        elif arg.startswith("@") or not arg.isdigit():
            username = arg.lstrip("@").lower()
            db_user_id = database.get_user_id_by_username(chat_id, username)
            if db_user_id:
                try:
                    chat_member = await context.bot.get_chat_member(chat_id, db_user_id)
                    return chat_member.user
                except Exception as e:
                    logger.error(f"Veritabanından kullanıcı çekilemedi: {e}")

            try:
                administrators = await context.bot.get_chat_administrators(chat_id)
                for admin in administrators:
                    if admin.user.username and admin.user.username.lower() == username:
                        database.save_user(chat_id, admin.user.id, admin.user.username)
                        return admin.user
            except Exception as e:
                logger.error(f"Yönetici arama hatası: {e}")

            if username in USER_CACHE:
                return USER_CACHE[username]

    return None

# Global Hata Yakalayıcı
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f"Geçici ağ hatası yakalandı: {context.error}")
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
        "• *Temiz Sohbet:* Bot mesajları 10 saniye sonra otomatik silinir.\n"
        "• *Dinamik Butonlu Duyuru:* Butonlu mesaj gönderip üst kısma sabitleme.\n\n"
        "📜 *Yönetici Komutları:*\n"
        "• `/welcome <mesaj>` - Hoş geldin mesajını değiştirir.\n"
        "• `/butonmetni <yazı>` - Varsayılan buton üzerindeki yazıyı değiştirir.\n"
        "• `/butonlinki <url>` - Varsayılan butonun yönlendireceği linki değiştirir.\n"
        "• `/duyuru <mesaj>` - Kayıtlı buton ile duyuru gönderir.\n"
        "• `/duyuru <mesaj> | <buton metni> | <link>` - Tek satırda özel butonlu duyuru gönderir.\n"
        "• `/kilit kapat / ac` - Gruba mesaj yazmayı kilitler veya açar.\n"
        "• `/defol <@kullanici|ID|yanıt>` - Kullanıcıyı banlar.\n"
        "• `/undefol <ID|yanıt>` - Engeli kaldırır.\n"
        "• `/itaat <@kullanici|ID|yanıt>` - Kullanıcıyı susturur.\n"
        "• `/unitaat <@kullanici|ID|yanıt>` - Susturmayı kaldırır.\n"
        "• `/warn <@kullanici|ID|yanıt>` - Kullanıcıya uyarı verir.\n"
        "• `/unwarn <@kullanici|ID|yanıt>` - Uyarıları sıfırlar."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

# /butonmetni
async def cmd_set_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, user_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    if not context.args:
        cfg = get_button_config()
        await send_auto_delete_message(context, chat_id, f"⚠️ Kullanım: /butonmetni <yeni yazı>\nMevcut Metin: {cfg['metin']}", parse_mode=None)
        return

    new_text = " ".join(context.args)
    cfg = get_button_config()
    cfg["metin"] = new_text
    save_button_config(cfg)
    safe_new_text = escape_markdown(new_text, version=1)
    await send_auto_delete_message(context, chat_id, f"✅ Buton metni güncellendi:\n👉 *{safe_new_text}*", parse_mode="Markdown")

# /butonlinki
async def cmd_set_button_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, user_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    if not context.args:
        cfg = get_button_config()
        await send_auto_delete_message(context, chat_id, f"⚠️ Kullanım: /butonlinki <https://t.me/...>\nMevcut Link: {cfg['url']}", parse_mode=None)
        return

    new_url = context.args[0].strip()
    if not (new_url.startswith("http://") or new_url.startswith("https://") or new_url.startswith("t.me/")):
        await send_auto_delete_message(context, chat_id, "❌ Lütfen geçerli bir URL veya Telegram linki girin.", parse_mode=None)
        return

    if new_url.startswith("t.me/"):
        new_url = "https://" + new_url

    cfg = get_button_config()
    cfg["url"] = new_url
    save_button_config(cfg)
    await send_auto_delete_message(context, chat_id, f"✅ Buton linki güncellendi:\n👉 {new_url}", parse_mode=None)

# /duyuru - YENİLENMİŞ VE ONARILMIŞ BUTONLU DUYURU FONKSİYONU
async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_user_admin(chat_id, user_id, context):
        await safe_delete_user_message(update)
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    raw_args = " ".join(context.args) if context.args else ""
    cfg = get_button_config()

    # '|' karakteri ile özel metin/buton/link ayrımı kontrol edilir
    if "|" in raw_args:
        parts = [p.strip() for p in raw_args.split("|")]
        announcement_text = parts[0] if parts[0] else "📌 *Duyuru*"
        button_text = parts[1] if len(parts) > 1 and parts[1] else cfg["metin"]
        button_url = parts[2] if len(parts) > 2 and parts[2] else cfg["url"]
    else:
        announcement_text = raw_args if raw_args else "📌 *Diğer Gruba Geçiş Yapabilirsiniz*"
        button_text = cfg["metin"]
        button_url = cfg["url"]

    # Link format kontrolü
    if button_url.startswith("t.me/"):
        button_url = "https://" + button_url
    elif not (button_url.startswith("http://") or button_url.startswith("https://")):
        button_url = "https://" + button_url

    # Inline Keyboard (Buton) Oluşturma
    keyboard = [[InlineKeyboardButton(text=button_text, url=button_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # Fotoğraflı yanıt var ise resimli butonlu duyuru paylaşır
        if update.message.reply_to_message and update.message.reply_to_message.photo:
            photo = update.message.reply_to_message.photo[-1].file_id
            sent_msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=announcement_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            sent_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=announcement_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

        # Mesajı grupta başa sabitleme (pin)
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=sent_msg.message_id)
        await safe_delete_user_message(update)

    except Exception as e:
        logger.error(f"Duyuru ve sabitleme hatası: {e}")
        await send_auto_delete_message(
            context, 
            chat_id, 
            "❌ Duyuru gönderilirken hata oluştu. Linkin geçerli bir URL (http:// veya https://) olduğundan emin olun.", 
            parse_mode=None
        )

# /welcome
async def cmd_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await safe_delete_user_message(update)

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

# Hoş Geldin Mesajı
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    raw_template = database.get_welcome_message(chat_id)

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue

        if member.username:
            USER_CACHE[member.username.lower()] = member
            database.save_user(chat_id, member.id, member.username)

        user_mention = member.mention_markdown(version=1)
        custom_msg = raw_template.replace("{user}", user_mention)
        await send_auto_delete_message(context, chat_id, custom_msg, delay=15, parse_mode="Markdown")

# /kilit
async def cmd_kilit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await safe_delete_user_message(update)

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

# /defol -> Ban
async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

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
        await send_auto_delete_message(context, chat_id, f"🚫 *{safe_name}* gruptan engellendi.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı engellenirken bir hata oluştu.", parse_mode=None)

# /undefol -> Unban
async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

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
        await send_auto_delete_message(context, chat_id, f"✅ *{safe_name}* engeli kaldırıldı.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Unban Hatası: {e}")

# /itaat -> Mute
async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

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
        await send_auto_delete_message(context, chat_id, f"🔇 *{safe_name}* susturuldu.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Mute Hatası: {e}")

# /unitaat -> Unmute
async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

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
        await send_auto_delete_message(context, chat_id, f"🔊 *{safe_name}* susturması kaldırıldı.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Unmute Hatası: {e}")

# /warn -> Uyarı
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

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
            await send_auto_delete_message(context, chat_id, f"🚫 *{safe_name}* 3 uyarıya ulaştığı için engellendi!", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Warn-Ban Hatası: {e}")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ *{safe_name}* uyarıldı! Uyarı: {count}/3", parse_mode="Markdown")

# /unwarn -> Uyarı Sıfırlama
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı. Lütfen yanıtlayın, ID yazın veya @kullanici_adi kullanın.", parse_mode=None)
        return

    database.reset_warnings(chat_id, user.id)
    safe_name = escape_markdown(user.full_name, version=1)
    await send_auto_delete_message(context, chat_id, f"✅ *{safe_name}* tüm uyarıları sıfırlandı.", parse_mode="Markdown")

# Mesaj Dinleyicisi
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id

    if user and user.username:
        USER_CACHE[user.username.lower()] = user
        database.save_user(chat_id, user.id, user.username)

    if not update.message.text:
        return

    text = update.message.text

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
                await send_auto_delete_message(context, chat_id, f"🚫 *{safe_name}* 3 uyarı sınırından engellendi.", parse_mode="Markdown")
            else:
                await send_auto_delete_message(context, chat_id, f"⚠️ *{safe_name}*, yasaklı kelime kullandınız! Uyarı: {count}/3", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Küfür filtresi hatası: {e}")

async def post_init(application):
    database.init_db()
    asyncio.create_task(start_web_server(application))
    logger.info("Veritabanı kuruldu ve Web Sunucusu başlatıldı.")

def main():
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

    app.add_error_handler(global_error_handler)

    # Handler Bağlantıları
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("welcome", cmd_set_welcome))
    app.add_handler(CommandHandler("butonmetni", cmd_set_button_text))
    app.add_handler(CommandHandler("butonlinki", cmd_set_button_url))
    app.add_handler(CommandHandler("duyuru", cmd_duyuru))
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

    # Polling başlatma
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

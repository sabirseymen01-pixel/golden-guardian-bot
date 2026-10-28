import os
import json
import asyncio
import logging
from aiohttp import web
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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

# --- GOLDEN STAFF MINI APP API ROTALARI & WEB SUNUCUSU ---
async def api_get_stats(request):
    """Mini App için gelişmiş istatistikleri JSON olarak döndürür."""
    try:
        stats = database.get_bot_statistics()
        return web.json_response(stats)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_get_logs(request):
    """Mini App için son log hareketlerini JSON olarak döndürür."""
    try:
        logs = database.get_recent_logs(limit=50)
        return web.json_response(logs)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def start_web_server(app_context):
    app = web.Application()
    
    # API Rotaları
    app.router.add_get("/api/stats", api_get_stats)
    app.router.add_get("/api/logs", api_get_logs)
    
    # Kök dizine girildiğinde index.html'i doğrudan HTML olarak sun
    async def handle_root(request):
        try:
            with open("public/index.html", "r", encoding="utf-8") as f:
                html_content = f.read()
            return web.Response(text=html_content, content_type="text/html")
        except Exception as e:
            return web.Response(text=f"Index okunamadı: {str(e)}", status=500)

    app.router.add_get("/", handle_root)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web sunucusu, API'ler ve Golden Staff Mini App {port} portunda başlatıldı.")

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

# Admin ve Kurucu Kontrolü (Founder ID Destekli)
async def is_user_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    # Render'a eklenen kurucu (FOUNDER_ID) kontrolü
    founder_id = os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) == str(founder_id).strip():
        return True

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
        
        try:
            user_id = int(arg)
            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            return chat_member.user
        except (ValueError, TelegramError):
            pass

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

    return None

# Global Hata Yakalayıcı
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f"Geçici ağ hatası yakalandı: {context.error}")
    else:
        logger.error(f"İşlenmeyen hata meydana geldi: {context.error}", exc_info=context.error)

# /start & /help (Komut listesi ve en altta Golden Staff Dokunmatik Paneli İle)
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 *Grup Yönetim & Golden Staff Güvenlik Botu*\n\n"
        "Grup düzenini sağlamak, argo/küfür, dış linkler ve yasaklı içerikleri otomatik engellemek için aktifim.\n\n"
        "📜 *Yönetici Komutları:*\n"
        "• `/welcome <mesaj>` - Hoş geldin mesajını değiştirir.\n"
        "• `/butonmetni <yazı>` - Varsayılan buton metnini değiştirir.\n"
        "• `/butonlinki <url>` - Varsayılan buton linkini değiştirir.\n"
        "• `/duyuru <mesaj>` - Duyuru gönderir.\n"
        "• `/zamanla <dakika>` - Periyodik paylaşım başlatır.\n"
        "• `/zamanladur` - Periyodik paylaşımı durdurur.\n"
        "• `/kilit kapat / ac` - Sohbeti kilitler/açar.\n"
        "• `/defol <kullanıcı>` - Kullanıcıyı banlar.\n"
        "• `/undefol <kullanıcı>` - Banı kaldırır.\n"
        "• `/itaat <kullanıcı>` - Susturur.\n"
        "• `/unitaat <kullanıcı>` - Susturmayı kaldırır.\n"
        "• `/warn <kullanıcı>` - Uyarı verir.\n"
        "• `/unwarn <kullanıcı>` - Uyarıları sıfırlar.\n\n"
        "Aşağıdaki butona dokunarak **Golden Staff Paneli**'ne giriş yapabilirsin! 👇"
    )

    # Render üzerindeki canlı web servisinizin adresi
    web_app_url = "https://golden-guardian-bot.onrender.com/"
    
    keyboard = [
        [InlineKeyboardButton(text="🛡️ Golden Staff Paneli", web_app=WebAppInfo(url=web_app_url))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

# --- ZAMANLAMA / SÜRELİ PAYLAŞIM EKLENTİSİ ---
async def cmd_zamanla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_user_admin(chat_id, user_id, context):
        await safe_delete_user_message(update)
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    if not update.message or not update.message.reply_to_message:
        await send_auto_delete_message(context, chat_id, "⚠️ Lütfen otomatik paylaşmak istediğiniz metne veya görsele yanıt verin.", delay=15)
        await safe_delete_user_message(update)
        return

    if not context.args or len(context.args) == 0:
        await send_auto_delete_message(context, chat_id, "⚠️ Süreyi dakika cinsinden belirtin. Örn: `/zamanla 30`", delay=15)
        await safe_delete_user_message(update)
        return

    try:
        dakika = int(context.args[0])
    except ValueError:
        await send_auto_delete_message(context, chat_id, "❌ Geçersiz süre! Lütfen sadece sayı girin.", delay=15)
        await safe_delete_user_message(update)
        return

    if dakika < 1:
        await send_auto_delete_message(context, chat_id, "⚠️ Süre en az 1 dakika olmalıdır.")
        await safe_delete_user_message(update)
        return

    saniye = dakika * 60
    target_message = update.message.reply_to_message

    job_data = {
        'chat_id': chat_id,
        'text': target_message.caption or target_message.text or "",
        'photo_id': target_message.photo[-1].file_id if target_message.photo else None
    }

    if context.job_queue is None:
        await send_auto_delete_message(context, chat_id, "❌ Sistem zamanlama altyapısı hazır değil.", delay=15)
        await safe_delete_user_message(update)
        return

    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_repeating(
        otomatik_paylasim_cb,
        interval=saniye,
        first=saniye,
        chat_id=chat_id,
        name=str(chat_id),
        data=job_data
    )

    await safe_delete_user_message(update)
    await send_auto_delete_message(context, chat_id, f"✅ Zamanlama Başlatıldı! Her {dakika} dakikada bir paylaşılacak.", delay=15)

async def otomatik_paylasim_cb(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    chat_id = data['chat_id']
    text = data['text']
    photo_id = data['photo_id']

    try:
        if photo_id:
            await context.bot.send_photo(chat_id=chat_id, photo=photo_id, caption=text, parse_mode="Markdown")
        elif text:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Zamanlanmış mesaj gönderme hatası: {e}")

async def cmd_zamanladur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_user_admin(chat_id, user_id, context):
        await safe_delete_user_message(update)
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    if context.job_queue is None:
        await send_auto_delete_message(context, chat_id, "❌ Zamanlayıcı altyapısı aktif değil.")
        await safe_delete_user_message(update)
        return

    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    await safe_delete_user_message(update)
    if not current_jobs:
        await send_auto_delete_message(context, chat_id, "❌ Aktif zamanlama bulunamadı.")
        return

    for job in current_jobs:
        job.schedule_removal()

    await send_auto_delete_message(context, chat_id, "🛑 Otomatik paylaşımlar durduruldu.")

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
        await send_auto_delete_message(context, chat_id, f"⚠️ Kullanım: /butonmetni <yeni yazı>\nMevcut: {cfg['metin']}", parse_mode=None)
        return

    new_text = " ".join(context.args)
    cfg = get_button_config()
    cfg["metin"] = new_text
    save_button_config(cfg)
    await send_auto_delete_message(context, chat_id, f"✅ Buton metni güncellendi: *{new_text}*", parse_mode="Markdown")

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
        await send_auto_delete_message(context, chat_id, f"⚠️ Kullanım: /butonlinki <url>\nMevcut: {cfg['url']}", parse_mode=None)
        return

    new_url = context.args[0].strip()
    if new_url.startswith("t.me/"):
        new_url = "https://" + new_url

    cfg = get_button_config()
    cfg["url"] = new_url
    save_button_config(cfg)
    await send_auto_delete_message(context, chat_id, f"✅ Buton linki güncellendi: {new_url}", parse_mode=None)

# /duyuru
async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_user_admin(chat_id, user_id, context):
        await safe_delete_user_message(update)
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    raw_args = " ".join(context.args) if context.args else ""
    cfg = get_button_config()

    if "|" in raw_args:
        parts = [p.strip() for p in raw_args.split("|")]
        announcement_text = parts[0] if parts[0] else "📌 *Duyuru*"
        button_text = parts[1] if len(parts) > 1 and parts[1] else cfg["metin"]
        button_url = parts[2] if len(parts) > 2 and parts[2] else cfg["url"]
    else:
        announcement_text = raw_args if raw_args else "📌 *Duyuru*"
        button_text = cfg["metin"]
        button_url = cfg["url"]

    if button_url.startswith("t.me/"):
        button_url = "https://" + button_url
    elif not (button_url.startswith("http://") or button_url.startswith("https://")):
        button_url = "https://" + button_url

    keyboard = [[InlineKeyboardButton(text=button_text, url=button_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if update.message.reply_to_message and update.message.reply_to_message.photo:
            photo = update.message.reply_to_message.photo[-1].file_id
            sent_msg = await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=announcement_text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            sent_msg = await context.bot.send_message(chat_id=chat_id, text=announcement_text, reply_markup=reply_markup, parse_mode="Markdown")

        await context.bot.pin_chat_message(chat_id=chat_id, message_id=sent_msg.message_id)
        await safe_delete_user_message(update)
        database.log_action(chat_id, "DUYURU", user_id, update.effective_user.username, "Grup duyurusu paylaşıldı ve sabitlendi.")
    except Exception as e:
        logger.error(f"Duyuru hatası: {e}")

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
        await send_auto_delete_message(context, chat_id, f"⚠️ Kullanım: /welcome <mesaj>\nEtiket için {{user}} yazın.\nMevcut: {current_msg}", delay=15, parse_mode=None)
        return

    new_welcome_text = " ".join(context.args)
    database.set_welcome_message(chat_id, new_welcome_text)
    await send_auto_delete_message(context, chat_id, "✅ Hoş geldin mesajı güncellendi!", parse_mode=None)

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    raw_template = database.get_welcome_message(chat_id)

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        if member.username:
            database.save_user(chat_id, member.id, member.username)

        user_mention = member.mention_markdown()
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
        await send_auto_delete_message(context, chat_id, "⚠ Kullanım: /kilit kapat veya /kilit ac", parse_mode=None)
        return

    durum = context.args[0].lower()
    try:
        if durum in ["kapat", "lock", "kapali"]:
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
            await send_auto_delete_message(context, chat_id, "🔒 Sohbet kilitlendi.", parse_mode=None)
            database.log_action(chat_id, "KILIT", user_id, update.effective_user.username, "Sohbet mesaj yazımına kapatıldı.")
        elif durum in ["ac", "aç", "unlock", "acik"]:
            permissions = ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
            await send_auto_delete_message(context, chat_id, "🔓 Sohbet kilitleri açıldı.", parse_mode=None)
            database.log_action(chat_id, "KILIT_AC", user_id, update.effective_user.username, "Sohbet kilitleri kaldırıldı.")
    except Exception as e:
        logger.error(f"Kilit Hatası: {e}")

# /defol (Ban)
async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı.", parse_mode=None)
        return

    if await is_user_admin(chat_id, user.id, context):
        await send_auto_delete_message(context, chat_id, "⚠ Yöneticiyi engelleyemezsiniz.", parse_mode=None)
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"🚫 *{safe_name}* engellendi.", parse_mode="Markdown")
        database.log_action(chat_id, "BAN", user.id, user.username, f"Admin tarafından banlandı.")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")

# /undefol (Unban)
async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı.", parse_mode=None)
        return

    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"✅ *{safe_name}* engeli kaldırıldı.", parse_mode="Markdown")
        database.log_action(chat_id, "UNBAN", user.id, user.username, "Kullanıcının banı kaldırıldı.")
    except Exception as e:
        logger.error(f"Unban Hatası: {e}")

# /itaat (Mute)
async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı.", parse_mode=None)
        return

    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False))
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"🔇 *{safe_name}* susturuldu.", parse_mode="Markdown")
        database.log_action(chat_id, "MUTE", user.id, user.username, "Kullanıcı susturuldu.")
    except Exception as e:
        logger.error(f"Mute Hatası: {e}")

# /unitaat (Unmute)
async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı.", parse_mode=None)
        return

    try:
        full_permissions = ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True,
            can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
            can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=full_permissions)
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"🔊 *{safe_name}* susturması kaldırıldı.", parse_mode="Markdown")
        database.log_action(chat_id, "UNMUTE", user.id, user.username, "Kullanıcının susturması kaldırıldı.")
    except Exception as e:
        logger.error(f"Unmute Hatası: {e}")

# /warn
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı.", parse_mode=None)
        return

    count = database.add_warning(chat_id, user.id)
    safe_name = escape_markdown(user.full_name, version=1)
    if count >= 3:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        database.reset_warnings(chat_id, user.id)
        await send_auto_delete_message(context, chat_id, f"🚫 *{safe_name}* 3 uyarıya ulaştığı için engellendi!", parse_mode="Markdown")
        database.log_action(chat_id, "WARN_BAN", user.id, user.username, "3 uyarı sınırına ulaştığı için otomatik banlandı.")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ *{safe_name}* uyarıldı! Uyarı: {count}/3", parse_mode="Markdown")
        database.log_action(chat_id, "WARN", user.id, user.username, f"Uyarı verildi ({count}/3)")

# /unwarn
async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Bu komutu sadece yöneticiler kullanabilir.", parse_mode=None)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı.", parse_mode=None)
        return

    database.reset_warnings(chat_id, user.id)
    safe_name = escape_markdown(user.full_name, version=1)
    await send_auto_delete_message(context, chat_id, f"✅ *{safe_name}* uyarıları sıfırlandı.", parse_mode="Markdown")
    database.log_action(chat_id, "UNWARN", user.id, user.username, "Uyarıları sıfırlandı.")

# --- @ADMIN BİLDİRİ VE ŞİKAYET SİSTEMİ (Grup İçi %100 Gizli & PM/Mini App Raporlu) ---
async def handle_admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.message or not update.message.text:
        return False

    text = update.message.text.lower()
    
    # Eğer mesajda @admin geçiyorsa ve bir mesaja yanıt verildiyse
    if "@admin" in text and update.message.reply_to_message:
        reporter = update.effective_user
        reported_msg = update.message.reply_to_message
        reported_user = reported_msg.from_user
        chat = update.effective_chat

        report_content = reported_msg.text or reported_msg.caption or "[Medya / Fotoğraf / Dosya]"

        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            
            # Şikayeti veritabanına logla (Sadece Golden Staff Mini App içinde görünecek)
            database.log_action(
                chat.id, 
                "ADMIN_REPORT", 
                reporter.id, 
                reporter.username or "Gizli", 
                f"Şikayet Edilen: {reported_user.full_name} | İçerik: {report_content[:50]}"
            )

            # Kullanıcının @admin yazan mesajını gruptan tamamen temizle (Sohbet asla kirlenmez)
            await safe_delete_user_message(update)

            # Yöneticilere (YT'lere) özel mesajla (PM) temiz rapor gönder
            admin_report_text = (
                f"🚨 *YENİ YÖNETİCİ BİLDİRİMİ*\n"
                f"📌 *Grup:* `{chat.title}`\n\n"
                f"👤 *Bildiren:* {reporter.full_name}\n"
                f"🎯 *Bildirilen:* {reported_user.full_name}\n\n"
                f"💬 *Şikayet Edilen Mesaj:*\n> _{report_content}_\n\n"
                f"✨ *Detaylar ve yönetim için Golden Staff Paneli'ni açabilirsin.*"
            )

            for admin in admins:
                if not admin.user.is_bot:
                    try:
                        await context.bot.send_message(
                            chat_id=admin.user.id,
                            text=admin_report_text,
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass # Botu özelden başlatmamış adminler için hata vermesini engeller

            return True
        except Exception as e:
            logger.error(f"@admin bildiri hatası: {e}")
            
    return False

# Gelişmiş Güvenlik ve Küfür Dinleyicisi
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    # Önce @admin bildiri kontrolü yap
    if await handle_admin_report(update, context):
        return

    user = update.effective_user
    chat_id = update.effective_chat.id

    if user and user.username:
        database.save_user(chat_id, user.id, user.username)

    if await is_user_admin(chat_id, user.id, context):
        return

    text = update.message.text or update.message.caption
    if not text:
        return

    # Yasaklı içerik, küfür, argo veya dış link tespit edilirse mesajı sil ve direkt banla
    if custom_filters.contains_profanity(text):
        try:
            await update.message.delete()
            safe_name = escape_markdown(user.full_name, version=1)
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            
            # Veritabanına güvenlik banı logunu kaydet
            database.log_action(chat_id, "SECURITY_BAN", user.id, user.username, f"Yasaklı içerik/link tespit edildi.")

            await send_auto_delete_message(
                context, 
                chat_id, 
                f"🚫 *{safe_name}* yasaklı içerik paylaştığı için gruptan kalıcı olarak engellendi!", 
                delay=10, 
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Güvenlik filtresi ban hatası: {e}")

async def post_init(application):
    database.init_db()
    asyncio.create_task(start_web_server(application))
    logger.info("Veritabanı kuruldu, Web Sunucusu ve Golden Staff API rotaları başlatıldı.")

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

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("welcome", cmd_set_welcome))
    app.add_handler(CommandHandler("butonmetni", cmd_set_button_text))
    app.add_handler(CommandHandler("butonlinki", cmd_set_button_url))
    app.add_handler(CommandHandler("duyuru", cmd_duyuru))
    app.add_handler(CommandHandler("zamanla", cmd_zamanla))
    app.add_handler(CommandHandler("zamanladur", cmd_zamanladur))
    app.add_handler(CommandHandler("kilit", cmd_kilit))
    app.add_handler(CommandHandler("defol", cmd_defol))
    app.add_handler(CommandHandler("undefol", cmd_undefol))
    app.add_handler(CommandHandler("itaat", cmd_itaat))
    app.add_handler(CommandHandler("unitaat", cmd_unitaat))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

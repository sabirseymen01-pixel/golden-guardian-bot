import os
import json
import asyncio
import logging
import time
import hashlib
from aiohttp import web
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ChatPermissions
from telegram.helpers import escape_markdown
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.request import HTTPXRequest
from telegram.error import TelegramError
import config
import database

# Loglama Konfigürasyonu
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

CONFIG_FILE = "config.json"

# --- GÜVENLİK & ACİL DURUM KİLİDİ DEĞİŞKENLERİ ---
ADMIN_ACTION_LIMITS = {}
MAX_ACTIONS_PER_MINUTE = 5
EMERGENCY_LOCKDOWN = False
BAD_WORDS = ["küfür1", "küfür2", "argo1", "mal", "aptal"]
SCHEDULED_TASKS = {}

# --- GOLDEN STAFF MINI APP API ROTALARI & WEB SUNUCUSU ---
async def api_get_stats(request):
    try:
        stats = database.get_bot_statistics()
        return web.json_response(stats)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_get_logs(request):
    try:
        logs = database.get_recent_logs(limit=50)
        return web.json_response(logs)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_get_admin_stats(request):
    try:
        stats = database.get_admin_performance_stats()
        return web.json_response(stats)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_get_data_cloud(request):
    try:
        tags = database.get_data_cloud_tags()
        return web.json_response(tags)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_get_lock_status(request):
    return web.json_response({"lockdown": EMERGENCY_LOCKDOWN})

async def api_toggle_lockdown(request):
    global EMERGENCY_LOCKDOWN
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        
        founder_id = os.environ.get("FOUNDER_ID")
        if not founder_id or str(user_id) != str(founder_id).strip():
            return web.json_response({"success": False, "error": "Yetkisiz işlem!"}, status=403)
        
        EMERGENCY_LOCKDOWN = not EMERGENCY_LOCKDOWN
        return web.json_response({"success": True, "lockdown": EMERGENCY_LOCKDOWN})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def api_verify_admin(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        
        founder_id = os.environ.get("FOUNDER_ID")
        if founder_id and str(user_id) == str(founder_id).strip():
            return web.json_response({"authorized": True, "is_founder": True})

        if database.is_authorized_staff(user_id):
            perms = database.get_staff_permissions(user_id)
            return web.json_response({"authorized": True, "is_founder": False, "permissions": perms})

        return web.json_response({"authorized": False})
    except Exception as e:
        return web.json_response({"authorized": False}, status=500)

async def api_get_staff_list(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        founder_id = os.environ.get("FOUNDER_ID")
        if not founder_id or str(user_id) != str(founder_id).strip():
            return web.json_response({"error": "Yetkisiz"}, status=403)

        staffs = database.get_all_staff()
        result = [{"user_id": s[0], "username": s[1], "full_name": s[2], "can_ban": s[3], "can_speak": s[4], "can_write": s[5], "is_muted": s[6], "warnings": s[7]} for s in staffs]
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_add_staff(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        input_value = str(data.get("staff_input", "")).strip()
        full_name = data.get("full_name", "Yetkili")

        founder_id = os.environ.get("FOUNDER_ID")
        if not founder_id or str(user_id) != str(founder_id).strip():
            return web.json_response({"success": False, "error": "Yetkisiz"}, status=403)

        if not input_value:
            return web.json_response({"success": False, "error": "Boş değer girilemez!"}, status=400)

        target_id = int(input_value) if input_value.isdigit() else int(hashlib.md5(input_value.encode()).hexdigest()[:8], 16)
        database.add_staff_member(target_id, input_value, full_name, user_id)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def api_remove_staff(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        target_id = int(data.get("target_id", 0))

        founder_id = os.environ.get("FOUNDER_ID")
        if not founder_id or str(user_id) != str(founder_id).strip():
            return web.json_response({"success": False, "error": "Yetkisiz"}, status=403)

        database.remove_staff_member(target_id)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def start_web_server(app_context):
    app = web.Application()
    app['bot'] = app_context.bot
    
    app.router.add_get("/api/stats", api_get_stats)
    app.router.add_get("/api/logs", api_get_logs)
    app.router.add_get("/api/admin-stats", api_get_admin_stats)
    app.router.add_get("/api/data-cloud", api_get_data_cloud)
    app.router.add_get("/api/lock-status", api_get_lock_status)
    app.router.add_post("/api/toggle-lockdown", api_toggle_lockdown)
    app.router.add_post("/api/verify-admin", api_verify_admin)
    app.router.add_post("/api/staff/list", api_get_staff_list)
    app.router.add_post("/api/staff/add", api_add_staff)
    app.router.add_post("/api/staff/remove", api_remove_staff)
    
    async def handle_root(request):
        try:
            with open("public/index.html", "r", encoding="utf-8") as f:
                html_content = f.read()
            return web.Response(text=html_content, content_type="text/html", headers={"Cache-Control": "no-cache"})
        except Exception as e:
            return web.Response(text=f"Index okunamadı: {str(e)}", status=500)

    app.router.add_get("/", handle_root)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web sunucusu, API'ler ve Golden Staff Mini App {port} portunda başlatıldı.")

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
        logger.error(f"Otomatik mesaj gönderme hatası: {e}")

async def safe_delete_user_message(update: Update):
    if update.message:
        try:
            await update.message.delete()
        except Exception as e:
            logger.debug(f"Kullanıcı mesajı silinemedi: {e}")

async def is_user_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    founder_id = os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) == str(founder_id).strip():
        return True

    perms = database.get_staff_permissions(user_id)
    if not database.is_authorized_staff(user_id) or perms["can_ban"] == 0:
        return False

    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logger.error(f"Admin kontrol hatası: {e}")
        return False

async def check_admin_rate_limit(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    global EMERGENCY_LOCKDOWN

    founder_id = os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) == str(founder_id).strip():
        return True

    if EMERGENCY_LOCKDOWN:
        return False

    now = time.time()
    key = (chat_id, user_id)
    
    if key not in ADMIN_ACTION_LIMITS:
        ADMIN_ACTION_LIMITS[key] = []

    ADMIN_ACTION_LIMITS[key] = [t for t in ADMIN_ACTION_LIMITS[key] if now - t < 60]

    if len(ADMIN_ACTION_LIMITS[key]) >= MAX_ACTIONS_PER_MINUTE:
        database.set_staff_permission(user_id, 'can_ban', 0)
        database.log_action(chat_id, "FLOOD_BAN", user_id, f"id_{user_id}", "Çok hızlı işlem yaptığı için ban yetkisi askıya alındı.")
        return False

    ADMIN_ACTION_LIMITS[key].append(now)
    return True

async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message and update.message.reply_to_message:
        return update.message.reply_to_message.from_user
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
            except Exception:
                pass
    return None

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Hata: {context.error}")

# --- TELEGRAM KOMUTLARI ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = database.get_group_settings(chat_id)
    welcome_text = (
        "🤖 *Grup Yönetim & Golden Staff Güvenlik Botu*\n\n"
        f"{settings['welcome_msg']}\n\n"
        "📜 *Yönetici Komutları:*\n"
        "- `/welcome <mesaj>` - Hoş geldin mesajını değiştirir.\n"
        "- `/butonmetni <yazı>` - Varsayılan buton metnini değiştirir.\n"
        "- `/butonlinki <url>` - Varsayılan buton linkini değiştirir.\n"
        "- `/duyuru <mesaj>` - Duyuru gönderir.\n"
        "- `/duyuru <mesaj> | <buton metni> | <link>` - Özel butonlu duyuru gönderir.\n"
        "- `/zamanla <dakika>` - Periyodik paylaşım başlatır.\n"
        "- `/zamanladur` - Periyodik paylaşımı durdurur.\n"
        "- `/kilit kapat / ac` - Sohbeti kilitler/açar.\n"
        "- `/defol <kullanıcı>` - Kullanıcıyı banlar.\n"
        "- `/undefol <kullanıcı>` - Banı kaldırır.\n"
        "- `/itaat <kullanıcı>` - Susturur.\n"
        "- `/unitaat <kullanıcı>` - Susturmayı kaldırır.\n"
        "- `/warn <kullanıcı>` - Uyarı verir (3 uyarı = Ban).\n"
        "- `/unwarn <kullanıcı>` - Uyarıları sıfırlar.\n\n"
        "Aşağıdaki butona dokunarak **Golden Staff Paneli**'ne giriş yapabilirsin! 👇"
    )
    
    web_app_url = "https://golden-guardian-bot.onrender.com/"
    keyboard = [[InlineKeyboardButton(text="🛡️ Golden Staff Paneli", web_app=WebAppInfo(url=web_app_url))]]
    
    await update.message.reply_text(
        welcome_text, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    text = " ".join(context.args)
    if not text: return
    database.update_group_setting(chat_id, "welcome_msg", text)
    await update.message.reply_text("✅ Hoş geldin mesajı güncellendi!")

async def cmd_butonmetni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    text = " ".join(context.args)
    if not text: return
    database.update_group_setting(chat_id, "btn_text", text)
    await update.message.reply_text("✅ Varsayılan buton metni güncellendi!")

async def cmd_butonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    if not context.args: return
    url = context.args[0].strip()
    database.update_group_setting(chat_id, "btn_url", url)
    await update.message.reply_text("✅ Varsayılan buton linki güncellendi!")

async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    full_text = " ".join(context.args)
    if not full_text: return
    
    settings = database.get_group_settings(chat_id)
    
    # Orijinal boru formatı: /duyuru <mesaj> | <buton metni> | <link>
    if "|" in full_text:
        parts = [p.strip() for p in full_text.split("|")]
        msg_text = parts[0]
        btn_text = parts[1] if len(parts) > 1 else settings["btn_text"]
        btn_url = parts[2] if len(parts) > 2 else settings["btn_url"]
    else:
        msg_text = full_text
        btn_text = settings["btn_text"]
        btn_url = settings["btn_url"]

    keyboard = [[InlineKeyboardButton(text=btn_text, url=btn_url)]]
    msg = await context.bot.send_message(chat_id=chat_id, text=f"📢 **DUYURU**\n\n{msg_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass

async def periodic_broadcast(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    text = job.data
    settings = database.get_group_settings(chat_id)
    keyboard = [[InlineKeyboardButton(text=settings["btn_text"], url=settings["btn_url"])]]
    await context.bot.send_message(chat_id=chat_id, text=f"⏰ **Periyodik Paylaşım**\n\n{text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def cmd_zamanla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: /zamanla <dakika>")
        return
    minutes = int(context.args[0])
    reply = update.message.reply_to_message
    text = reply.text if reply and reply.text else "Periyodik bilgilendirme metni."
    
    if chat_id in SCHEDULED_TASKS:
        SCHEDULED_TASKS[chat_id].schedule_removal()
    
    job = context.job_queue.run_repeating(periodic_broadcast, interval=minutes * 60, first=minutes * 60, chat_id=chat_id, data=text)
    SCHEDULED_TASKS[chat_id] = job
    await update.message.reply_text(f"✅ Periyodik paylaşım her {minutes} dakikada bir olacak şekilde başlatıldı!")

async def cmd_zamanladur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    if chat_id in SCHEDULED_TASKS:
        SCHEDULED_TASKS[chat_id].schedule_removal()
        del SCHEDULED_TASKS[chat_id]
        await update.message.reply_text("⏹️ Periyodik paylaşım durduruldu.")
    else:
        await update.message.reply_text("Aktif bir periyodik paylaşım bulunmuyor.")

async def cmd_kilit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    if not context.args: return
    action = context.args[0].lower()
    try:
        if action == "kapat":
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=ChatPermissions(can_send_messages=False))
            await update.message.reply_text("🔒 Sohbet kilitlendi.")
            database.log_action(chat_id, "LOCK_CHAT", 0, "admin", "Sohbet kilitlendi.")
        elif action == "ac":
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True))
            await update.message.reply_text("🔓 Sohbetin kilidi açıldı.")
            database.log_action(chat_id, "UNLOCK_CHAT", 0, "admin", "Sohbetin kilidi açıldı.")
    except Exception as e:
        logger.error(f"Kilit hatası: {e}")

async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    await safe_delete_user_message(update)

    if not await is_user_admin(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "❌ Ban yetkiniz yok veya askıya alındı.", parse_mode=None)
        return

    if not await check_admin_rate_limit(chat_id, admin_id, context):
        await send_auto_delete_message(context, chat_id, "⚠️ Güvenlik Sınırı: Çok hızlı işlem yaptığınız için ban yetkiniz askıya alındı.", delay=10)
        return

    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "❌ Kullanıcı bulunamadı.", parse_mode=None)
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        safe_name = escape_markdown(user.full_name, version=1)
        await send_auto_delete_message(context, chat_id, f"🚫 *{safe_name}* engellendi.", parse_mode="Markdown")
        database.log_action(chat_id, "BAN", user.id, user.username or str(user.id), "Admin tarafından banlandı.")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")

async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    user = await get_target_user(update, context)
    if not user: return
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        await send_auto_delete_message(context, chat_id, f"✅ *{escape_markdown(user.full_name, version=1)}* engeli kaldırıldı.")
        database.log_action(chat_id, "UNBAN", user.id, user.username or str(user.id), "Engeli kaldırıldı.")
    except Exception as e:
        logger.error(f"Unban hatası: {e}")

async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    user = await get_target_user(update, context)
    if not user: return
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False))
        await send_auto_delete_message(context, chat_id, f"🔇 *{escape_markdown(user.full_name, version=1)}* susturuldu.")
        database.log_action(chat_id, "MUTE", user.id, user.username or str(user.id), "Susturuldu.")
    except Exception as e:
        logger.error(f"Mute hatası: {e}")

async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    user = await get_target_user(update, context)
    if not user: return
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True))
        await send_auto_delete_message(context, chat_id, f"🔊 *{escape_markdown(user.full_name, version=1)}* susturması kaldırıldı.")
        database.log_action(chat_id, "UNMUTE", user.id, user.username or str(user.id), "Susturması kaldırıldı.")
    except Exception as e:
        logger.error(f"Unmute hatası: {e}")

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    user = await get_target_user(update, context)
    if not user: return
    current_warns = database.add_warning(user.id)
    database.log_action(chat_id, "WARN", user.id, user.username or str(user.id), f"Uyarı: {current_warns}/3")
    if current_warns >= 3:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        database.log_action(chat_id, "AUTO_BAN_WARN", user.id, user.username or str(user.id), "3 uyarı sınırından banlandı.")
        await send_auto_delete_message(context, chat_id, f"⚠️ *{escape_markdown(user.full_name, version=1)}* 3 uyarı sınırına ulaştığı için banlandı!")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ *{escape_markdown(user.full_name, version=1)}* uyarıldı ({current_warns}/3).")

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await is_user_admin(chat_id, admin_id, context): return
    user = await get_target_user(update, context)
    if not user: return
    database.reset_warnings(user.id)
    database.log_action(chat_id, "UNWARN", user.id, user.username or str(user.id), "Uyarılar sıfırlandı.")
    await send_auto_delete_message(context, chat_id, f"♻️ *{escape_markdown(user.full_name, version=1)}* uyarıları sıfırlandı.")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = update.message.text.lower()
    if user and user.username:
        database.save_user(chat_id, user.id, user.username)

    # Otomatik Küfür ve Argo Filtresi
    if any(word in text for word in BAD_WORDS):
        try:
            await update.message.delete()
            database.log_action(chat_id, "FILTER_DELETE", user.id, user.username or str(user.id), "Küfür/argo mesaj silindi.")
        except Exception:
            pass

async def post_init(application):
    database.init_db()
    
    commands = [
        ("start", "🛡️ Paneli ve Komutları Listele"),
        ("welcome", "👋 Hoş geldin mesajını değiştir"),
        ("butonmetni", "📝 Buton metnini değiştir"),
        ("butonlinki", "🔗 Buton linkini değiştir"),
        ("duyuru", "📢 Duyuru gönder"),
        ("zamanla", "⏰ Periyodik paylaşım başlat"),
        ("zamanladur", "⏹️ Periyodik paylaşımı durdur"),
        ("kilit", "🔒 Sohbeti kilitle/aç"),
        ("defol", "🚫 Kullanıcıyı banla"),
        ("undefol", "✅ Banı kaldır"),
        ("itaat", "🔇 Kullanıcıyı sustur"),
        ("unitaat", "🔊 Susturmayı kaldır"),
        ("warn", "⚠️ Uyarı ver"),
        ("unwarn", "♻️ Uyarıları sıfırla")
    ]
    try:
        await application.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc in commands])
    except Exception as e:
        logger.error(f"Komutlar ayarlanamadı: {e}")

    asyncio.create_task(start_web_server(application))
    logger.info("Veritabanı kuruldu, Web Sunucusu ve API'ler başlatıldı.")

def main():
    request_config = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = ApplicationBuilder().token(config.BOT_TOKEN).request(request_config).post_init(post_init).build()

    app.add_error_handler(global_error_handler)
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("welcome", cmd_welcome))
    app.add_handler(CommandHandler("butonmetni", cmd_butonmetni))
    app.add_handler(CommandHandler("butonlinki", cmd_butonlinki))
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
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

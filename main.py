import os
import json
import asyncio
import logging
import time
import hashlib
from aiohttp import web
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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

async def api_get_chat_messages(request):
    try:
        messages = database.get_chat_messages(limit=50)
        return web.json_response(messages)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_post_chat_message(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        username = data.get("username", "bilinmiyor")
        full_name = data.get("full_name", "Admin")
        message = data.get("message", "")
        msg_type = data.get("msg_type", "text")

        if not message:
            return web.json_response({"success": False, "error": "Boş mesaj olamaz"}, status=400)

        founder_id = os.environ.get("FOUNDER_ID")
        if not founder_id or str(user_id) != str(founder_id).strip():
            perms = database.get_staff_permissions(user_id)
            if perms["can_write"] == 0 or perms["is_muted"] == 1:
                return web.json_response({"success": False, "error": "Kurucu tarafından mesaj yazmanız engellendi!"}, status=403)

        database.save_chat_message(user_id, username, full_name, message, msg_type)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

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
        result = [{"user_id": s[0], "username": s[1], "full_name": s[2], "can_ban": s[3], "can_speak": s[4], "can_write": s[5], "is_muted": s[6]} for s in staffs]
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_set_staff_permission(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        target_id = int(data.get("target_id", 0))
        perm_type = data.get("perm_type")
        status = int(data.get("status", 0))

        founder_id = os.environ.get("FOUNDER_ID")
        if not founder_id or str(user_id) != str(founder_id).strip():
            return web.json_response({"success": False, "error": "Yetkisiz işlem!"}, status=403)

        database.set_staff_permission(target_id, perm_type, status)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

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

        target_id = 0
        target_username = ""

        if input_value.isdigit() or (input_value.startswith("-") and input_value[1:].isdigit()):
            target_id = int(input_value)
            target_username = "id_" + input_value
        else:
            clean_username = input_value.lstrip("@").lower()
            found_id = database.get_user_id_by_username(0, clean_username)
            if found_id:
                target_id = found_id
                target_username = clean_username
            else:
                target_id = int(hashlib.md5(clean_username.encode()).hexdigest()[:8], 16)
                target_username = clean_username

        database.add_staff_member(target_id, target_username, full_name, user_id)
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
    app.router.add_get("/api/chat", api_get_chat_messages)
    app.router.add_post("/api/chat", api_post_chat_message)
    app.router.add_get("/api/lock-status", api_get_lock_status)
    app.router.add_post("/api/toggle-lockdown", api_toggle_lockdown)
    app.router.add_post("/api/verify-admin", api_verify_admin)
    app.router.add_post("/api/staff/list", api_get_staff_list)
    app.router.add_post("/api/staff/permission", api_set_staff_permission)
    app.router.add_post("/api/staff/add", api_add_staff)
    app.router.add_post("/api/staff/remove", api_remove_staff)
    
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

# --- START KOMUTU VE MENÜ ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 *Grup Yönetim & Golden Staff Güvenlik Botu*\n\n"
        "Grup düzenini sağlamak, argo/küfür, dış linkler ve yasaklı içerikleri otomatik engellemek için aktifim.\n\n"
        "📜 *Yönetici Komutları:*\n"
        "- `/welcome <mesaj>` - Hoş geldin mesajını değiştirir.\n"
        "- `/butonmetni <yazı>` - Varsayılan buton metnini değiştirir.\n"
        "- `/butonlinki <url>` - Varsayılan buton linkini değiştirir.\n"
        "- `/duyuru <mesaj>` - Duyuru gönderir.\n"
        "- `/zamanla <dakika>` - Periyodik paylaşım başlatır.\n"
        "- `/zamanladur` - Periyodik paylaşımı durdurur.\n"
        "- `/kilit kapat / ac` - Sohbeti kilitler/açar.\n"
        "- `/defol <kullanıcı>` - Kullanıcıyı banlar.\n"
        "- `/undefol <kullanıcı>` - Banı kaldırır.\n"
        "- `/itaat <kullanıcı>` - Susturur.\n"
        "- `/unitaat <kullanıcı>` - Susturmayı kaldırır.\n"
        "- `/warn <kullanıcı>` - Uyarı verir.\n"
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
        database.log_action(chat_id, "BAN", user.id, user.username, "Admin tarafından banlandı.")
    except Exception as e:
        logger.error(f"Ban Hatası: {e}")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    if user and user.username:
        database.save_user(chat_id, user.id, user.username)

async def post_init(application):
    database.init_db()
    
    # Telegram menü komutlarını otomatik kaydet
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
    app.add_handler(CommandHandler("defol", cmd_defol))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

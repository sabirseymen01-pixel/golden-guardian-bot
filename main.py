import os
import json
import asyncio
import logging
import time
import hashlib
from aiohttp import web
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.request import HTTPXRequest
from telegram.error import TelegramError, Conflict
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
try:
    import config
except ImportError:
    config = None

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

ADMIN_ACTION_LIMITS = {}
MAX_ACTIONS_PER_MINUTE = 15
EMERGENCY_LOCKDOWN = False
BAD_WORDS = ["küfür1", "küfür2", "argo1"]
SCHEDULED_TASKS = {}
scheduler = AsyncIOScheduler()

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        return
    logger.error(f"Telegram Bot Hatası: {context.error}")

# --- API ROTALARI ---
async def api_get_stats(request):
    return web.json_response(database.get_bot_statistics())

async def api_get_logs(request):
    try:
        log_filter = request.query.get("filter", "all")
        limit = int(request.query.get("limit", 100))
        logs = database.get_recent_logs(limit=limit, log_filter=log_filter)
        return web.json_response(logs)
    except Exception as e:
        return web.json_response([], status=500)

async def api_clear_logs(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        days = int(data.get("days", 0))
        if not database.is_authorized_staff(user_id):
            founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
            if not founder_id or str(user_id) != str(founder_id).strip():
                return web.json_response({"success": False, "error": "Yetkisiz!"}, status=403)
        database.clear_logs_by_retention(days)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def api_get_admin_stats(request):
    return web.json_response(database.get_admin_performance_stats())

async def api_get_data_cloud(request):
    return web.json_response(database.get_data_cloud_tags())

async def api_get_lock_status(request):
    return web.json_response({"lockdown": EMERGENCY_LOCKDOWN})

async def api_dummy_chat(request):
    return web.json_response([])

async def api_get_groups(request):
    return web.json_response(database.get_bot_groups())

async def api_toggle_lockdown(request):
    global EMERGENCY_LOCKDOWN
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) != str(founder_id).strip():
        return web.json_response({"success": False, "error": "Yetkisiz!"}, status=403)
    EMERGENCY_LOCKDOWN = not EMERGENCY_LOCKDOWN
    return web.json_response({"success": True, "lockdown": EMERGENCY_LOCKDOWN})

async def api_verify_admin(request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) == str(founder_id).strip():
        return web.json_response({"authorized": True, "is_founder": True})
    if database.is_authorized_staff(user_id):
        return web.json_response({"authorized": True, "is_founder": False, "permissions": database.get_staff_permissions(user_id)})
    if not founder_id:
        return web.json_response({"authorized": True, "is_founder": True})
    return web.json_response({"authorized": False})

async def api_get_staff_list(request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not database.is_authorized_staff(user_id):
        founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
        if not founder_id or str(user_id) != str(founder_id).strip():
            return web.json_response({"success": False, "error": "Yetkisiz!"}, status=403)
    return web.json_response(database.get_all_staff())

async def api_add_staff(request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    input_value = str(data.get("staff_input", "")).strip()
    full_name = data.get("full_name", "Yetkili")
    if not input_value: return web.json_response({"success": False, "error": "Boş değer"}, status=400)
    clean_val = input_value.lstrip("@").lower()
    target_id = int(clean_val) if clean_val.isdigit() or (clean_val.startswith("-") and clean_val[1:].isdigit()) else (database.get_user_id_by_username(0, clean_val) or int(hashlib.md5(clean_val.encode()).hexdigest()[:8], 16))
    database.add_staff_member(target_id, clean_val, full_name, user_id)
    return web.json_response({"success": True})

async def api_toggle_staff_perm(request):
    data = await request.json()
    target_id = int(data.get("target_id", 0))
    perm_field = str(data.get("perm", ""))
    if perm_field in ["can_ban", "can_mute"]:
        new_val = database.toggle_staff_permission(target_id, perm_field)
        return web.json_response({"success": True, "new_val": new_val})
    return web.json_response({"success": False}, status=400)

async def api_remove_staff(request):
    data = await request.json()
    target_id = int(data.get("target_id", 0))
    database.remove_staff_member(target_id)
    return web.json_response({"success": True})

async def api_member_action(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        target_input = str(data.get("target", "")).strip()
        action = str(data.get("action", "")).strip()
        chat_id = int(data.get("chat_id", 0))
        
        founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
        is_founder = founder_id and str(user_id) == str(founder_id).strip()
        if not is_founder and not database.is_authorized_staff(user_id):
            return web.json_response({"success": False, "error": "Yetkisiz!"}, status=403)

        bot = request.app['bot']
        clean_target = target_input.lstrip("@").lower()
        target_id = int(clean_target) if clean_target.isdigit() or (clean_target.startswith("-") and clean_target[1:].isdigit()) else database.get_user_id_by_username(chat_id, clean_target)
        if not target_id: return web.json_response({"success": False, "error": "Kullanıcı bulunamadı!"}, status=404)

        if chat_id:
            if action == "ban":
                await bot.ban_chat_member(chat_id=chat_id, user_id=target_id)
                database.log_action(chat_id, "BAN", target_id, clean_target, f"Üye Portundan banlandı (ID: {target_id})")
            elif action == "unban":
                await bot.unban_chat_member(chat_id=chat_id, user_id=target_id, only_if_banned=True)
                database.log_action(chat_id, "UNBAN", target_id, clean_target, f"Üye Portundan banı kaldırıldı (ID: {target_id})")
            elif action == "mute":
                await bot.restrict_chat_member(chat_id=chat_id, user_id=target_id, permissions=ChatPermissions(can_send_messages=False))
                database.log_action(chat_id, "MUTE", target_id, clean_target, f"Üye Portundan susturuldu (ID: {target_id})")
            elif action == "unmute":
                perms = ChatPermissions(can_send_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
                await bot.restrict_chat_member(chat_id=chat_id, user_id=target_id, permissions=perms)
                database.log_action(chat_id, "UNMUTE", target_id, clean_target, f"Üye Portundan susturması kaldırıldı (ID: {target_id})")
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def api_update_settings(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        chat_id = int(data.get("chat_id", 0))
        if not database.is_authorized_staff(user_id): return web.json_response({"success": False, "error": "Yetkisiz!"}, status=403)
        if chat_id:
            for key in ["welcome_msg", "btn_text", "btn_url", "media_url"]:
                if key in data: database.update_group_setting(chat_id, key, data[key])
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def start_web_server(app_context):
    app = web.Application()
    app['bot'] = app_context.bot
    app.router.add_get("/api/stats", api_get_stats)
    app.router.add_get("/api/logs", api_get_logs)
    app.router.add_post("/api/logs/clear", api_clear_logs)
    app.router.add_get("/api/admin-stats", api_get_admin_stats)
    app.router.add_get("/api/data-cloud", api_get_data_cloud)
    app.router.add_get("/api/lock-status", api_get_lock_status)
    app.router.add_get("/api/chat", api_dummy_chat)
    app.router.add_post("/api/chat", api_dummy_chat)
    app.router.add_get("/api/groups", api_get_groups)
    app.router.add_post("/api/toggle-lockdown", api_toggle_lockdown)
    app.router.add_post("/api/verify-admin", api_verify_admin)
    app.router.add_post("/api/staff/list", api_get_staff_list)
    app.router.add_post("/api/staff/add", api_add_staff)
    app.router.add_post("/api/staff/remove", api_remove_staff)
    app.router.add_post("/api/staff/toggle", api_toggle_staff_perm)
    app.router.add_post("/api/member/action", api_member_action)
    app.router.add_post("/api/settings/update", api_update_settings)
    
    async def handle_root(request):
        try:
            with open("public/index.html", "r", encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
        except Exception as e:
            return web.Response(text=f"Index okunamadı: {str(e)}", status=500)

    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def _delete_after_delay(context, chat_id, message_id, delay=5):
    await asyncio.sleep(delay)
    try: await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception: pass

async def send_auto_delete_message(context, chat_id, text, delay=5):
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        asyncio.create_task(_delete_after_delay(context, chat_id, msg.message_id, delay))
    except Exception as e: logger.error(f"Mesaj hatası: {e}")

async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user: return False
    founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
    if founder_id and str(user.id) == str(founder_id).strip(): return True
    if database.is_authorized_staff(user.id): return True
    if update.effective_chat.type in ["group", "supergroup"]:
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            if member.status in ["administrator", "creator"]: return True
        except Exception: pass
    else: return True
    await send_auto_delete_message(context, chat_id, "❌ Bu komutu kullanmak için yetkiniz yok!")
    return False

async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        if target and target.username: database.save_user(chat_id, target.id, target.username.lower())
        return target
    if context.args:
        clean_arg = context.args[0].strip().lstrip("@").lower()
        if clean_arg.isdigit() or (clean_arg.startswith("-") and clean_arg[1:].isdigit()):
            try:
                member = await context.bot.get_chat_member(chat_id, int(clean_arg))
                return member.user
            except Exception:
                class TempUser:
                    def __init__(self, uid): self.id, self.full_name, self.username = uid, f"Kullanıcı_{uid}", f"id_{uid}"
                return TempUser(int(clean_arg))
        else:
            db_uid = database.get_user_id_by_username(chat_id, clean_arg) or database.get_user_id_by_username(0, clean_arg)
            if db_uid:
                try: return (await context.bot.get_chat_member(chat_id, db_uid)).user
                except Exception: pass
            class UsernameTarget:
                def __init__(self, uname): self.id, self.full_name, self.username = f"@{uname}", f"@{uname}", uname
            return UsernameTarget(clean_arg)
    return None

# --- PERİYODİK ZAMANLI DUYURU FONKSİYONU ---
async def send_periodic_announcement(bot, chat_id, text, btn_text, btn_url, media_url):
    try:
        if not btn_url.startswith(("http://", "https://", "tg://")):
            btn_url = "https://" + btn_url
        keyboard = [[InlineKeyboardButton(text=btn_text, url=btn_url)]]
        if media_url:
            await bot.send_photo(chat_id=chat_id, photo=media_url, caption=text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Zamanlı duyuru gönderme hatası: {e}")

# --- TELEGRAM KOMUTLARI ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        database.register_bot_group(chat.id, chat.title or "Grup")
        try: await update.message.delete()
        except Exception: pass
        return

    welcome_text = (
        "🤖 **Grup Yönetim & Golden Staff Güvenlik Botu**\n\n"
        "📜 **Yönetici Komutları:**\n"
        "• `/welcome <mesaj>` - Hoş geldin mesajı\n"
        "• `/butonmetni <yazı>` - Buton yazısı\n"
        "• `/butonlinki <url>` - Buton linki\n"
        "• `/duyuru <mesaj>` - Duyuru gönder\n"
        "• `/zamanla <dakika> | <mesaj> | <buton> | <link>` - Periyodik duyuru başlat\n"
        "• `/zamanladur <dakika> | <mesaj> | <buton> | <link>` - Periyodik duyuruyu durdur\n"
        "• `/defol <kullanıcı>` - Banla\n"
        "• `/undefol <kullanıcı>` - Ban kaldır\n"
        "• `/itaat <kullanıcı>` - Sustur\n"
        "• `/unitaat <kullanıcı>` - Susturma kaldır\n"
        "• `/warn <kullanıcı>` - Uyarı ver\n"
        "• `/unwarn <kullanıcı>` - Uyarı sıfırla\n\n"
        "⚜️ *Powered by 𝘽𝙍𝙀𝙎𝙆𝘼𝙑𝙄𝘾𝘼 🇷🇸*"
    )
    web_app_url = "https://golden-guardian-bot.onrender.com/"
    keyboard = [[InlineKeyboardButton(text="🛡️ Paneli Aç", web_app=WebAppInfo(url=web_app_url))]]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    text = " ".join(context.args)
    if text: 
        database.update_group_setting(update.effective_chat.id, "welcome_msg", text)
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Hoş geldin mesajı güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_butonmetni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    text = " ".join(context.args)
    if text: 
        database.update_group_setting(update.effective_chat.id, "btn_text", text)
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Buton metni güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_butonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    if context.args: 
        database.update_group_setting(update.effective_chat.id, "btn_url", context.args[0].strip())
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Buton linki güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_zamanla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    raw_text = update.message.text.partition(" ")[2].strip()
    if not raw_text and reply: raw_text = reply.text or reply.caption or ""
    
    if "|" not in raw_text:
        await send_auto_delete_message(context, chat_id, "❌ Format: /zamanla <dakika> | <mesaj> | <buton> | <link>", 6)
        try: await update.message.delete()
        except Exception: pass
        return
        
    parts = [p.strip() for p in raw_text.split("|")]
    try:
        minutes = int(parts[0])
    except ValueError:
        await send_auto_delete_message(context, chat_id, "❌ Dakika sayısal olmalıdır!", 5)
        try: await update.message.delete()
        except Exception: pass
        return
        
    msg_text = parts[1] if len(parts) > 1 else ""
    settings = database.get_group_settings(chat_id)
    btn_text = parts[2] if len(parts) > 2 and parts[2] else settings["btn_text"]
    btn_url = parts[3] if len(parts) > 3 and parts[3] else settings["btn_url"]
    media_url = reply.photo[-1].file_id if (reply and reply.photo) else settings.get("media_url", "")
    
    # Uniquely identify the scheduled job based on chat and exact text content
    job_id = hashlib.md5(f"{chat_id}_{raw_text}".encode()).hexdigest()
    
    scheduler.add_job(
        send_periodic_announcement,
        'interval',
        minutes=minutes,
        args=[context.bot, chat_id, msg_text, btn_text, btn_url, media_url],
        id=job_id,
        replace_existing=True
    )
    
    database.log_action(chat_id, "SCHEDULE_START", 0, "SYSTEM", f"Her {minutes} dakikada bir otomatik duyuru başlatıldı.")
    await send_auto_delete_message(context, chat_id, f"✅ Her {minutes} dakikada bir tekrarlanacak duyuru başlatıldı!", 5)
    try: await update.message.delete()
    except Exception: pass

async def cmd_zamanladur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    raw_text = update.message.text.partition(" ")[2].strip()
    
    if not raw_text:
        await send_auto_delete_message(context, chat_id, "❌ Durdurulacak duyuruyu belirtin! Örnek: /zamanladur 30 | Mesaj | Buton | Link", 6)
        try: await update.message.delete()
        except Exception: pass
        return
        
    job_id = hashlib.md5(f"{chat_id}_{raw_text}".encode()).hexdigest()
    try:
        scheduler.remove_job(job_id)
        await send_auto_delete_message(context, chat_id, "🛑 Belirtilen periyodik duyuru durduruldu!", 5)
        database.log_action(chat_id, "SCHEDULE_STOP", 0, "SYSTEM", "Periyodik duyuru durduruldu.")
    except Exception:
        await send_auto_delete_message(context, chat_id, "❌ Eşleşen aktif periyodik duyuru bulunamadı!", 5)
        
    try: await update.message.delete()
    except Exception: pass

async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    raw_text = update.message.text.partition(" ")[2].strip()
    if not raw_text and reply: raw_text = reply.text or reply.caption or ""
    if not raw_text: return
        
    settings = database.get_group_settings(chat_id)
    msg_text, btn_text, btn_url, media_url = raw_text, settings["btn_text"], settings["btn_url"], settings.get("media_url", "")
    if "|" in raw_text:
        parts = [p.strip() for p in raw_text.split("|")]
        msg_text = parts[0]
        if len(parts) > 1 and parts[1]: btn_text = parts[1]
        if len(parts) > 2 and parts[2]: btn_url = parts[2]
    if not btn_url.startswith(("http://", "https://", "tg://")): btn_url = "https://" + btn_url

    keyboard = [[InlineKeyboardButton(text=btn_text, url=btn_url)]]
    try:
        photo_to_send = media_url if media_url else (reply.photo[-1].file_id if (reply and reply.photo) else None)
        if photo_to_send:
            msg = await context.bot.send_photo(chat_id=chat_id, photo=photo_to_send, caption=msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
        try: await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception: pass
    except Exception as e: logger.error(f"Duyuru hatası: {e}")
    try: await update.message.delete()
    except Exception: pass

async def cmd_kilit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    if context.args:
        action = context.args[0].lower()
        try:
            if action in ["kapat", "kilit"]:
                await context.bot.set_chat_permissions(chat_id=chat_id, permissions=ChatPermissions(can_send_messages=False))
                await send_auto_delete_message(context, chat_id, "🔒 Sohbet kilitlendi!", 4)
            elif action in ["ac", "aç"]:
                await context.bot.set_chat_permissions(chat_id=chat_id, permissions=ChatPermissions(can_send_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True))
                await send_auto_delete_message(context, chat_id, "🔓 Sohbet kilidi açıldı!", 4)
        except Exception as e: logger.error(f"Kilit hatası: {e}")
    try: await update.message.delete()
    except Exception: pass

async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            uname = getattr(user, 'username', str(user.id))
            admin_uname = update.effective_user.username or update.effective_user.first_name
            database.log_action(chat_id, "BAN", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} tarafından banlandı.")
            await send_auto_delete_message(context, chat_id, f"🚫 @{uname} gruptan banlandı!", 5)
        except Exception as e: logger.error(f"Ban hatası: {e}")
    try: await update.message.delete()
    except Exception: pass

async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
            uname = getattr(user, 'username', str(user.id))
            admin_uname = update.effective_user.username or update.effective_user.first_name
            database.log_action(chat_id, "UNBAN", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} tarafından banı kaldırıldı.")
            await send_auto_delete_message(context, chat_id, f"✅ @{uname} kullanıcısının banı kaldırıldı!", 5)
        except Exception as e: logger.error(f"Unban hatası: {e}")
    try: await update.message.delete()
    except Exception: pass

async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False))
            uname = getattr(user, 'username', str(user.id))
            admin_uname = update.effective_user.username or update.effective_user.first_name
            database.log_action(chat_id, "MUTE", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} tarafından susturuldu.")
            await send_auto_delete_message(context, chat_id, f"🔇 @{uname} susturuldu!", 5)
        except Exception as e: logger.error(f"Mute hatası: {e}")
    try: await update.message.delete()
    except Exception: pass

async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            perms = ChatPermissions(can_send_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
            await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=perms)
            uname = getattr(user, 'username', str(user.id))
            admin_uname = update.effective_user.username or update.effective_user.first_name
            database.log_action(chat_id, "UNMUTE", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} tarafından susturması kaldırıldı.")
            await send_auto_delete_message(context, chat_id, f"🔊 @{uname} kullanıcısının susturması kaldırıldı!", 5)
        except Exception as e: logger.error(f"Unmute hatası: {e}")
    try: await update.message.delete()
    except Exception: pass

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        uname = getattr(user, 'username', str(user.id))
        admin_uname = update.effective_user.username or update.effective_user.first_name
        current_warns = database.add_warning(user.id)
        database.log_action(chat_id, "WARN", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} uyarı verdi ({current_warns}/3).")
        if current_warns >= 3:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            database.reset_warnings(user.id)
            await send_auto_delete_message(context, chat_id, f"⚠️ @{uname} 3 uyarıya ulaştığı için banlandı!", 5)
        else:
            await send_auto_delete_message(context, chat_id, f"⚠️ @{uname} uyarlandı ({current_warns}/3).", 5)
    try: await update.message.delete()
    except Exception: pass

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user: 
        database.reset_warnings(user.id)
        await send_auto_delete_message(context, chat_id, "✅ Kullanıcının uyarıları sıfırlandı!", 5)
    try: await update.message.delete()
    except Exception: pass

COMMAND_MAP = {
    "start": cmd_start, "help": cmd_start, "welcome": cmd_welcome,
    "butonmetni": cmd_butonmetni, "butonlinki": cmd_butonlinki, "duyuru": cmd_duyuru,
    "zamanla": cmd_zamanla, "zamanladur": cmd_zamanladur, "kilit": cmd_kilit,
    "defol": cmd_defol, "undefol": cmd_undefol, "itaat": cmd_itaat,
    "unitaat": cmd_unitaat, "warn": cmd_warn, "unwarn": cmd_unwarn,
}

async def master_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user, chat, chat_id = update.effective_user, update.effective_chat, update.effective_chat.id
    if chat.type in ["group", "supergroup"]: database.register_bot_group(chat_id, chat.title or "Grup")
    if user and user.username: database.save_user(chat_id, user.id, user.username.lower())

    text = (update.message.text or update.message.caption or "").strip()
    if chat.type in ["group", "supergroup"] and text:
        is_admin = False
        if user:
            try:
                member = await context.bot.get_chat_member(chat_id, user.id)
                if member.status in ["administrator", "creator"] or database.is_authorized_staff(user.id): is_admin = True
            except Exception: pass
        if not is_admin:
            lower_text = text.lower()
            has_link = any(p in lower_text for p in ["http://", "https://", "t.me/", "telegram.me/", "www.", "joinchat"])
            has_bad_words = any(w in lower_text for w in BAD_WORDS)
            if has_link or has_bad_words:
                try:
                    await update.message.delete()
                    if user:
                        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                        uname_str = f"@{user.username}" if user.username else str(user.id)
                        database.log_action(chat_id, "AUTO_BAN", user.id, f"{uname_str} (ID: {user.id})", f"Yasaklı içerik paylaştığı için otomatik banlandı.")
                except Exception as e: logger.error(f"Filtre hatası: {e}")

async def post_init(application):
    database.init_db()
    if not scheduler.running:
        scheduler.start()
    await application.bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_web_server(application))

def main():
    token = os.environ.get("BOT_TOKEN") or (config and getattr(config, "BOT_TOKEN", None))
    if not token: return
    app = ApplicationBuilder().token(token).request(HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)).post_init(post_init).build()
    app.add_error_handler(global_error_handler)
    
    for cmd, func in COMMAND_MAP.items():
        app.add_handler(CommandHandler(cmd, func))
        
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, master_message_router))
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

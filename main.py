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
            
    staffs = database.get_all_staff()
    return web.json_response(staffs)

async def api_add_staff(request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    input_value = str(data.get("staff_input", "")).strip()
    full_name = data.get("full_name", "Yetkili")
    if not input_value:
        return web.json_response({"success": False, "error": "Boş değer"}, status=400)
    
    clean_val = input_value.lstrip("@").lower()
    if clean_val.isdigit() or (clean_val.startswith("-") and clean_val[1:].isdigit()):
        target_id = int(clean_val)
    else:
        found_id = database.get_user_id_by_username(0, clean_val)
        target_id = found_id if found_id else int(hashlib.md5(clean_val.encode()).hexdigest()[:8], 16)
        
    database.add_staff_member(target_id, clean_val, full_name, user_id)
    return web.json_response({"success": True})

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
        
        if not database.is_authorized_staff(user_id):
            founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
            if not founder_id or str(user_id) != str(founder_id).strip():
                return web.json_response({"success": False, "error": "Yetkisiz işlem!"}, status=403)

        bot = request.app['bot']
        clean_target = target_input.lstrip("@").lower()
        if clean_target.isdigit() or (clean_target.startswith("-") and clean_target[1:].isdigit()):
            target_id = int(clean_target)
        else:
            target_id = database.get_user_id_by_username(chat_id, clean_target)
            if not target_id:
                return web.json_response({"success": False, "error": "Kullanıcı veritabanında bulunamadı!"}, status=404)

        if chat_id:
            if action == "ban":
                await bot.ban_chat_member(chat_id=chat_id, user_id=target_id)
                database.log_action(chat_id, "BAN", target_id, clean_target, "Web panelinden üye banlandı.")
            elif action == "mute":
                await bot.restrict_chat_member(chat_id=chat_id, user_id=target_id, permissions=ChatPermissions(can_send_messages=False))
                database.log_action(chat_id, "MUTE", target_id, clean_target, "Web panelinden üye susturuldu.")

        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def api_update_settings(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        chat_id = int(data.get("chat_id", 0))
        if not database.is_authorized_staff(user_id):
            return web.json_response({"success": False, "error": "Yetkisiz!"}, status=403)

        if chat_id:
            if "welcome_msg" in data:
                database.update_group_setting(chat_id, "welcome_msg", data["welcome_msg"])
            if "btn_text" in data:
                database.update_group_setting(chat_id, "btn_text", data["btn_text"])
            if "btn_url" in data:
                database.update_group_setting(chat_id, "btn_url", data["btn_url"])
            if "media_url" in data:
                database.update_group_setting(chat_id, "media_url", data["media_url"])

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

async def _delete_after_delay(context, chat_id, message_id, delay=10):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def send_auto_delete_message(context, chat_id, text, delay=10):
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        asyncio.create_task(_delete_after_delay(context, chat_id, msg.message_id, delay))
    except Exception as e:
        logger.error(f"Mesaj gönderme hatası: {e}")

async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user: return False
    user_id = user.id

    if update.message and update.message.sender_chat and update.message.sender_chat.id == chat_id:
        return True

    founder_id = (config and getattr(config, "FOUNDER_ID", None)) or os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) == str(founder_id).strip():
        return True

    if database.is_authorized_staff(user_id):
        return True
    if user.username:
        hash_id = int(hashlib.md5(user.username.lower().encode()).hexdigest()[:8], 16)
        if database.is_authorized_staff(hash_id):
            return True

    if update.effective_chat.type in ["group", "supergroup"]:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ["administrator", "creator"]:
                return True
        except Exception:
            pass
    else:
        return True

    await send_auto_delete_message(context, chat_id, "❌ Bu komutu kullanmak için yetkiniz yok!")
    return False

async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        if target:
            if target.username:
                database.save_user(chat_id, target.id, target.username.lower())
            return target

    if update.message and update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention" and entity.user:
                return entity.user

    if context.args:
        arg = context.args[0].strip()
        clean_arg = arg.lstrip("@").lower()
        if clean_arg.isdigit() or (clean_arg.startswith("-") and clean_arg[1:].isdigit()):
            uid = int(clean_arg)
            try:
                member = await context.bot.get_chat_member(chat_id, uid)
                return member.user
            except Exception:
                class TempUser:
                    def __init__(self, user_id):
                        self.id = user_id
                        self.full_name = f"Kullanıcı_{user_id}"
                        self.username = f"id_{user_id}"
                return TempUser(uid)
        else:
            db_uid = database.get_user_id_by_username(chat_id, clean_arg) or database.get_user_id_by_username(0, clean_arg)
            if db_uid:
                try:
                    member = await context.bot.get_chat_member(chat_id, db_uid)
                    return member.user
                except Exception:
                    pass
            class UsernameTarget:
                def __init__(self, uname):
                    self.id = f"@{uname}"
                    self.full_name = f"@{uname}"
                    self.username = uname
            return UsernameTarget(clean_arg)
    return None

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
        "Grup düzenini sağlamak, argo/küfür, dış linkler ve yasaklı içerikleri otomatik engellemek için aktifim.\n\n"
        "Aşağıdaki butondan Golden Staff Paneli'ne giriş yapabilirsin! 👇"
    )
    web_app_url = "https://golden-guardian-bot.onrender.com/"
    keyboard = [[InlineKeyboardButton(text="🛡️ Paneli Aç", web_app=WebAppInfo(url=web_app_url))]]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    text = " ".join(context.args)
    if not text: return
    database.update_group_setting(chat_id, "welcome_msg", text)
    await send_auto_delete_message(context, chat_id, "✅ Hoş geldin mesajı güncellendi!")

async def cmd_butonmetni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    text = " ".join(context.args)
    if not text: return
    database.update_group_setting(chat_id, "btn_text", text)
    await send_auto_delete_message(context, chat_id, "✅ Buton metni güncellendi!")

async def cmd_butonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    if not context.args: return
    url = context.args[0].strip()
    database.update_group_setting(chat_id, "btn_url", url)
    await send_auto_delete_message(context, chat_id, "✅ Buton linki güncellendi!")

async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    raw_text = update.message.text.partition(" ")[2].strip()
    if not raw_text and reply:
        raw_text = reply.text or reply.caption or ""
        
    if not raw_text:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanım: /duyuru Mesaj | Buton Metni | Link")
        return
        
    settings = database.get_group_settings(chat_id)
    msg_text = raw_text
    btn_text = settings["btn_text"]
    btn_url = settings["btn_url"]
    media_url = settings.get("media_url", "")

    if "|" in raw_text:
        parts = [p.strip() for p in raw_text.split("|")]
        msg_text = parts[0]
        if len(parts) > 1 and parts[1]: btn_text = parts[1]
        if len(parts) > 2 and parts[2]: btn_url = parts[2]

    if not btn_url.startswith(("http://", "https://", "tg://")):
        btn_url = "https://" + btn_url

    keyboard = [[InlineKeyboardButton(text=btn_text, url=btn_url)]]
    try:
        photo_to_send = media_url if media_url else (reply.photo[-1].file_id if (reply and reply.photo) else None)
        if photo_to_send:
            msg = await context.bot.send_photo(chat_id=chat_id, photo=photo_to_send, caption=msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
        try: await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception: pass
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Duyuru hatası: {e}")

async def cmd_kilit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    if not context.args: return
    action = context.args[0].lower()
    try:
        if action in ["kapat", "kilit"]:
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=ChatPermissions(can_send_messages=False))
            await context.bot.send_message(chat_id=chat_id, text="🔒 Sohbet kilitlendi.")
        elif action in ["ac", "aç"]:
            perms = ChatPermissions(can_send_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=perms)
            await context.bot.send_message(chat_id=chat_id, text="🔓 Sohbetin kilidi açıldı.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Kilit hatası: {e}")

async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı.")
        return
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"🚫 {name} gruptan banlandı!")
        database.log_action(chat_id, "BAN", str(user.id), update.effective_user.username or "admin", f"{name} banlandı.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Ban hatası: {e}")

async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if not user: return
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"✅ {name} banı kaldırıldı.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Unban hatası: {e}")

async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı.")
        return
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False))
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"🔇 {name} susturuldu!")
        database.log_action(chat_id, "MUTE", str(user.id), update.effective_user.username or "admin", f"{name} susturuldu.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Mute hatası: {e}")

async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if not user: return
    try:
        perms = ChatPermissions(can_send_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=perms)
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"🔊 {name} susturması kaldırıldı.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Unmute hatası: {e}")

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı.")
        return
    name = getattr(user, 'full_name', str(user.id))
    current_warns = database.add_warning(user.id)
    if current_warns >= 3:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        database.reset_warnings(user.id)
        await send_auto_delete_message(context, chat_id, f"🚫 {name} 3 uyarıya ulaştığı için banlandı!")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ {name} uyarıldı ({current_warns}/3).")

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if not user: return
    database.reset_warnings(user.id)
    await send_auto_delete_message(context, chat_id, f"♻️ Uyarılar sıfırlandı.")

COMMAND_MAP = {
    "start": cmd_start,
    "help": cmd_start,
    "welcome": cmd_welcome,
    "butonmetni": cmd_butonmetni,
    "butonlinki": cmd_butonlinki,
    "duyuru": cmd_duyuru,
    "kilit": cmd_kilit,
    "defol": cmd_defol,
    "undefol": cmd_undefol,
    "itaat": cmd_itaat,
    "unitaat": cmd_unitaat,
    "warn": cmd_warn,
    "unwarn": cmd_unwarn,
}

async def master_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user = update.effective_user
    chat = update.effective_chat
    chat_id = chat.id

    if chat.type in ["group", "supergroup"]:
        database.register_bot_group(chat_id, chat.title or "Grup")

    if user and user.username:
        database.save_user(chat_id, user.id, user.username.lower())

    text = (update.message.text or update.message.caption or "").strip()
    
    if chat.type in ["group", "supergroup"]:
        is_admin = False
        if user:
            try:
                member = await context.bot.get_chat_member(chat_id, user.id)
                if member.status in ["administrator", "creator"] or database.is_authorized_staff(user.id):
                    is_admin = True
            except Exception:
                pass

        if not is_admin and text:
            lower_text = text.lower()
            has_link = "http://" in lower_text or "https://" in lower_text or "t.me/" in lower_text or "telegram.me/" in lower_text or "www." in lower_text or "t.me" in lower_text or "joinchat" in lower_text
            has_bad_words = any(word in lower_text for word in BAD_WORDS)
            
            if has_link or has_bad_words:
                try:
                    await update.message.delete()
                    if user:
                        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                        uname_str = f"@{user.username}" if user.username else str(user.id)
                        name_str = user.full_name or "Kullanıcı"
                        database.log_action(chat_id, "AUTO_BAN_LINK", user.id, uname_str, f"{name_str} ({user.id}) yasaklı içerik/link paylaştı: {text}")
                        await send_auto_delete_message(context, chat_id, f"🚨 Yasaklı içerik engellendi!\n👤 @{uname_str}\n🆔 ID: `{user.id}`", delay=15)
                except Exception as e:
                    logger.error(f"Filtreleme hatası: {e}")
                return

async def post_init(application):
    database.init_db()
    await application.bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_web_server(application))

def main():
    token = os.environ.get("BOT_TOKEN") or (config and getattr(config, "BOT_TOKEN", None))
    if not token: return
    request_config = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = ApplicationBuilder().token(token).request(request_config).post_init(post_init).build()
    
    app.add_error_handler(global_error_handler)
    
    # --- TÜM KOMUTLARI GRUP İÇİNDE AKTİF ETMEK İÇİN COMMAND HANDLER ---
    for cmd, func in COMMAND_MAP.items():
        app.add_handler(CommandHandler(cmd, func))
        
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, master_message_router))

    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

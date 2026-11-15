import os
import json
import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ChatMemberHandler,
    filters
)
from telegram.request import HTTPXRequest
from telegram.error import Conflict
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
try:
    import config
except ImportError:
    config = None

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = (os.environ.get("BOT_TOKEN") or (config and getattr(config, "BOT_TOKEN", None)) or "").strip()
FOUNDER_ID = (os.environ.get("FOUNDER_ID") or (config and getattr(config, "FOUNDER_ID", None)) or "8245373459").strip()

EMERGENCY_LOCKDOWN = False
BAD_WORDS = ["küfür1", "küfür2", "argo1"]
scheduler = AsyncIOScheduler()
tg_application = None

def log_event(level: str, message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        return
    logger.error(f"Telegram Bot Hatası: {context.error}")

# ==========================================
# 🔗 OTOMATİK GRUP ALGILAMA (AUTO-DISCOVERY)
# ==========================================
async def track_bot_group_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result: return
    chat = update.effective_chat
    new_status = result.new_chat_member.status
    if new_status in ["member", "administrator"]:
        try:
            chat_info = await context.bot.get_chat(chat.id)
            photo_url = ""
            if chat_info.photo:
                file_obj = await context.bot.get_file(chat_info.photo.small_file_id)
                photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_obj.file_path}"
            database.register_bot_group(chat.id, chat_info.title or "Yeni Grup", photo_url)
            logger.info(f"✨ Yeni grup otomatik algılandı: {chat_info.title} (ID: {chat.id})")
        except Exception as e:
            logger.error(f"Grup otomatik kayıt hatası: {e}")

# ==========================================
# FASTAPI LİFE-SPAN (YAŞAM DÖNGÜSÜ & BOT BAŞLATICI)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_application
    database.init_db()
    if not scheduler.running:
        scheduler.start()

    saved_jobs = database.get_all_scheduled_jobs()
    for job in saved_jobs:
        try:
            scheduler.add_job(
                send_periodic_announcement,
                'interval',
                minutes=job["minutes"],
                args=[None, job["chat_id"], job["msg_text"], job.get("btn_text", ""), job.get("btn_url", ""), job.get("media_url", "")],
                id=job["job_id"],
                replace_existing=True
            )
        except Exception as e:
            logger.error(f"Kayıtlı görev yüklenemedi: {e}")

    if BOT_TOKEN:
        tg_application = ApplicationBuilder().token(BOT_TOKEN).request(HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)).build()
        tg_application.add_error_handler(global_error_handler)
        
        for cmd, func in COMMAND_MAP.items():
            tg_application.add_handler(CommandHandler(cmd, func))
            
        tg_application.add_handler(ChatMemberHandler(track_bot_group_join, ChatMemberHandler.MY_CHAT_MEMBER))
        tg_application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, master_message_router))
        
        await tg_application.initialize()
        await tg_application.start()
        await tg_application.updater.start_polling(drop_pending_updates=True)
        log_event("SYS", "Telegram Bot ve FastAPI (FastAPI + PTB) eşzamanlı aktif!")

    yield

    if tg_application:
        await tg_application.updater.stop()
        await tg_application.stop()
        await tg_application.shutdown()
    if scheduler.running:
        scheduler.shutdown()
    log_event("SYS", "Sunucu kapatıldı.")

app = FastAPI(lifespan=lifespan)

# ==========================================
# STAFF & ADMIN API ROTALARI (FASTAPI)
# ==========================================

@app.get("/health")
async def health_check():
    return HTMLResponse("GOLDEN_ENTERPRISE_FASTAPI_OK")

@app.get("/api/stats")
async def api_get_stats():
    return database.get_bot_statistics()

@app.get("/api/logs")
async def api_get_logs(filter: str = "all", limit: int = 100):
    try:
        logs = database.get_recent_logs(limit=limit, log_filter=filter)
        return logs
    except Exception as e:
        return []

@app.post("/api/logs/clear")
async def api_clear_logs(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        days = int(data.get("days", 0))
        if not database.is_authorized_staff(user_id) and str(user_id) != str(FOUNDER_ID).strip():
            return {"success": False, "error": "Yetkisiz!"}
        database.clear_logs_by_retention(days)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/admin-stats")
async def api_get_admin_stats():
    return database.get_admin_performance_stats()

@app.get("/api/groups")
async def api_get_groups():
    return database.get_bot_groups()

@app.post("/api/verify-admin")
async def api_verify_admin(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if str(user_id) == str(FOUNDER_ID).strip():
        return {"authorized": True, "is_founder": True}
    if database.is_authorized_staff(user_id):
        return {"authorized": True, "is_founder": False, "permissions": database.get_staff_permissions(user_id)}
    if not FOUNDER_ID:
        return {"authorized": True, "is_founder": True}
    return {"authorized": False}

@app.post("/api/staff/list")
async def api_get_staff_list(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    chat_id = int(data.get("chat_id", 0))
    if not database.is_authorized_staff(user_id) and str(user_id) != str(FOUNDER_ID).strip():
        return {"success": False, "error": "Yetkisiz!"}
    return database.get_all_staff(chat_id)

@app.post("/api/staff/add")
async def api_add_staff(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    chat_id = int(data.get("chat_id", 0))
    input_value = str(data.get("staff_input", "")).strip()
    full_name = data.get("full_name", "💎 Yetkili")
    if not input_value: return {"success": False, "error": "Boş değer"}
    clean_val = input_value.lstrip("@").lower()
    target_id = int(clean_val) if clean_val.isdigit() or (clean_val.startswith("-") and clean_val[1:].isdigit()) else (database.get_user_id_by_username(chat_id, clean_val) or int(hashlib.md5(clean_val.encode()).hexdigest()[:8], 16))
    database.add_staff_member(target_id, clean_val, full_name, user_id, chat_id)
    return {"success": True}

@app.post("/api/staff/toggle")
async def api_toggle_staff_perm(request: Request):
    data = await request.json()
    target_id = int(data.get("target_id", 0))
    chat_id = int(data.get("chat_id", 0))
    perm_field = str(data.get("perm", ""))
    if perm_field in ["can_ban", "can_mute"]:
        new_val = database.toggle_staff_permission(target_id, chat_id, perm_field)
        return {"success": True, "new_val": new_val}
    return {"success": False}

@app.post("/api/staff/remove")
async def api_remove_staff(request: Request):
    data = await request.json()
    target_id = int(data.get("target_id", 0))
    chat_id = int(data.get("chat_id", 0))
    database.remove_staff_member(target_id, chat_id)
    return {"success": True}

@app.post("/api/member/action")
async def api_member_action(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        target_input = str(data.get("target", "")).strip()
        action = str(data.get("action", "")).strip()
        chat_id = int(data.get("chat_id", 0))
        
        is_founder = str(user_id) == str(FOUNDER_ID).strip()
        if not is_founder and not database.is_authorized_staff(user_id):
            return {"success": False, "error": "Yetkisiz!"}

        if not tg_application or not tg_application.bot:
            return {"success": False, "error": "Bot aktif değil!"}

        bot = tg_application.bot
        clean_target = target_input.lstrip("@").lower()
        target_id = int(clean_target) if clean_target.isdigit() or (clean_target.startswith("-") and clean_target[1:].isdigit()) else database.get_user_id_by_username(chat_id, clean_target)
        if not target_id: return {"success": False, "error": "Kullanıcı bulunamadı!"}

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
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/settings/update")
async def api_update_settings(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        chat_id = int(data.get("chat_id", 0))
        if not database.is_authorized_staff(user_id) and str(user_id) != str(FOUNDER_ID).strip():
            return {"success": False, "error": "Yetkisiz!"}
        if chat_id:
            for key in ["welcome_msg", "btn_text", "btn_url", "media_url", "announcement_btn_text", "announcement_btn_url", "scheduled_btn_text", "scheduled_btn_url"]:
                if key in data: database.update_group_setting(chat_id, key, data[key])
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# MUTLAK YOL (ABSOLUTE PATH) İLE İNDEX.HTML SUNUMU
@app.get("/", response_class=HTMLResponse)
async def handle_root():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    paths_to_check = [
        os.path.join(base_dir, "public", "index.html"),
        os.path.join(base_dir, "index.html")
    ]
    
    for path in paths_to_check:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return HTMLResponse(content=f.read(), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
            except Exception as e:
                logger.error(f"HTML okuma hatası ({path}): {e}")
                
    return HTMLResponse(content=f"Index dosyası (public/index.html) bulunamadı! Temel dizin: {base_dir}", status_code=500)

# ==========================================
# TELEGRAM BOT KOMUTLARI & YÖNETİM
# ==========================================

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
    if str(user.id) == str(FOUNDER_ID).strip(): return True
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

async def send_periodic_announcement(bot, chat_id, text, btn_text, btn_url, media_url):
    try:
        current_bot = bot or (tg_application and tg_application.bot)
        if not current_bot: return
        
        reply_markup = None
        if not btn_text or not btn_url:
            sched_settings = database.get_announcement_settings(chat_id)
            if not btn_text: btn_text = sched_settings.get("scheduled_btn_text", "")
            if not btn_url: btn_url = sched_settings.get("scheduled_btn_url", "")

        if btn_text and btn_url:
            clean_url = str(btn_url).strip()
            if clean_url.startswith(("http://", "https://", "tg://")) and " " not in clean_url and "\n" not in clean_url:
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text=btn_text, url=clean_url)]])
            elif " " not in clean_url and "\n" not in clean_url:
                clean_url = "https://" + clean_url
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text=btn_text, url=clean_url)]])

        if media_url:
            await current_bot.send_photo(chat_id=chat_id, photo=media_url, caption=text, reply_markup=reply_markup)
        else:
            await current_bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Zamanlı duyuru gönderme hatası: {e}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        try:
            chat_info = await context.bot.get_chat(chat.id)
            photo_url = ""
            if chat_info.photo:
                file_id = chat_info.photo.small_file_id
                file_obj = await context.bot.get_file(file_id)
                photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_obj.file_path}"
            database.register_bot_group(chat.id, chat.title or "Grup", photo_url)
        except Exception:
            database.register_bot_group(chat.id, chat.title or "Grup", "")

        try: await update.message.delete()
        except Exception: pass
        return

    welcome_text = (
        "🤖 Grup Yönetim & Golden Staff Güvenlik Botu\n\n"
        "📜 Yönetici Komutları:\n"
        "- /welcome <mesaj> - Hoş geldin mesajı\n"
        "- /butonmetni <yazı> - Hoş geldin butonu yazısı\n"
        "- /butonlinki <url> - Hoş geldin butonu linki\n"
        "- /duyuru <mesaj> - Duyuru gönder (Otomatik Pinlenir / Yanıtlanan görseli sabitler)\n"
        "- /duyuru <mesaj> | <buton metni> | <link> - Özel butonlu duyuru\n"
        "- /duyurubutonmetni <yazı> - Varsayılan duyuru butonu yazısını ayarla\n"
        "- /duyurubutonlinki <url> - Varsayılan duyuru butonu linkini ayarla\n"
        "- /zamanlabutonmetin <yazı> - Zamanlı duyuru butonu yazısını ayarla\n"
        "- /zamanlabutonlinki <url> - Zamanlı duyuru butonu linkini ayarla\n"
        "- /zamanla <dakika> <mesaj> - Periyodik duyuru başlat\n"
        "- /zamanla <dakika> | <mesaj> | <buton metni> | <link> - Özel butonlu periyodik duyuru\n"
        "- /zamanladur veya /zamanla dur - Zamanlanmış paylaşımı durdur\n"
        "- /defol <kullanıcı> - Banla\n"
        "- /undefol <kullanıcı> - Ban kaldır\n"
        "- /itaat <kullanıcı> - Sustur\n"
        "- /unitaat <kullanıcı> - Susturma kaldır\n"
        "- /uyari <kullanıcı> - Uyarı ver (3 uyarıda otomatik banlar)\n"
        "- /unuyari <kullanıcı> - Uyarı sıfırla\n\n"
        "⚜️ Powered by 𝘽𝙍𝙀𝙎𝙆𝘼𝙑𝙄𝘾𝘼 🇷🇸"
    )
    web_app_url = os.environ.get("WEBAPP_URL", "https://golden-guardian-bot.onrender.com/")
    keyboard = [[InlineKeyboardButton(text="🛡️ Paneli Aç", web_app=WebAppInfo(url=web_app_url))]]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

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
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Hoş geldin buton metni güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_butonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    if context.args: 
        database.update_group_setting(update.effective_chat.id, "btn_url", context.args[0].strip())
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Hoş geldin buton linki güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    raw_text = update.message.text.partition(" ")[2].strip()

    photo_file_id = None
    if reply:
        if reply.photo:
            photo_file_id = reply.photo[-1].file_id
        if not raw_text:
            raw_text = reply.caption or reply.text or ""

    if not raw_text and not photo_file_id:
        await send_auto_delete_message(context, chat_id, "❌ Duyuru metni veya görsel belirtmelisiniz!", 4)
        try: await update.message.delete()
        except Exception: pass
        return

    def_settings = database.get_announcement_settings(chat_id)
    btn_text = def_settings.get("announcement_btn_text", def_settings.get("btn_text", ""))
    btn_url = def_settings.get("announcement_btn_url", def_settings.get("btn_url", ""))
    msg_text = raw_text

    if "|" in raw_text:
        parts = [p.strip() for p in raw_text.split("|")]
        msg_text = parts[0]
        if len(parts) > 1 and parts[1]: btn_text = parts[1]
        if len(parts) > 2 and parts[2]: btn_url = parts[2]

    reply_markup = None
    if btn_text and btn_url:
        clean_url = str(btn_url).strip()
        if not clean_url.startswith(("http://", "https://", "tg://")):
            clean_url = "https://" + clean_url
        if " " not in clean_url and "\n" not in clean_url:
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text=btn_text, url=clean_url)]])

    try:
        if photo_file_id:
            sent_msg = await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=msg_text, reply_markup=reply_markup)
        else:
            sent_msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=reply_markup)

        await context.bot.pin_chat_message(chat_id=chat_id, message_id=sent_msg.message_id, disable_notification=False)
        admin_user = update.effective_user.username or update.effective_user.first_name
        database.log_action(chat_id, "ANNOUNCEMENT", 0, f"@{admin_user}", f"Duyuru gönderildi: {msg_text[:30]}...")
    except Exception as e:
        logger.error(f"Duyuru gönderme hatası: {e}")

    try: await update.message.delete()
    except Exception: pass

async def cmd_duyurubutonmetni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    text = " ".join(context.args)
    if text:
        database.update_group_setting(update.effective_chat.id, "announcement_btn_text", text)
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Varsayılan duyuru buton metni güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_duyurubutonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    if context.args:
        database.update_group_setting(update.effective_chat.id, "announcement_btn_url", context.args[0].strip())
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Varsayılan duyuru buton linki güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_zamanlabutonmetin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    text = " ".join(context.args)
    if text:
        database.update_group_setting(update.effective_chat.id, "scheduled_btn_text", text)
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Zamanlı duyuru buton metni güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_zamanlabutonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    if context.args:
        database.update_group_setting(update.effective_chat.id, "scheduled_btn_url", context.args[0].strip())
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Zamanlı duyuru buton linki güncellendi!", 4)
    try: await update.message.delete()
    except Exception: pass

async def cmd_zamanladur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    job_to_remove = database.get_latest_scheduled_job(chat_id)

    if job_to_remove:
        job_id = job_to_remove["job_id"]
        try: scheduler.remove_job(job_id)
        except Exception: pass
        database.remove_scheduled_job_db(job_id)
        admin_user = update.effective_user.username or update.effective_user.first_name
        database.log_action(chat_id, "SCHEDULE_STOP", 0, f"@{admin_user}", "Periyodik duyuru durduruldu.")
        await send_auto_delete_message(context, chat_id, "🛑 Periyodik duyuru durduruldu!", 5)
    else:
        await send_auto_delete_message(context, chat_id, "❌ Aktif periyodik duyuru bulunamadı!", 5)
    try: await update.message.delete()
    except Exception: pass

async def cmd_zamanla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    raw_args = update.message.text.partition(" ")[2].strip()

    if raw_args.lower().startswith("dur"):
        return await cmd_zamanladur(update, context)

    media_url = ""
    if reply and reply.photo:
        media_url = reply.photo[-1].file_id

    btn_text, btn_url, minutes, msg_text = "", "", 0, ""
    if "|" in raw_args:
        parts = [p.strip() for p in raw_args.split("|")]
        try: minutes = int(parts[0])
        except ValueError:
            await send_auto_delete_message(context, chat_id, "❌ Dakika sayısal olmalıdır!", 5)
            try: await update.message.delete()
            except Exception: pass
            return
        msg_text = parts[1] if len(parts) > 1 else ""
        btn_text = parts[2] if len(parts) > 2 else ""
        btn_url = parts[3] if len(parts) > 3 else ""
    else:
        parts = raw_args.split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            await send_auto_delete_message(context, chat_id, "❌ Format: /zamanla <dakika> <mesaj>", 6)
            try: await update.message.delete()
            except Exception: pass
            return
        minutes = int(parts[0])
        msg_text = parts[1] if len(parts) > 1 else ""

    if not msg_text and reply:
        msg_text = reply.caption or reply.text or ""

    if not btn_text or not btn_url:
        sched_settings = database.get_announcement_settings(chat_id)
        if not btn_text: btn_text = sched_settings.get("scheduled_btn_text", "")
        if not btn_url: btn_url = sched_settings.get("scheduled_btn_url", "")

    job_id = hashlib.md5(f"{chat_id}_{minutes}_{msg_text[:20]}".encode()).hexdigest()
    scheduler.add_job(
        send_periodic_announcement,
        'interval',
        minutes=minutes,
        args=[context.bot, chat_id, msg_text, btn_text, btn_url, media_url],
        id=job_id,
        replace_existing=True
    )

    database.save_scheduled_job(job_id, chat_id, minutes, msg_text, btn_text, btn_url, media_url)
    admin_user = update.effective_user.username or update.effective_user.first_name
    database.log_action(chat_id, "SCHEDULE_START", 0, f"@{admin_user}", f"Her {minutes} dakikada bir duyuru başlatıldı.")
    await send_auto_delete_message(context, chat_id, f"✅ Her {minutes} dakikada bir paylaşılacak görev başlatıldı!", 5)
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
            database.log_action(chat_id, "UNBAN", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} banı kaldırdı.")
            await send_auto_delete_message(context, chat_id, f"✅ @{uname} banı kaldırıldı!", 5)
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
            database.log_action(chat_id, "MUTE", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} susturdu.")
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
            database.log_action(chat_id, "UNMUTE", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} susturmayı kaldırdı.")
            await send_auto_delete_message(context, chat_id, f"🔊 @{uname} susturması kaldırıldı!", 5)
        except Exception as e: logger.error(f"Unmute hatası: {e}")
    try: await update.message.delete()
    except Exception: pass

async def cmd_uyari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        uname = getattr(user, 'username', str(user.id))
        admin_uname = update.effective_user.username or update.effective_user.first_name
        current_warns = database.add_warning(user.id)
        if current_warns >= 3:
            try:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                database.reset_warnings(user.id)
                database.log_action(chat_id, "BAN", user.id, f"@{uname} (ID: {user.id})", f"3 uyarı sınırını aştığı için otomatik banlandı.")
                await send_auto_delete_message(context, chat_id, f"🚫 @{uname} 3 uyarıyı aştığı için otomatik banlandı!", 6)
            except Exception as e: logger.error(f"Otomatik ban hatası: {e}")
        else:
            database.log_action(chat_id, "WARN", user.id, f"@{uname} (ID: {user.id})", f"Admin @{admin_uname} uyarı verdi ({current_warns}/3).")
            await send_auto_delete_message(context, chat_id, f"⚠️ @{uname} uyarıldı! ({current_warns}/3)", 5)
    try: await update.message.delete()
    except Exception: pass

async def cmd_unuyari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user: 
        uname = getattr(user, 'username', str(user.id))
        admin_uname = update.effective_user.username or update.effective_user.first_name
        database.reset_warnings(user.id)
        database.log_action(chat_id, "UNWARN", user.id, f"@{uname} (ID: {user.id})", f"Admin uyarıları sıfırladı.")
        await send_auto_delete_message(context, chat_id, f"✅ @{uname} uyarıları sıfırlandı!", 5)
    try: await update.message.delete()
    except Exception: pass

COMMAND_MAP = {
    "start": cmd_start,
    "help": cmd_start,
    "welcome": cmd_welcome,
    "butonmetni": cmd_butonmetni,
    "butonlinki": cmd_butonlinki,
    "duyuru": cmd_duyuru,
    "duyurubutonmetni": cmd_duyurubutonmetni,
    "duyurubutonlinki": cmd_duyurubutonlinki,
    "zamanlabutonmetin": cmd_zamanlabutonmetin,
    "zamanlabutonlinki": cmd_zamanlabutonlinki,
    "zamanla": cmd_zamanla,
    "zamanladur": cmd_zamanladur,
    "kilit": cmd_kilit,
    "defol": cmd_defol,
    "undefol": cmd_undefol,
    "itaat": cmd_itaat,
    "unitaat": cmd_unitaat,
    "uyari": cmd_uyari,
    "warn": cmd_uyari,
    "unuyari": cmd_unuyari,
    "unwarn": cmd_unuyari,
}

async def master_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user, chat, chat_id = update.effective_user, update.effective_chat, update.effective_chat.id
    if chat.type in ["group", "supergroup"]:
        try:
            chat_info = await context.bot.get_chat(chat.id)
            photo_url = ""
            if chat_info.photo:
                file_obj = await context.bot.get_file(chat_info.photo.small_file_id)
                photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_obj.file_path}"
            database.register_bot_group(chat.id, chat.title or "Grup", photo_url)
        except Exception:
            database.register_bot_group(chat.id, chat.title or "Grup", "")

    if user and user.username: database.save_user(chat_id, user.id, user.username.lower())

    text = (update.message.text or update.message.caption or "").strip()
    if not text: return

    lower_first_word = text.split()[0].lower()
    if lower_first_word in ["/uyarı", "/uyari"]:
        context.args = text.split()[1:]
        return await cmd_uyari(update, context)
    elif lower_first_word in ["/unuyarı", "/unuyari"]:
        context.args = text.split()[1:]
        return await cmd_unuyari(update, context)

    if chat.type in ["group", "supergroup"]:
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
                        database.log_action(chat_id, "AUTO_BAN", user.id, f"{uname_str} (ID: {user.id})", "Yasaklı içerik (otomatik ban).")
                except Exception as e: logger.error(f"Filtre hatası: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

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
import config
import database

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Arayüz log kalabalığını kapat
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

ADMIN_ACTION_LIMITS = {}
MAX_ACTIONS_PER_MINUTE = 10
EMERGENCY_LOCKDOWN = False
BAD_WORDS = ["küfür1", "küfür2", "argo1"]
SCHEDULED_TASKS = {}

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.warning("⚠️ UYARI: Bot tokenı başka bir yerde daha açık! Lütfen BotFather'dan tokenı yenileyin (Revoke).")
        return
    logger.error(f"Telegram Bot Hatası: {context.error}", exc_info=context.error)

# --- API ROTALARI ---
async def api_get_stats(request):
    return web.json_response(database.get_bot_statistics())

async def api_get_logs(request):
    return web.json_response(database.get_recent_logs(limit=50))

async def api_get_admin_stats(request):
    return web.json_response(database.get_admin_performance_stats())

async def api_get_data_cloud(request):
    return web.json_response(database.get_data_cloud_tags())

async def api_get_lock_status(request):
    return web.json_response({"lockdown": EMERGENCY_LOCKDOWN})

async def api_dummy_chat(request):
    return web.json_response([])

async def api_toggle_lockdown(request):
    global EMERGENCY_LOCKDOWN
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    founder_id = getattr(config, "FOUNDER_ID", None) or os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) != str(founder_id).strip():
        return web.json_response({"success": False, "error": "Yetkisiz!"}, status=403)
    EMERGENCY_LOCKDOWN = not EMERGENCY_LOCKDOWN
    return web.json_response({"success": True, "lockdown": EMERGENCY_LOCKDOWN})

async def api_verify_admin(request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    founder_id = getattr(config, "FOUNDER_ID", None) or os.environ.get("FOUNDER_ID")
    
    if founder_id and str(user_id) == str(founder_id).strip():
        return web.json_response({"authorized": True, "is_founder": True})
    if database.is_authorized_staff(user_id):
        return web.json_response({"authorized": True, "is_founder": False, "permissions": database.get_staff_permissions(user_id)})
    if not founder_id:
        return web.json_response({"authorized": True, "is_founder": True})
    return web.json_response({"authorized": False})

async def api_get_staff_list(request):
    staffs = database.get_all_staff()
    result = [{"user_id": s[0], "username": s[1], "full_name": s[2], "can_ban": s[3], "can_speak": s[4], "can_write": s[5], "is_muted": s[6], "warnings": s[7]} for s in staffs]
    return web.json_response(result)

async def api_add_staff(request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    input_value = str(data.get("staff_input", "")).strip()
    full_name = data.get("full_name", "Yetkili")
    if not input_value:
        return web.json_response({"success": False, "error": "Boş değer"}, status=400)
    
    clean_val = input_value.lstrip("@")
    if clean_val.isdigit() or (clean_val.startswith("-") and clean_val[1:].isdigit()):
        target_id = int(clean_val)
    else:
        found_id = database.get_user_id_by_username(0, clean_val.lower())
        target_id = found_id if found_id else int(hashlib.md5(clean_val.lower().encode()).hexdigest()[:8], 16)
        
    database.add_staff_member(target_id, clean_val, full_name, user_id)
    return web.json_response({"success": True})

async def api_remove_staff(request):
    data = await request.json()
    target_id = int(data.get("target_id", 0))
    database.remove_staff_member(target_id)
    return web.json_response({"success": True})

async def start_web_server(app_context):
    app = web.Application()
    app['bot'] = app_context.bot
    app.router.add_get("/api/stats", api_get_stats)
    app.router.add_get("/api/logs", api_get_logs)
    app.router.add_get("/api/admin-stats", api_get_admin_stats)
    app.router.add_get("/api/data-cloud", api_get_data_cloud)
    app.router.add_get("/api/lock-status", api_get_lock_status)
    app.router.add_get("/api/chat", api_dummy_chat)
    app.router.add_post("/api/chat", api_dummy_chat)
    app.router.add_post("/api/toggle-lockdown", api_toggle_lockdown)
    app.router.add_post("/api/verify-admin", api_verify_admin)
    app.router.add_post("/api/staff/list", api_get_staff_list)
    app.router.add_post("/api/staff/add", api_add_staff)
    app.router.add_post("/api/staff/remove", api_remove_staff)
    
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
    logger.info(f"Web sunucusu {port} portunda başlatıldı.")

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

# YETKİ KONTROLÜ
async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user:
        return False
    user_id = user.id

    if update.message and update.message.sender_chat and update.message.sender_chat.id == chat_id:
        return True

    founder_id = getattr(config, "FOUNDER_ID", None) or os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) == str(founder_id).strip():
        return True

    if database.is_authorized_staff(user_id):
        perms = database.get_staff_permissions(user_id)
        if perms.get("can_ban", 1) == 1:
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
        except Exception as e:
            logger.error(f"Grup admin kontrol hatası: {e}")
    else:
        return True

    await send_auto_delete_message(context, chat_id, "❌ Bu komutu kullanmak için yönetici yetkiniz yok!")
    return False

async def check_admin_rate_limit(chat_id, user_id, context):
    global EMERGENCY_LOCKDOWN
    founder_id = getattr(config, "FOUNDER_ID", None) or os.environ.get("FOUNDER_ID")
    if founder_id and str(user_id) == str(founder_id).strip():
        return True
    if EMERGENCY_LOCKDOWN:
        await send_auto_delete_message(context, chat_id, "🛑 Acil Durum Kilidi aktif! İşlemler durduruldu.")
        return False

    now = time.time()
    key = (chat_id, user_id)
    if key not in ADMIN_ACTION_LIMITS:
        ADMIN_ACTION_LIMITS[key] = []

    ADMIN_ACTION_LIMITS[key] = [t for t in ADMIN_ACTION_LIMITS[key] if now - t < 60]

    if len(ADMIN_ACTION_LIMITS[key]) >= MAX_ACTIONS_PER_MINUTE:
        database.log_action(chat_id, "FLOOD_BAN", user_id, f"id_{user_id}", "Flood korumasına takıldı.")
        await send_auto_delete_message(context, chat_id, "⚠️ Çok hızlı işlem yaptığınız için Flood Korumasına takıldınız!")
        return False

    ADMIN_ACTION_LIMITS[key].append(now)
    return True

# HEDEF KULLANICIYI BULMA
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
        clean_arg = arg.lstrip("@")
        
        if clean_arg.isdigit() or (clean_arg.startswith("-") and clean_arg[1:].isdigit()):
            uid = int(clean_arg)
            try:
                member = await context.bot.get_chat_member(chat_id, uid)
                return member.user
            except Exception:
                class TempUser:
                    def __init__(self, user_id, name):
                        self.id = user_id
                        self.full_name = name
                        self.username = name
                return TempUser(uid, f"ID:{uid}")
        else:
            db_uid = database.get_user_id_by_username(chat_id, clean_arg.lower())
            if db_uid:
                try:
                    member = await context.bot.get_chat_member(chat_id, db_uid)
                    return member.user
                except Exception:
                    class TempUser:
                        def __init__(self, user_id, name):
                            self.id = user_id
                            self.full_name = name
                            self.username = name
                    return TempUser(db_uid, clean_arg)
                    
    return None

# --- TELEGRAM KOMUTLARI ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = database.get_group_settings(chat_id)
    
    welcome_text = (
        "🤖 Grup Yönetim & Moderasyon Botu\n\n"
        f"{settings['welcome_msg']}\n\n"
        "🛠 Gelişmiş Özellikler:\n"
        "- Dinamik Hoş Geldin Mesajı: Özelleştirilebilir karşılama mesajı.\n"
        "- Otomatik Küfür Filtresi: Yasaklı kelimeler anında silinir.\n"
        "- Sohbet Kilidi: Yönetici komutuyla grubu tamamen kapatma/açma.\n"
        "- 3 Uyarı Sistemi: 3 uyarı alan kullanıcı gruptan otomatik engellenir.\n"
        "- Temiz Sohbet: Bot mesajları 10 saniye sonra otomatik silinir.\n"
        "- Dinamik Butonlu Duyuru: Butonlu mesaj gönderip üst kısma sabitleme.\n"
        "- Süreli Otomatik Paylaşım: Belirli aralıklarla otomatik mesaj/resim gönderme.\n\n"
        "📜 Yönetici Komutları:\n"
        "- /welcome <mesaj> : Hoş geldin mesajını değiştirir.\n"
        "- /butonmetni <yazı> : Varsayılan buton üzerindeki yazıyı değiştirir.\n"
        "- /butonlinki <url> : Varsayılan butonun yönlendireceği linki değiştirir.\n"
        "- /duyuru <mesaj> : Kayıtlı buton ile duyuru gönderir.\n"
        "- /duyuru <mesaj> | <buton metni> | <link> : Tek satırda özel butonlu duyuru gönderir.\n"
        "- /zamanla <dakika> : Yanıtlanan mesajı/görseli periyodik olarak otomatik paylaşır.\n"
        "- /zamanladur : Aktif otomatik paylaşımı durdurur.\n"
        "- /kilit kapat / ac : Gruba mesaj yazmayı kilitler veya açar.\n"
        "- /defol <@kullanici|ID|yanıt> : Kullanıcıyı banlar.\n"
        "- /undefol <ID|yanıt> : Engeli kaldırır.\n"
        "- /itaat <@kullanici|ID|yanıt> : Kullanıcıyı susturur.\n"
        "- /unitaat <@kullanici|ID|yanıt> : Susturmayı kaldırır.\n"
        "- /warn <@kullanici|ID|yanıt> : Kullanıcıya uyarı verir.\n"
        "- /unwarn <@kullanici|ID|yanıt> : Uyarıları sıfırlar."
    )
    
    web_app_url = "https://golden-guardian-bot.onrender.com/"
    if update.effective_chat.type == "private":
        keyboard = [[InlineKeyboardButton(text="🛡️ Golden Staff Paneli", web_app=WebAppInfo(url=web_app_url))]]
    else:
        keyboard = [[InlineKeyboardButton(text="🛡️ Golden Staff Paneli", url=web_app_url)]]
        
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    text = " ".join(context.args)
    if not text:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanım: /welcome <yeni karşılama mesajı>")
        return
    database.update_group_setting(chat_id, "welcome_msg", text)
    await send_auto_delete_message(context, chat_id, "✅ Hoş geldin mesajı güncellendi!")

async def cmd_butonmetni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    text = " ".join(context.args)
    if not text:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanım: /butonmetni <buton yazısı>")
        return
    database.update_group_setting(chat_id, "btn_text", text)
    await send_auto_delete_message(context, chat_id, f"✅ Varsayılan buton metni '{text}' olarak güncellendi!")

async def cmd_butonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    if not context.args:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanım: /butonlinki <https://link.com>")
        return
    url = context.args[0].strip()
    if not url.startswith(("http://", "https://", "tg://")):
        url = "https://" + url
    database.update_group_setting(chat_id, "btn_url", url)
    await send_auto_delete_message(context, chat_id, f"✅ Varsayılan buton linki güncellendi: {url}")

async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    
    raw_text = update.message.text.partition(" ")[2].strip()
    if not raw_text and update.message.reply_to_message:
        raw_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        
    if not raw_text:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanım:\n/duyuru <mesaj>\nveya\n/duyuru <mesaj> | <buton metni> | <link>")
        return
        
    settings = database.get_group_settings(chat_id)
    
    if "|" in raw_text:
        parts = [p.strip() for p in raw_text.split("|")]
        msg_text = parts[0]
        btn_text = parts[1] if len(parts) > 1 and parts[1] else settings["btn_text"]
        btn_url = parts[2] if len(parts) > 2 and parts[2] else settings["btn_url"]
    else:
        msg_text = raw_text
        btn_text = settings["btn_text"]
        btn_url = settings["btn_url"]

    if not btn_url.startswith(("http://", "https://", "tg://")):
        btn_url = "https://" + btn_url

    keyboard = [[InlineKeyboardButton(text=btn_text, url=btn_url)]]
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📢 DUYURU\n\n{msg_text}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        try:
            await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception:
            pass
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Duyuru gönderilemedi: {e}")

async def periodic_broadcast(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    data = job.data
    settings = database.get_group_settings(chat_id)
    btn_url = settings["btn_url"]
    if not btn_url.startswith(("http://", "https://", "tg://")):
        btn_url = "https://" + btn_url
    keyboard = [[InlineKeyboardButton(text=settings["btn_text"], url=btn_url)]]
    
    try:
        if data.get("photo"):
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=data["photo"],
                caption=data.get("text", ""),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=data.get("text", "Periyodik paylaşım"),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        logger.error(f"Periyodik paylaşım hatası: {e}")

async def cmd_zamanla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        await send_auto_delete_message(context, chat_id, "⚠️ Bir mesajı yanıtlayarak kullanın: /zamanla <dakika>")
        return
        
    minutes = int(context.args[0])
    reply = update.message.reply_to_message
    
    payload = {"text": "Periyodik bilgilendirme mesajı.", "photo": None}
    if reply:
        if reply.photo:
            payload["photo"] = reply.photo[-1].file_id
            payload["text"] = reply.caption or ""
        elif reply.text:
            payload["text"] = reply.text
    elif len(context.args) > 1:
        payload["text"] = " ".join(context.args[1:])
        
    if chat_id in SCHEDULED_TASKS:
        SCHEDULED_TASKS[chat_id].schedule_removal()
        
    job = context.job_queue.run_repeating(periodic_broadcast, interval=minutes * 60, first=minutes * 60, chat_id=chat_id, data=payload)
    SCHEDULED_TASKS[chat_id] = job
    await send_auto_delete_message(context, chat_id, f"✅ Otomatik paylaşım her {minutes} dakikada bir çalışacak şekilde ayarlandı!")

async def cmd_zamanladur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in SCHEDULED_TASKS:
        SCHEDULED_TASKS[chat_id].schedule_removal()
        del SCHEDULED_TASKS[chat_id]
        await send_auto_delete_message(context, chat_id, "⏹️ Aktif otomatik paylaşım durduruldu.")
    else:
        await send_auto_delete_message(context, chat_id, "ℹ️ Aktif bir otomatik paylaşım bulunmuyor.")

async def cmd_kilit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    if not context.args:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanım: /kilit kapat veya /kilit ac")
        return
    action = context.args[0].lower()
    try:
        if action in ["kapat", "kilit"]:
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=ChatPermissions(can_send_messages=False))
            await context.bot.send_message(chat_id=chat_id, text="🔒 Sohbet yönetici tarafından kilitlendi.")
            database.log_action(chat_id, "LOCK_CHAT", 0, update.effective_user.username or "admin", "Sohbet kilitlendi.")
        elif action in ["ac", "aç"]:
            perms = ChatPermissions(
                can_send_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            await context.bot.set_chat_permissions(chat_id=chat_id, permissions=perms)
            await context.bot.send_message(chat_id=chat_id, text="🔓 Sohbetin kilidi açıldı.")
            database.log_action(chat_id, "UNLOCK_CHAT", 0, update.effective_user.username or "admin", "Sohbet kilidi açıldı.")
        else:
            await send_auto_delete_message(context, chat_id, "⚠️ Kullanım: /kilit kapat veya /kilit ac")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Kilit işlemi başarısız: {e}")

async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await check_admin_rate_limit(chat_id, admin_id, context): return
    
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı! Bir mesajı yanıtlayın veya /defol <ID|@kullanici> yazın.")
        return
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"🚫 {name} gruptan engellendi!")
        database.log_action(chat_id, "BAN", user.id, update.effective_user.username or str(admin_id), f"{name} banlandı.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Ban işlemi başarısız: {e}")

async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı! Bir mesajı yanıtlayın veya /undefol <ID> yazın.")
        return
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"✅ {name} kullanıcısının engeli kaldırıldı.")
        database.log_action(chat_id, "UNBAN", user.id, update.effective_user.username or str(admin_id), f"{name} banı kaldırıldı.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Unban hatası: {e}")

async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    if not await check_admin_rate_limit(chat_id, admin_id, context): return
    
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı! Bir mesajı yanıtlayın veya /itaat <ID|@kullanici> yazın.")
        return
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False))
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"🔇 {name} susturuldu!")
        database.log_action(chat_id, "MUTE", user.id, update.effective_user.username or str(admin_id), f"{name} susturuldu.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Susturma başarısız: {e}")

async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı! Bir mesajı yanıtlayın veya /unitaat <ID|@kullanici> yazın.")
        return
    try:
        perms = ChatPermissions(
            can_send_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=perms)
        name = getattr(user, 'full_name', str(user.id))
        await send_auto_delete_message(context, chat_id, f"🔊 {name} susturması kaldırıldı.")
        database.log_action(chat_id, "UNMUTE", user.id, update.effective_user.username or str(admin_id), f"{name} susturması kaldırıldı.")
    except Exception as e:
        await send_auto_delete_message(context, chat_id, f"❌ Susturma kaldırma hatası: {e}")

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı! Bir mesajı yanıtlayın veya /warn <ID|@kullanici> yazın.")
        return
        
    name = getattr(user, 'full_name', str(user.id))
    uname = getattr(user, 'username', str(user.id)) or str(user.id)
    
    if not database.is_authorized_staff(user.id):
        database.add_staff_member(user.id, uname, name, 0)
        
    current_warns = database.add_warning(user.id)
    database.log_action(chat_id, "WARN", user.id, update.effective_user.username or str(admin_id), f"{name} uyarıldı ({current_warns}/3)")
    
    if current_warns >= 3:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            database.reset_warnings(user.id)
            database.log_action(chat_id, "AUTO_BAN_WARN", user.id, "SYSTEM", f"{name} 3 uyarıya ulaştığı için banlandı.")
            await send_auto_delete_message(context, chat_id, f"🚫 {name} 3/3 uyarı sınırına ulaştığı için gruptan otomatik engellendi!")
        except Exception as e:
            await send_auto_delete_message(context, chat_id, f"⚠️ {name} 3/3 uyarıya ulaştı ancak banlanamadı: {e}")
    else:
        await send_auto_delete_message(context, chat_id, f"⚠️ {name} uyarıldı! (Mevcut Uyarı: {current_warns}/3)")

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context): return
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    user = await get_target_user(update, context)
    if not user:
        await send_auto_delete_message(context, chat_id, "⚠️ Kullanıcı bulunamadı! Bir mesajı yanıtlayın veya /unwarn <ID|@kullanici> yazın.")
        return
    name = getattr(user, 'full_name', str(user.id))
    database.reset_warnings(user.id)
    database.log_action(chat_id, "UNWARN", user.id, update.effective_user.username or str(admin_id), f"{name} uyarıları sıfırlandı.")
    await send_auto_delete_message(context, chat_id, f"♻️ {name} kullanıcısının tüm uyarıları sıfırlandı (0/3).")

async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members: return
    chat_id = update.effective_chat.id
    settings = database.get_group_settings(chat_id)
    for member in update.message.new_chat_members:
        if member.is_bot: continue
        if member.username:
            database.save_user(chat_id, member.id, member.username.lower())
        btn_url = settings["btn_url"]
        if not btn_url.startswith(("http://", "https://", "tg://")):
            btn_url = "https://" + btn_url
        keyboard = [[InlineKeyboardButton(text=settings["btn_text"], url=btn_url)]]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"👋 Hoş geldin {member.full_name}!\n\n{settings['welcome_msg']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# TÜM MESAJLARI VE KOMUTLARI YAKALAYAN MERKEZİ YÖNLENDİRİCİ
COMMAND_MAP = {
    "start": cmd_start,
    "help": cmd_start,
    "welcome": cmd_welcome,
    "butonmetni": cmd_butonmetni,
    "butonlinki": cmd_butonlinki,
    "duyuru": cmd_duyuru,
    "zamanla": cmd_zamanla,
    "zamanladur": cmd_zamanladur,
    "kilit": cmd_kilit,
    "defol": cmd_defol,
    "undefol": cmd_undefol,
    "itaat": cmd_itaat,
    "unitaat": cmd_unitaat,
    "warn": cmd_warn,
    "unwarn": cmd_unwarn,
}

async def master_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    user = update.effective_user
    chat_id = update.effective_chat.id
    if user and user.username:
        database.save_user(chat_id, user.id, user.username.lower())

    text = (update.message.text or update.message.caption or "").strip()
    if not text:
        return

    # EĞER MESAJ "/" VEYA "!" İLE BAŞLIYORSA DOĞRUDAN KOMUT OLARAK ÇALIŞTIR
    if text.startswith(("/", "!")):
        parts = text.split()
        cmd_word = parts[0][1:].split("@")[0].lower()
        context.args = parts[1:]
        
        if cmd_word in COMMAND_MAP:
            logger.info(f"✅ [KOMUT YAKALANDI] /{cmd_word} | Kullanıcı: {user.id if user else 'Anonim'} | Chat: {chat_id}")
            await COMMAND_MAP[cmd_word](update, context)
            return

    # Küfür / Argo Filtresi
    lower_text = text.lower()
    if any(word in lower_text for word in BAD_WORDS):
        try:
            await update.message.delete()
            database.log_action(chat_id, "FILTER_DELETE", user.id, user.username or str(user.id), "Küfür/argo mesaj silindi.")
            await send_auto_delete_message(context, chat_id, f"🛡️ {user.full_name}, küfür/argo kullanımı yasaktır!")
        except Exception:
            pass

async def post_init(application):
    database.init_db()
    await application.bot.delete_webhook(drop_pending_updates=True)
    commands = [
        ("start", "🛡️ Paneli ve Komutları Göster"),
        ("welcome", "👋 Hoş geldin mesajı"),
        ("butonmetni", "📝 Buton metni"),
        ("butonlinki", "🔗 Buton linki"),
        ("duyuru", "📢 Duyuru gönder"),
        ("zamanla", "⏰ Periyodik paylaşım"),
        ("zamanladur", "⏹️ Periyodik durdur"),
        ("kilit", "🔒 Sohbet kilidi"),
        ("defol", "🚫 Banla"),
        ("undefol", "✅ Ban kaldır"),
        ("itaat", "🔇 Sustur"),
        ("unitaat", "🔊 Susturma kaldır"),
        ("warn", "⚠️ Uyarı ver"),
        ("unwarn", "♻️ Uyarı sıfırla")
    ]
    try:
        await application.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc in commands])
    except Exception:
        pass
    asyncio.create_task(start_web_server(application))
    logger.info("Bot ve Web Sunucusu başarıyla başlatıldı.")

def main():
    request_config = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = ApplicationBuilder().token(config.BOT_TOKEN).request(request_config).post_init(post_init).build()

    app.add_error_handler(global_error_handler)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    app.add_handler(MessageHandler(filters.ALL, master_message_router))

    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

import os
import sys
import logging
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, ContextTypes

# --- 1. LOGLAMA AYARLARI ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("RoseFedBot")

# --- 2. VERİTABANI (Geçici Bellek) ---
# Gerçek bir projede buralar SQLite veya PostgreSQL'e bağlanır
federations = {}       # fed_id -> creator_user_id (Hangi fedi kim kurdu)
chat_to_fed = {}       # chat_id -> fed_id (Hangi grup hangi fede bağlı)
fed_to_chats = {}      # fed_id -> [chat_id1, chat_id2] (Fed içindeki gruplar)
timed_automations = {} # chat_id -> duration

# --- 3. YARDIMCI FONKSİYONLAR ---
async def is_admin(chat, user_id) -> bool:
    if chat.type == "private": return True
    try:
        member = await chat.get_member(user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False

async def resolve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if context.args:
        arg = context.args[0].replace("@", "")
        try:
            if arg.isdigit(): return int(arg)
            member = await update.effective_chat.get_member(arg)
            return member.user
        except Exception:
            return None
    return None

# --- 4. FEDERASYON VE PORTAL MODÜLÜ ---
async def new_fed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Kullanım: `/fkur <Federasyon_Adı>`", parse_mode="Markdown")
        return
    
    fed_id = "_".join(context.args)
    if fed_id in federations:
        await update.message.reply_text("❌ Bu isimde bir federasyon zaten var.")
        return
        
    federations[fed_id] = user_id
    fed_to_chats[fed_id] = []
    await update.message.reply_text(f"✅ **{fed_id}** adlı federasyon başarıyla kuruldu!\nGrupları bağlamak için grubun içinde `/fbagla {fed_id}` yazın.", parse_mode="Markdown")

async def join_fed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not await is_admin(chat, user.id):
        return await update.message.reply_text("❌ Sadece yöneticiler grubu federasyona bağlayabilir.")
        
    if not context.args:
        return await update.message.reply_text("Kullanım: `/fbagla <Federasyon_Adı>`", parse_mode="Markdown")
        
    fed_id = context.args[0]
    if fed_id not in federations:
        return await update.message.reply_text("❌ Böyle bir federasyon bulunamadı.")
        
    chat_to_fed[chat.id] = fed_id
    if chat.id not in fed_to_chats[fed_id]:
        fed_to_chats[fed_id].append(chat.id)
        
    # Portal Butonu
    keyboard = [[InlineKeyboardButton("🌐 Federasyon Ağına Git", url=f"https://t.me/c/{str(chat.id).replace('-100', '')}/1")]]
    markup = InlineKeyboardMarkup(keyboard)
    
    msg = await update.message.reply_text(
        f"🔗 Bu grup başarıyla **{fed_id}** federasyonuna bağlandı!\nArtık bu gruptaki kurallar tüm federasyona uygulanabilir.",
        reply_markup=markup, parse_mode="Markdown"
    )
    try:
        await msg.pin()
    except:
        pass

async def fed_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_admin(chat, update.effective_user.id): return
    
    if chat.id not in chat_to_fed:
        return await update.message.reply_text("❌ Bu grup herhangi bir federasyona bağlı değil.")
        
    fed_id = chat_to_fed[chat.id]
    target_user = await resolve_user(update, context)
    
    if not target_user:
        return await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        
    target_id = target_user.id if hasattr(target_user, 'id') else target_user
    banned_chats = 0
    
    for c_id in fed_to_chats[fed_id]:
        try:
            await context.bot.ban_chat_member(chat_id=c_id, user_id=target_id)
            banned_chats += 1
        except Exception:
            continue
            
    await update.message.reply_text(f"🛡 **FedBan Raporu**\nKullanıcı, {fed_id} federasyonundaki **{banned_chats}** gruptan yasaklandı.", parse_mode="Markdown")

# --- 5. STANDART MODERASYON VE SÜRE ---
async def set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat, update.effective_user.id): return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Lütfen geçerli bir dakika girin: `/sure 15`", parse_mode="Markdown")
        
    timed_automations[update.effective_chat.id] = int(context.args[0])
    await update.message.reply_text(f"⏱ Grup otomasyon süresi **{context.args[0]} dakika** olarak ayarlandı.", parse_mode="Markdown")

async def simple_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat, update.effective_user.id): return
    target = await resolve_user(update, context)
    if target:
        target_id = target.id if hasattr(target, 'id') else target
        try:
            await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=target_id)
            await update.message.reply_text("🔨 Kullanıcı bu gruptan banlandı.")
        except Exception as e:
            await update.message.reply_text("❌ Yetkim yok veya kullanıcı bir yönetici.")

# --- 6. SAĞLIK SUNUCUSU (AIOHTTP - ASYNCIO UYUMLU) ---
async def health_handler(request):
    return web.Response(text="Bot is alive, webhook cleared, and polling perfectly on Render!")

# --- 7. ANA DÖNGÜ (MAIN) ---
async def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN bulunamadı!")
        sys.exit(1)

    application = Application.builder().token(TOKEN).build()

    # Komutları Yükle
    application.add_handler(CommandHandler("fkur", new_fed))
    application.add_handler(CommandHandler("fbagla", join_fed))
    application.add_handler(CommandHandler("fban", fed_ban))
    application.add_handler(CommandHandler("sure", set_duration))
    application.add_handler(CommandHandler("ban", simple_ban))

    # 1. Render için Async Web Sunucusunu Başlat (Döngüyü dondurmaz!)
    app = web.Application()
    app.router.add_get('/', health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ Render Web sunucusu {port} portunda başlatıldı.")

    # 2. Telegram Webhook Çakışmasını Zorla Temizle
    await application.bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Telegram Webhook bağları koparıldı, Polling'e geçiliyor.")

    # 3. Polling'i Başlat
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("🚀 Bot başarıyla çalışıyor! Mesajları dinlemeye başladı.")

    # 4. Asenkron döngüyü açık tut
    stop_signal = asyncio.Event()
    await stop_signal.wait()

if __name__ == "__main__":
    # Windows/Linux Asyncio Hatalarını Engelleme
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Asenkron ana fonksiyonu çalıştır
    asyncio.run(main())

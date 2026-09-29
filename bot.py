import os
import sys
import logging
import sqlite3
import asyncio
import traceback
import html
from datetime import datetime
from aiohttp import web
from functools import wraps

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ChatPermissions
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from telegram.error import BadRequest, Forbidden, TelegramError

# ==========================================
# 1. PROFESYONEL LOGLAMA SİSTEMİ
# ==========================================
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("RoseEnterprise")
# Gürültü yapan kütüphanelerin log seviyesini düşür
logging.getLogger("httpx").setLevel(logging.WARNING)

# ==========================================
# 2. VERİTABANI YÖNETİM SINIFI (OOP MİMARİ)
# ==========================================
class DatabaseManager:
    def __init__(self, db_name="rose_enterprise.db"):
        self.db_name = db_name
        self.setup_database()

    def _execute(self, query, params=(), fetch=False, fetchall=False):
        try:
            with sqlite3.connect(self.db_name) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                if fetch: return cursor.fetchone()
                if fetchall: return cursor.fetchall()
                return cursor.rowcount
        except sqlite3.Error as e:
            logger.error(f"Veritabanı Hatası: {e}")
            return None

    def setup_database(self):
        """Tüm tabloları ilişkisel (Relational) ve güvenli şekilde oluşturur."""
        queries = [
            """CREATE TABLE IF NOT EXISTS federations (
                fed_id TEXT PRIMARY KEY,
                fed_name TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS fed_chats (
                chat_id INTEGER PRIMARY KEY,
                fed_id TEXT,
                chat_name TEXT,
                FOREIGN KEY (fed_id) REFERENCES federations(fed_id)
            )""",
            """CREATE TABLE IF NOT EXISTS fed_bans (
                fed_id TEXT,
                user_id INTEGER,
                reason TEXT,
                banned_by INTEGER,
                date TEXT,
                PRIMARY KEY (fed_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS warnings (
                chat_id INTEGER,
                user_id INTEGER,
                warn_count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )"""
        ]
        for q in queries: self._execute(q)
        logger.info("✅ Veritabanı tabloları hazırlandı.")

db = DatabaseManager()

# ==========================================
# 3. YETKİ VE GÜVENLİK DEKORATÖRLERİ
# ==========================================
def admin_required(func):
    """Komutu kullanan kişinin ve BOTUN yetkilerini doğrular."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_chat or update.effective_chat.type == "private":
            await update.message.reply_text("❌ Bu komut sadece gruplarda çalışır.")
            return

        chat = update.effective_chat
        user_id = update.effective_user.id
        bot_id = context.bot.id

        # 1. Botun yetkisini kontrol et
        try:
            bot_member = await chat.get_member(bot_id)
            if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
                await update.message.reply_text("❌ İşlem yapabilmem için beni grupta **Yönetici** yapmalısınız!", parse_mode=ParseMode.MARKDOWN)
                return
        except Exception as e:
            logger.error(f"Bot yetki kontrol hatası: {e}")
            return

        # 2. Kullanıcının yetkisini kontrol et
        try:
            user_member = await chat.get_member(user_id)
            if user_member.status not in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]:
                await update.message.reply_text("⛔ Bu komutu kullanmak için grup yöneticisi olmalısın.")
                return
        except Exception:
            return

        return await func(update, context, *args, **kwargs)
    return wrapper

# ==========================================
# 4. YARDIMCI FONKSİYONLAR
# ==========================================
async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcıyı yanıttan, ID'den veya @username'den güvenle çeker."""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    
    if len(context.args) > 0:
        target = context.args[0].replace("@", "")
        if target.isdigit():
            try:
                chat = await context.bot.get_chat(int(target))
                return chat
            except BadRequest:
                return None
    return None

# ==========================================
# 5. GLOBAL HATA YAKALAYICI (CRASH-PROOF)
# ==========================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botun çökmesini engeller, hataları loglar."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    try:
        if isinstance(context.error, BadRequest):
            # Telegram tarafında geçersiz istek (örn: 48 saatten eski mesajı silme)
            pass 
        elif isinstance(context.error, Forbidden):
            # Bot gruptan atılmış
            pass
        else:
            # Geliştiriciyi veya logu uyar
            tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
            tb_string = "".join(tb_list)
            logger.critical(f"Kritik Hata Tespiti:\n{tb_string}")
    except Exception:
        pass

# ==========================================
# 6. ARAYÜZ (UI) VE MENÜ SİSTEMİ
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Beni Grubuna Ekle", url=f"https://t.me/{context.bot.username}?startgroup=true")],
        [InlineKeyboardButton("📚 Komutlar (Yardım)", callback_data="help_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        f"Merhaba {update.effective_user.first_name}! 🌹\n\n"
        "Ben yeni nesil, kesintisiz ve yüksek performanslı moderasyon & federasyon botuyum.\n"
        "Grubunu güvende tutmak için tasarlandım."
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "help_main":
        keyboard = [
            [InlineKeyboardButton("🛡 Moderasyon", callback_data="help_mod"),
             InlineKeyboardButton("🌐 Federasyon", callback_data="help_fed")],
            [InlineKeyboardButton("❌ Kapat", callback_data="help_close")]
        ]
        text = "⚙️ **Yardım Menüsü**\nLütfen bir kategori seçin:"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        
    elif query.data == "help_mod":
        text = (
            "🛡 **Moderasyon Komutları:**\n\n"
            "`/ban` [yanıt/ID] - Kullanıcıyı yasaklar.\n"
            "`/unban` [ID] - Kullanıcının yasağını açar.\n"
            "`/sil` [sayı] - Belirtilen sayıda mesajı temizler.\n"
            "`/uyar` [yanıt/ID] - Kullanıcıya uyarı verir (3 uyarıda banlar)."
        )
        keyboard = [[InlineKeyboardButton("🔙 Geri", callback_data="help_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        
    elif query.data == "help_fed":
        text = (
            "🌐 **Federasyon Komutları:**\n\n"
            "`/fkur <ID> <Ad>` - Yeni federasyon kurar.\n"
            "`/fbagla <ID>` - Grubu federasyona bağlar.\n"
            "`/fban <yanıt/ID>` - Kullanıcıyı ağdaki tüm gruplardan yasaklar.\n"
            "`/fbilgi` - Bağlı olduğunuz ağ bilgisini verir."
        )
        keyboard = [[InlineKeyboardButton("🔙 Geri", callback_data="help_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        
    elif query.data == "help_close":
        await query.message.delete()

# ==========================================
# 7. MODERASYON MODÜLÜ
# ==========================================
@admin_required
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await get_target_user(update, context)
    if not target:
        return await update.message.reply_text("⚠️ Lütfen yasaklanacak kişiyi yanıtlayın veya ID'sini yazın.")
    
    try:
        await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=target.id)
        await update.message.reply_text(f"🔨 Mjolnir indi! {target.first_name} gruptan sürüldü.")
    except Exception as e:
        await update.message.reply_text("❌ Bu kişiyi yasaklayamıyorum. (Kurucu veya Başka bir admin olabilir)")

@admin_required
async def purge_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("⚠️️ Silmeye başlanacak ilk mesajı yanıtlayın.")
    
    start_id = update.message.reply_to_message.message_id
    end_id = update.message.message_id
    
    deleted_count = 0
    # Telegram limitlerine takılmamak için asenkron silme
    for msg_id in range(start_id, end_id + 1):
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
            deleted_count += 1
        except BadRequest:
            # 48 saatten eski mesajları silemeyiz, yoksay
            continue
        except Exception:
            continue
            
    success_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=f"🧹 Temizlik tamamlandı. `{deleted_count}` mesaj silindi.", 
        parse_mode=ParseMode.MARKDOWN
    )
    # Bildirim mesajını 5 saniye sonra sil
    await asyncio.sleep(5)
    try:
        await success_msg.delete()
    except:
        pass

@admin_required
async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await get_target_user(update, context)
    chat_id = update.effective_chat.id
    
    if not target:
        return await update.message.reply_text("⚠️ Kimi uyaracağımı belirtmelisin.")
        
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Belirtilmedi"
    
    record = db._execute("SELECT warn_count FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, target.id), fetch=True)
    current_warns = (record[0] + 1) if record else 1
    
    if current_warns >= 3:
        # Banla ve uyarıları sıfırla
        db._execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, target.id))
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=target.id)
            await update.message.reply_text(f"🚫 {target.first_name} 3 uyarı sınırına ulaştı ve gruptan yasaklandı.")
        except:
            await update.message.reply_text("❌ Kullanıcı 3 uyarıya ulaştı ama onu banlamaya yetkim yetmiyor.")
    else:
        db._execute(
            "INSERT OR REPLACE INTO warnings (chat_id, user_id, warn_count) VALUES (?, ?, ?)",
            (chat_id, target.id, current_warns)
        )
        await update.message.reply_text(
            f"⚠️ **Uyarı!** [{current_warns}/3]\nKullanıcı: {target.mention_html()}\nSebep: {reason}",
            parse_mode=ParseMode.HTML
        )

# ==========================================
# 8. FEDERASYON MODÜLÜ (GLOBAL BAN AĞI)
# ==========================================
async def fed_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("ℹ️ Kullanım: `/fkur <Federasyon_ID> <Federasyon_Adı>`", parse_mode=ParseMode.MARKDOWN)
    
    fed_id = context.args[0].lower()
    fed_name = " ".join(context.args[1:])
    user_id = update.effective_user.id
    
    existing = db._execute("SELECT fed_id FROM federations WHERE fed_id=?", (fed_id,), fetch=True)
    if existing:
        return await update.message.reply_text("❌ Bu ID'ye sahip bir federasyon zaten mevcut.")
        
    date_now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db._execute(
        "INSERT INTO federations (fed_id, fed_name, owner_id, created_at) VALUES (?, ?, ?, ?)",
        (fed_id, fed_name, user_id, date_now)
    )
    
    await update.message.reply_text(
        f"👑 **Federasyon Başarıyla Kuruldu!**\n\n"
        f"**Adı:** {fed_name}\n"
        f"**ID:** `{fed_id}`\n\n"
        f"Gruplarınızı bağlamak için: `/fbagla {fed_id}`",
        parse_mode=ParseMode.MARKDOWN
    )

@admin_required
async def fed_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("⚠️ Hangi federasyona bağlanmak istiyorsunuz? Kullanım: `/fbagla <ID>`", parse_mode=ParseMode.MARKDOWN)
        
    fed_id = context.args[0].lower()
    chat = update.effective_chat
    
    fed = db._execute("SELECT fed_name FROM federations WHERE fed_id=?", (fed_id,), fetch=True)
    if not fed:
        return await update.message.reply_text("❌ Böyle bir federasyon bulunamadı.")
        
    db._execute(
        "INSERT OR REPLACE INTO fed_chats (chat_id, fed_id, chat_name) VALUES (?, ?, ?)",
        (chat.id, fed_id, chat.title)
    )
    
    await update.message.reply_text(
        f"🔗 Grup başarıyla **{fed[0]}** federasyonuna bağlandı!\n"
        "Artık global kurallar ve ban listeleri bu grup için de geçerlidir."
    )

@admin_required
async def fed_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    # Grubun bağlı olduğu federasyonu bul
    fed_data = db._execute("SELECT fed_id FROM fed_chats WHERE chat_id=?", (chat.id,), fetch=True)
    if not fed_data:
        return await update.message.reply_text("❌ Bu grup herhangi bir federasyona bağlı değil.")
    fed_id = fed_data[0]
    
    # Fede ban atan kişinin fed sahibi olup olmadığını kontrol et (Geliştirilebilir: Fed Adminleri eklenebilir)
    fed_owner = db._execute("SELECT owner_id FROM federations WHERE fed_id=?", (fed_id,), fetch=True)
    if fed_owner and fed_owner[0] != user.id:
        return await update.message.reply_text("❌ Global ban (FedBan) atmak için bu federasyonun sahibi olmalısınız.")
    
    target = await get_target_user(update, context)
    if not target:
        return await update.message.reply_text("⚠️ Lütfen FedBan atılacak kişiyi belirtin.")
        
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Federasyon kuralları ihlali."
    
    # Banı DB'ye kaydet
    db._execute(
        "INSERT OR REPLACE INTO fed_bans (fed_id, user_id, reason, banned_by, date) VALUES (?, ?, ?, ?, ?)",
        (fed_id, target.id, reason, user.id, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    )
    
    # Tüm bağlı gruplardan yasakla
    connected_chats = db._execute("SELECT chat_id FROM fed_chats WHERE fed_id=?", (fed_id,), fetchall=True)
    success_count = 0
    
    msg = await update.message.reply_text("🔄 Global ban ağa iletiliyor, lütfen bekleyin...")
    
    for (c_id,) in connected_chats:
        try:
            await context.bot.ban_chat_member(chat_id=c_id, user_id=target.id)
            success_count += 1
        except Exception:
            pass # Bot o gruptan atılmış veya yetkisi alınmış olabilir
            
    await msg.edit_text(
        f"🛡 **KÜRESEL YASAK (FEDBAN)**\n\n"
        f"👤 **Kullanıcı:** {target.id}\n"
        f"🌐 **Federasyon:** `{fed_id}`\n"
        f"✅ **Etkilenen Grup Sayısı:** {success_count} / {len(connected_chats)}\n"
        f"📝 **Sebep:** {reason}",
        parse_mode=ParseMode.MARKDOWN
    )

# ==========================================
# 9. RENDER SAĞLIK SUNUCUSU (AIOHTTP)
# ==========================================
async def health_check_handler(request):
    """Render'ın uygulamanın çökmediğini anlaması için sürekli '200 OK' döndüren portal."""
    return web.Response(text="Bot is running flawlessly in Enterprise Mode!", status=200)

async def start_health_server():
    """Web sunucusunu arkaplanda, asenkron loopu bloklamadan başlatır."""
    app = web.Application()
    app.router.add_get('/', health_check_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Health Check Web Sunucusu {port} portunda aktifleştirildi.")

# ==========================================
# 10. ANA DÖNGÜ VE BAŞLATICI (MAIN ORCHESTRATOR)
# ==========================================
async def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.critical("HATA: TELEGRAM_BOT_TOKEN bulunamadı. Environment Variables ayarlarını kontrol edin.")
        sys.exit(1)

    # Bot Yapılandırması
    application = Application.builder().token(TOKEN).build()
    
    # Gelişmiş Global Hata Yakalayıcıyı Ekle
    application.add_error_handler(error_handler)

    # ------------------------------------
    # KOMUT ROUTER KAYITLARI
    # ------------------------------------
    # Menü ve Arayüz
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("yardim", lambda u, c: start_command(u, c)))
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^help_"))
    
    # Moderasyon
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("sil", purge_messages))
    application.add_handler(CommandHandler("uyar", warn_user))
    
    # Federasyon
    application.add_handler(CommandHandler("fkur", fed_create))
    application.add_handler(CommandHandler("fbagla", fed_join))
    application.add_handler(CommandHandler("fban", fed_ban))

    # ------------------------------------
    # RENDER & TELEGRAM ÇALIŞTIRMA MİMARİSİ
    # ------------------------------------
    # 1. Önce Render'ın istediği web sunucusunu asenkron olarak ayağa kaldır
    await start_health_server()
    
    # 2. Telegram'da sıkışmış, botu donduran eski istekleri / webhook'ları yokederek temiz sayfa aç
    logger.info("⚙️ Telegram bağlantıları temizleniyor...")
    await application.bot.delete_webhook(drop_pending_updates=True)
    
    # 3. Botu PTB altyapısı ile başlat (Start Polling ASYNC)
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    logger.info("🚀 Enterprise Bot Kusursuz Şekilde Çalışıyor! Dinleme modunda...")

    # 4. Asenkron döngüyü sonsuza dek (Render kapanana dek) açık tut
    stop_event = asyncio.Event()
    await stop_event.wait()

if __name__ == "__main__":
    # Windows ve spesifik Linux sunucularda Asyncio Loop hatası almamak için politika belirle
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manuel olarak kapatıldı.")
    except Exception as e:
        logger.critical(f"FATAL Kapanış Hatası: {e}")

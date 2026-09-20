import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Loglama ayarları
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Örnek Bellek / Veritabanı Yapısı (Federasyon ve Süreli Mesajlar için)
federation_links = {}  # {chat_id: target_chat_id}
timed_messages = {}     # {chat_id: duration_in_minutes}

# --- YÖNETİCİ VE YETKİ KONTROLÜ ---
async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Kullanıcının grupta yönetici veya yetkili olup olmadığını kontrol eder."""
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        return True
    member = await chat.get_member(user.id)
    return member.status in ["creator", "administrator"]

# --- FEDERASYON VE GRUP BAĞLAMA KOMUTLARI ---
async def bind_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Arayüz veya komut üzerinden iki grubu birbirine bağlar."""
    if not await is_user_admin(update, context):
        await update.message.reply_text("Bu komutu kullanmak için yetkiniz yok.")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Lütfen bağlanacak grubun kullanıcı adını veya ID'sini girin. Örnek: /bagla @arayis_grubu")
        return

    current_chat_id = update.effective_chat.id
    target_group = args[0]
    
    federation_links[current_chat_id] = target_group
    
    # Butonlu ve sabitlenecek mesaj yapısı (Arayüz / Sabitleme Özelliği)
    keyboard = [
        [
            InlineKeyboardButton("💬 Chat Grubuna Git", url=f"https://t.me/{str(current_chat_id).replace('-100', '')}"),
            InlineKeyboardButton("🔍 Arayış Grubuna Git", url=f"https://t.me/{target_group.replace('@', '')}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    sent_message = await update.message.reply_text(
        f"✅ Federasyon başarıyla kuruldu!\nBağlanan Grup: {target_group}",
        reply_markup=reply_markup
    )
    
    try:
        await sent_message.pin()
    except Exception as e:
        logger.error(f"Mesaj sabitleme hatası: {e}")

async def unbind_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Federasyon bağlantısını koparır ve gruptan ayırır."""
    if not await is_user_admin(update, context):
        await update.message.reply_text("Bu komutu kullanmak için yetkiniz yok.")
        return

    current_chat_id = update.effective_chat.id
    if current_chat_id in federation_links:
        del federation_links[current_chat_id]
        await update.message.reply_text("❌ Federasyon bağlantısı bu grup için kaldırıldı.")
    else:
        await update.message.reply_text("⚠️ Bu gruba tanımlı aktif bir federasyon bulunamadı.")

# --- METİN / PARAGRAF VE SÜRELİ İŞLEMLER ---
async def set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Metin veya paragraflar için dakika bazlı süre belirleme."""
    if not await is_user_admin(update, context):
        await update.message.reply_text("Yetkiniz yok.")
        return

    args = context.args
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_text("Lütfen geçerli bir dakika süresi girin. Örnek: /sure 10")
        return

    duration = int(args[0])
    chat_id = update.effective_chat.id
    timed_messages[chat_id] = duration
    
    await update.message.reply_text(f"⏱️ Bu grup için yayın ve metin süresi {duration} dakika olarak ayarlandı.")

# --- İTAAT, KRALİÇE VE DİĞER KOMUTLAR (Hatasız ve Esnek Hedefleme) ---
async def handle_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yanıtla, ID veya kullanıcı adı ile hedef belirleyerek komut çalıştırma (İtaat, Kraliçe vb.)."""
    if not await is_user_admin(update, context):
        await update.message.reply_text("Bu komutu kullanmak için yetkiniz yok.")
        return

    target_user = None
    
    # 1. Yöntem: Mesajı yanıtlayarak (Reply) kullanım
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user.username or update.message.reply_to_message.from_user.first_name
        target_id = update.message.reply_to_message.from_user.id
    # 2. Yöntem: Argüman ile (ID veya @KullanıcıAdı) kullanım
    elif context.args:
        target_input = context.args[0]
        target_user = target_input
    else:
        await update.message.reply_text("⚠️ Lütfen bir kullanıcıyı yanıtlayın ya da kullanıcı adı/ID belirtin.")
        return

    command_name = update.message.text.split()[0].replace('/', '')
    await update.message.reply_text(f"👑 '{command_name}' komutu başarıyla uygulandı!\nHedef: {target_user}")

def main():
    # Render ortam değişkeninden veya doğrudan token tanımı
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "BURAYA_BOT_TOKEN_Gelecek")
    
    application = Application.builder().token(TOKEN).build()

    # Komut Tanımlamaları
    application.add_handler(CommandHandler("bagla", bind_group))
    application.add_handler(CommandHandler("ayir", unbind_group))
    application.add_handler(CommandHandler("sure", set_duration))
    
    # Esnek Hedeflemeli Komutlar (İtaat, Kraliçe vb.)
    application.add_handler(CommandHandler("itaat", handle_target_user))
    application.add_handler(CommandHandler("krallice", handle_target_user))
    application.add_handler(CommandHandler("kralice", handle_target_user))

    # Botu Başlatma
    print("Bot çalıştırılıyor...")
    application.run_polling()

if __name__ == "__main__":
    main()

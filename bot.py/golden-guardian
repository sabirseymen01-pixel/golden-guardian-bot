import logging
from datetime import timedelta
import sqlite3
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- RENDER WEB SERVİS İÇİN MİNİ HTTP SUNUCUSU (Çökmesini Önler) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Golden Guardian Bot is active and running!")

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# --- BOT TOKEN VE YAPILANDIRMA ---
TOKEN = "8841883539:AAF3FHH_Ibdk4ypEcOusRaLwOyTEq6rpwUw"

# --- OTOMATİK MESAJ TEMİZLEME FONKSİYONU (10 Saniye Garanti) ---
async def mesaj_temizle_gorevi(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    message_id = job_data["message_id"]
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

def bot_mesajini_sil_planla(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, saniye: float = 10.0):
    context.job_queue.run_once(
        mesaj_temizle_gorevi,
        saniye,
        data={"chat_id": chat_id, "message_id": message_id}
    )

# --- LOGLAMA YAPILANDIRMASI ---
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("GoldenGuardianBot")

# --- VERİTABANI YÖNETİMİ (SQLite) ---
def db_kur():
    conn = sqlite3.connect("guardian_pro.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS uyarilar (
            user_id INTEGER PRIMARY KEY,
            uyari_sayisi INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_loglar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zaman TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            islem_turu TEXT,
            detay TEXT
        )
    """)
    conn.commit()
    conn.close()

db_kur()

def log_kaydet(islem_turu, detay):
    conn = sqlite3.connect("guardian_pro.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bot_loglar (islem_turu, detay) VALUES (?, ?)", (islem_turu, detay))
    conn.commit()
    conn.close()
    logger.info(f"[{islem_turu}] {detay}")

def uyari_arttir(user_id):
    conn = sqlite3.connect("guardian_pro.db")
    cursor = conn.cursor()
    cursor.execute("SELECT uyari_sayisi FROM uyarilar WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if res is None:
        cursor.execute("INSERT INTO uyarilar (user_id, uyari_sayisi) VALUES (?, ?)", (user_id, 1))
        yeni_sayi = 1
    else:
        yeni_sayi = res[0] + 1
        cursor.execute("UPDATE uyarilar SET uyari_sayisi = ? WHERE user_id = ?", (yeni_sayi, user_id))
    conn.commit()
    conn.close()
    return yeni_sayi

def uyari_sifirla(user_id):
    conn = sqlite3.connect("guardian_pro.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM uyarilar WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# --- YASAKLI KELİME LİSTESİ ---
YASAKLI_KELIMELER = [
    "amina", "orospu", "o.ç", "piç", "sik", "sikerim",
    "sikik", "got", "götveren", "amse", "kahpe", "karshane",
    "salak", "gerizekalı", "aptal", "ezik", "haysiyetsiz",
    "discord.gg/", "http://", "https://", "reklam", "hack"
]

# --- YÖNETİCİ KONTROLÜ ---
async def yonetici_mi(update: Update, user_id: int) -> bool:
    try:
        chat = update.effective_chat
        member = await chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

# --- HEDEF KULLANICI ÇÖZÜMLEME ---
async def hedef_kullanici_bul(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    hedef_id = None
    aciklama = ""

    if update.message.reply_to_message:
        hedef_user = update.message.reply_to_message.from_user
        return hedef_user.id, f"Yanıtlanan kullanıcı ({hedef_user.full_name} - {hedef_user.id})"

    if context.args:
        girdi = context.args[0]
        aciklama = girdi
        try:
            if girdi.startswith("@"):
                try:
                    chat_member = await context.bot.get_chat_member(chat.id, girdi)
                    hedef_id = chat_member.user.id
                except Exception:
                    user_info = await context.bot.get_chat(girdi)
                    hedef_id = user_info.id
            else:
                hedef_id = int(girdi)
        except Exception:
            hedef_id = None

    return hedef_id, aciklama

# --- KÜFÜR VE İHLAL DENETİMİ ---
async def mesaj_denetimi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.message.from_user
    chat = update.effective_chat
    
    if await yonetici_mi(update, user.id):
        return

    text = update.message.text.lower()
    for kelime in YASAKLI_KELIMELER:
        if kelime in text:
            try:
                await update.message.delete()
                toplam_uyari = uyari_arttir(user.id)
                log_kaydet("KÜFÜR/İHLAL", f"Kullanıcı: {user.full_name} ({user.id}) | Kelime: {kelime}")

                await chat.restrict_member(
                    user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=timedelta(minutes=10)
                )
                
                keyboard = [[InlineKeyboardButton("⚖️ İstatistikleri Gör", callback_data="menu_istatistik")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                uyari_msj = await context.bot.send_message(
                    chat_id=chat.id,
                    text=f"⚠️ {user.mention_html()}, yasaklı kelime kullandığı için 10 dakika süreyle susturuldu! (Toplam İhlal: {toplam_uyari})",
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                bot_mesajini_sil_planla(context, chat.id, uyari_msj.message_id, 10.0)
            except Exception as e:
                log_kaydet("HATA", f"Mesaj denetim hatası: {e}")
            break

# --- KOMUT: /start ---
async def start_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_kaydet("KOMUT", f"/start komutu çalıştırıldı: {user.full_name} ({user.id})")
    
    try:
        await update.message.delete()
    except Exception:
        pass

    keyboard = [
        [InlineKeyboardButton("📊 Bot İstatistikleri", callback_data="menu_istatistik")],
        [InlineKeyboardButton("📜 Son Log Kayıtları", callback_data="menu_loglar")],
        [InlineKeyboardButton("🛠️ Yardım & Komutlar", callback_data="menu_yardim")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msj = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🤖 **Golden Guardian Yönetim Paneline Hoş Geldiniz, {user.mention_html()}!**\n\n"
             "Bu bot grubunuzu korumak, küfürleri engellemek ve moderasyon işlemlerini "
             "yürütmek için aktif durumdadır.\n\n"
             "Tüm bot yanıtları **10 saniye içinde** otomatik olarak silinir.",
        parse_mode="HTML",
        reply_markup=reply_markup
    )
    bot_mesajini_sil_planla(context, update.effective_chat.id, msj.message_id, 10.0)

# --- İNTERAKTİF BUTON YÖNETİCİSİ ---
async def buton_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "menu_istatistik":
        conn = sqlite3.connect("guardian_pro.db")
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(uyari_sayisi), COUNT(user_id) FROM uyarilar")
        res = cursor.fetchone()
        conn.close()

        toplam_ihlal = res[0] if res and res[0] else 0
        toplam_cezali = res[1] if res and res[1] else 0

        metin = (
            "📊 **Golden Guardian - İstatistik Paneli**\n\n"
            f"• Toplam Cezalı/Uyarılı Üye: `{toplam_cezali}`\n"
            f"• Toplam Engellenen İhlal: `{toplam_ihlal}`\n"
            "• Durum: `Stabil ve Aktif`"
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_loglar":
        conn = sqlite3.connect("guardian_pro.db")
        cursor = conn.cursor()
        cursor.execute("SELECT zaman, islem_turu, detay FROM bot_loglar ORDER BY id DESC LIMIT 5")
        kayitlar = cursor.fetchall()
        conn.close()

        metin = "📜 **Bot İçerisindeki Son 5 Log Kaydı**\n\n"
        if not kayitlar:
            metin += "Henüz kayıtlı bir işlem bulunmuyor."
        else:
            for k in kayitlar:
                metin += f"⏱ `{k[0]}`\n🔹 **{k[1]}**: {k[2]}\n\n"

        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_yardim":
        metin = (
            "🛠️ **Golden Guardian Kontrol & Komut Menüsü**\n\n"
            "• `/defol` : Kullanıcıyı gruptan banlar (Yanıtla / ID / @etiket).\n"
            "• `/itaat` : Kullanıcıyı 10 dk susturur (Yanıtla / ID / @etiket).\n"
            "• `/biat` : Kullanıcının banını kaldırır (Yanıtla / ID / @etiket).\n"
            "• `/kralice` : Kullanıcının mutesini kaldırır (Yanıtla / ID / @etiket).\n"
            "• `/istatistik` : Genel raporu gösterir.\n"
            "• *Not:* Tüm bot yanıtları **10 saniye sonra** silinir."
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_ana":
        keyboard = [
            [InlineKeyboardButton("📊 Bot İstatistikleri", callback_data="menu_istatistik")],
            [InlineKeyboardButton("📜 Son Log Kayıtları", callback_data="menu_loglar")],
            [InlineKeyboardButton("🛠️ Yardım & Komutlar", callback_data="menu_yardim")]
        ]
        await query.edit_message_text(
            text="🤖 **Golden Guardian Yönetim Paneli**\n\nİşlem seçiniz:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- KOMUTLAR ---
async def defol_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass
    if not await yonetici_mi(update, update.message.from_user.id):
        return
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id:
        return
    try:
        await chat.ban_member(hedef_id)
        log_kaydet("BANLAMA (/defol)", f"Hedef ID: {hedef_id} ({aciklama}) gruptan banlandı.")
        msj = await context.bot.send_message(chat.id, f"🔨 Emir yerine getirildi! Hedef kullanıcı gruptan banlandı (`{aciklama}`).", parse_mode="Markdown")
        bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
    except Exception as e:
        log_kaydet("HATA", f"Banlama hatası: {e}")

async def itaat_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass
    if not await yonetici_mi(update, update.message.from_user.id):
        return
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id:
        return
    try:
        await chat.restrict_member(
            hedef_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=timedelta(minutes=10)
        )
        uyari_arttir(hedef_id)
        log_kaydet("SUSTURMA (/itaat)", f"Hedef ID: {hedef_id} ({aciklama}) 10 dakika susturuldu.")
        msj = await context.bot.send_message(chat.id, f"🤐 İtaat sağlandı! Hedef kullanıcı 10 dakika süreyle susturuldu (`{aciklama}`).", parse_mode="Markdown")
        bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
    except Exception as e:
        log_kaydet("HATA", f"Susturma hatası: {e}")

async def kralice_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass
    if not await yonetici_mi(update, update.message.from_user.id):
        return
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id:
        return
    try:
        await chat.unban_member(hedef_id, only_if_banned=False)
        uyari_sifirla(hedef_id)
        log_kaydet("MUTE KALDIRMA (/kralice)", f"Hedef ID: {hedef_id} ({aciklama}) mutesi kaldırıldı.")
        msj = await context.bot.send_message(chat.id, f"✨ Kraliçenin fermanıyla hedefin susturulması kaldırıldı (`{aciklama}`).", parse_mode="Markdown")
        bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
    except Exception as e:
        log_kaydet("HATA", f"Mute kaldırma hatası: {e}")

async def biat_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass
    if not await yonetici_mi(update, update.message.from_user.id):
        return
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id:
        return
    try:
        await chat.unban_member(hedef_id)
        log_kaydet("BAN KALDIRMA (/biat)", f"Hedef ID: {hedef_id} ({aciklama}) banı kaldırıldı.")
        msj = await context.bot.send_message(chat.id, f"🕊️ Biat kabul edildi! Hedef kullanıcının yasağı başarıyla kaldırıldı (`{aciklama}`).", parse_mode="Markdown")
        bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
    except Exception as e:
        log_kaydet("HATA", f"Ban kaldırma hatası: {e}")

async def istatistik_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass
    conn = sqlite3.connect("guardian_pro.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(uyari_sayisi), COUNT(user_id) FROM uyarilar")
    res = cursor.fetchone()
    conn.close()
    toplam_ihlal = res[0] if res and res[0] else 0
    toplam_cezali = res[1] if res and res[1] else 0
    metin = (
        "📊 **Golden Guardian - İstatistik Raporu**\n\n"
        f"• Toplam Cezalı/Uyarılı Üye: `{toplam_cezali}`\n"
        f"• Toplam Engellenen İhlal: `{toplam_ihlal}`\n"
        "• Durum: `Stabil ve Aktif`"
    )
    msj = await context.bot.send_message(chat.id, metin, parse_mode="Markdown")
    bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)

# --- ANA UYGULAMA BAŞLATICI ---
def main():
    # Mini web sunucusunu arka planda başlat (Render'ı kandırmak için)
    server_thread = threading.Thread(target=start_dummy_server, daemon=True)
    server_thread.start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_komutu))
    app.add_handler(CommandHandler("defol", defol_komutu))
    app.add_handler(CommandHandler("itaat", itaat_komutu))
    app.add_handler(CommandHandler("kralice", kralice_komutu))
    app.add_handler(CommandHandler("biat", biat_komutu))
    app.add_handler(CommandHandler("istatistik", istatistik_komutu))
    
    app.add_handler(CallbackQueryHandler(buton_yoneticisi))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), mesaj_denetimi))

    log_kaydet("SİSTEM", "Golden Guardian botu web sunucusu eşliğinde başarıyla başlatıldı.")
    app.run_polling()

if __name__ == "__main__":
    main()

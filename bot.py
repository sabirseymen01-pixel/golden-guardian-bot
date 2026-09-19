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

# --- RENDER PORT VE SAĞLIK KONTROL SUNUCUSU ---
PORT = int(os.environ.get("PORT", 10000))

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Golden Guardian Pro Ultimate Bot is active and running!")
    def log_message(self, format, *args):
        pass

def start_dummy_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception:
        pass

TOKEN = os.environ.get("BOT_TOKEN")

# --- OTOMATİK MESAJ TEMİZLEME ---
async def mesaj_temizle_gorevi(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    try:
        await context.bot.delete_message(chat_id=job_data["chat_id"], message_id=job_data["message_id"])
    except Exception:
        pass

def bot_mesajini_sil_planla(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, saniye: float = 10.0):
    context.job_queue.run_once(
        mesaj_temizle_gorevi,
        saniye,
        data={"chat_id": chat_id, "message_id": message_id}
    )

# --- LOGLAMA ---
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("GoldenGuardianBot")

# --- VERİTABANI YÖNETİMİ ---
def db_kur():
    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fed_banlar (
                user_id INTEGER PRIMARY KEY,
                sebep TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS federasyon_gruplar (
                chat_id INTEGER PRIMARY KEY,
                grup_adi TEXT,
                grup_linki TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS zamanli_icerikler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                icerik_tipi TEXT,
                icerik_metni TEXT,
                sure_saniye INTEGER
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Veritabanı kurulum hatası: {e}")

db_kur()

def log_kaydet(islem_turu, detay):
    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO bot_loglar (islem_turu, detay) VALUES (?, ?)", (islem_turu, detay))
        conn.commit()
        conn.close()
    except Exception:
        pass
    logger.info(f"[{islem_turu}] {detay}")

def uyari_arttir(user_id):
    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
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
    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM uyarilar WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

YASAKLI_KELIMELER = [
    "amina", "orospu", "o.ç", "piç", "sik", "sikerim",
    "sikik", "got", "götveren", "amse", "kahpe", "karshane",
    "salak", "gerizekalı", "aptal", "ezik", "haysiyetsiz",
    "discord.gg/", "http://", "https://", "reklam", "hack"
]

async def yonetici_mi(update: Update, user_id: int) -> bool:
    try:
        chat = update.effective_chat
        member = await chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

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

# --- KÜFÜR VE FEDERASYON KONTROLÜ ---
async def mesaj_denetimi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.message.from_user
    chat = update.effective_chat

    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM fed_banlar WHERE user_id = ?", (user.id,))
        fed_yasakli = cursor.fetchone()
        conn.close()

        if fed_yasakli:
            await update.message.delete()
            await chat.ban_member(user.id)
            return
    except Exception:
        pass

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

# --- ANA MENÜ VE ARAYÜZ ---
async def start_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    log_kaydet("KOMUT", f"/start komutu çalıştırıldı: {user.full_name} ({user.id})")
    
    try:
        await update.message.delete()
    except Exception:
        pass

    grup_linki = f"t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO federasyon_gruplar (chat_id, grup_adi, grup_linki) VALUES (?, ?, ?)", 
                       (chat.id, chat.title or "Federasyon Grubu", grup_linki))
        conn.commit()
        conn.close()
    except Exception:
        pass

    keyboard = [
        [InlineKeyboardButton("📊 Bot İstatistikleri", callback_data="menu_istatistik")],
        [InlineKeyboardButton("🌐 Federasyon & Grup Geçişleri", callback_data="menu_federasyon")],
        [InlineKeyboardButton("⏱️ Zamanlı Medya/Metin Yönetimi", callback_data="menu_zamanlayici")],
        [InlineKeyboardButton("📜 Son Log Kayıtları", callback_data="menu_loglar")],
        [InlineKeyboardButton("🛠️ Yardım & Komutlar", callback_data="menu_yardim")],
        [InlineKeyboardButton("📌 Bu Paneli Sabitle", callback_data="menu_sabitle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msj = await context.bot.send_message(
        chat_id=chat.id,
        text=f"🤖 **Golden Guardian Pro & Federasyon Paneli**\n\n"
             f"Hoş Geldiniz, {user.mention_html()}!\n"
             "Bu arayüz üzerinden federasyon grupları arasında geçiş yapabilir, zamanlı içerikleri yönetebilir ve grubu koruyabilirsiniz.",
        parse_mode="HTML",
        reply_markup=reply_markup
    )
    bot_mesajini_sil_planla(context, chat.id, msj.message_id, 15.0)

# --- İNTERAKTİF BUTON YÖNETİCİSİ (effective_user güncellendi) ---
async def buton_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat = update.effective_chat
    user = update.effective_user

    if data == "menu_istatistik":
        try:
            conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(uyari_sayisi), COUNT(user_id) FROM uyarilar")
            res = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM fed_banlar")
            fed_res = cursor.fetchone()
            conn.close()
        except Exception:
            res, fed_res = (0, 0), (0,)

        toplam_ihlal = res[0] if res and res[0] else 0
        toplam_cezali = res[1] if res and res[1] else 0
        toplam_fed_ban = fed_res[0] if fed_res else 0

        metin = (
            "📊 **Golden Guardian - İstatistik Paneli**\n\n"
            f"• Toplam Cezalı/Uyarılı Üye: `{toplam_cezali}`\n"
            f"• Toplam Engellenen İhlal: `{toplam_ihlal}`\n"
            f"• Küresel Federasyon Banlı (İdam): `{toplam_fed_ban}`\n"
            "• Durum: `Stabil, Güvenli ve Aktif`"
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_federasyon":
        try:
            conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT grup_adi, grup_linki FROM federasyon_gruplar")
            gruplar = cursor.fetchall()
            conn.close()
        except Exception:
            gruplar = []

        metin = "🌐 **Federasyon & Gruplar Arası Geçiş Paneli**\n\nSisteme bağlı federasyon ağındaki diğer gruplar:\n\n"
        keyboard = []
        if not gruplar:
            metin += "Henüz kayıtlı başka grup bulunmuyor."
        else:
            for g in gruplar:
                g_adi, g_link = g[0], g[1]
                if g_link and ("t.me" in g_link or "http" in g_link):
                    keyboard.append([InlineKeyboardButton(f"🔗 {g_adi}", url=g_link)])
                else:
                    metin += f"• **{g_adi}**\n"

        keyboard.append([InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")])
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_zamanlayici":
        metin = (
            "⏱️ **Zamanlı Medya ve Metin Yönetimi**\n\n"
            "Botun belirli aralıklarla grup içine otomatik duyuru veya medya göndermesini sağlamak için veritabanı altyapısı hazırdır."
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_loglar":
        try:
            conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT zaman, islem_turu, detay FROM bot_loglar ORDER BY id DESC LIMIT 5")
            kayitlar = cursor.fetchall()
            conn.close()
        except Exception:
            kayitlar = []

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
            "🛠️ **Golden Guardian Pro - Komut Menüsü**\n\n"
            "• `/defol` : Kullanıcıyı yerel gruptan banlar.\n"
            "• `/itaat` : Kullanıcıyı 10 dk susturur.\n"
            "• `/kralice` : Kullanıcının mutesini kaldırır (Asla banlamaz/atmaz!).\n"
            "• `/biat` : Yerel yasağı kaldırır.\n"
            "• `/idam` : Kullanıcıyı federasyondaki TÜM gruplardan küresel olarak banlar.\n"
            "• `/genelaf` : Küresel federasyon banını kaldırır.\n"
            "• `/istatistik` : Genel raporu gösterir."
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_sabitle":
        if user and not await yonetici_mi(update, user.id):
            await query.answer("Bu işlem için yönetici olmalısınız!", show_alert=True)
            return
        try:
            await query.message.pin()
            await query.answer("Panel başarıyla gruba sabitlendi!", show_alert=True)
        except Exception:
            await query.answer("Panel sabitlenemedi (Yönetici yetkisi eksik olabilir).", show_alert=True)

    elif data == "menu_ana":
        keyboard = [
            [InlineKeyboardButton("📊 Bot İstatistikleri", callback_data="menu_istatistik")],
            [InlineKeyboardButton("🌐 Federasyon & Grup Geçişleri", callback_data="menu_federasyon")],
            [InlineKeyboardButton("⏱️ Zamanlı Medya/Metin Yönetimi", callback_data="menu_zamanlayici")],
            [InlineKeyboardButton("📜 Son Log Kayıtları", callback_data="menu_loglar")],
            [InlineKeyboardButton("🛠️ Yardım & Komutlar", callback_data="menu_yardim")],
            [InlineKeyboardButton("📌 Bu Paneli Sabitle", callback_data="menu_sabitle")]
        ]
        await query.edit_message_text(
            text="🤖 **Golden Guardian Pro & Federasyon Paneli**\n\nİşlem seçiniz:",
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

# --- KRALİÇE: Yalnızca mute (susturma) kaldırır, asla atmaz ---
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
        await chat.restrict_member(
            hedef_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
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

# --- FEDERASYON: /idam (Küresel Genel Ban) ---
async def idam_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO fed_banlar (user_id, sebep) VALUES (?, ?)", (hedef_id, "Federasyon İdam Kararı"))
        conn.commit()
        conn.close()
    except Exception:
        pass

    try:
        await chat.ban_member(hedef_id)
        log_kaydet("FEDERASYON İDAM (/idam)", f"Kullanıcı Küresel Banlandı: {hedef_id} ({aciklama})")
        msj = await context.bot.send_message(chat.id, f"⚖️ **KÜRESEL İDAM KARARI!** Hedef kullanıcı federasyona bağlı tüm sistemlerden idam edildi (`{aciklama}`).", parse_mode="Markdown")
        bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
    except Exception as e:
        log_kaydet("HATA", f"İdam hatası: {e}")

# --- FEDERASYON: /genelaf (Küresel Ban Kaldırma) ---
async def genelaf_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM fed_banlar WHERE user_id = ?", (hedef_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

    try:
        await chat.unban_member(hedef_id)
        log_kaydet("FEDERASYON GENELAF (/genelaf)", f"Küresel Af Uygulandı: {hedef_id} ({aciklama})")
        msj = await context.bot.send_message(chat.id, f"🕊️ **GENEL AF ÇIKTI!** Hedef kullanıcının federasyon küresel yasağı kaldırıldı (`{aciklama}`).", parse_mode="Markdown")
        bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
    except Exception as e:
        log_kaydet("HATA", f"Genel af hatası: {e}")

async def istatistik_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass
    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(uyari_sayisi), COUNT(user_id) FROM uyarilar")
        res = cursor.fetchone()
        conn.close()
    except Exception:
        res = (0, 0)
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

# --- POST_INIT: BOT BAŞLARKEN ESKİ TÜNELLERİ TEMİZLEME KANCASI ---
async def post_init(application):
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Telegram sunucu bağlantı tünelleri başarıyla sıfırlandı ve senkronize edildi.")
    except Exception as e:
        logger.error(f"Post-init kilit temizleme uyarısı: {e}")

# --- ANA UYGULAMA VE ÇAKIŞMASIZ BAŞLATICI ---
def main():
    if not TOKEN:
        logger.error("HATA: BOT_TOKEN çevresel değişkeni bulunamadı! Render panelinde BOT_TOKEN ekleyin.")
        return

    server_thread = threading.Thread(target=start_dummy_server, daemon=True)
    server_thread.start()

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_komutu))
    app.add_handler(CommandHandler("defol", defol_komutu))
    app.add_handler(CommandHandler("itaat", itaat_komutu))
    app.add_handler(CommandHandler("kralice", kralice_komutu))
    app.add_handler(CommandHandler("biat", biat_komutu))
    app.add_handler(CommandHandler("idam", idam_komutu))
    app.add_handler(CommandHandler("genelaf", genelaf_komutu))
    app.add_handler(CommandHandler("istatistik", istatistik_komutu))
    
    app.add_handler(CallbackQueryHandler(buton_yoneticisi))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), mesaj_denetimi))

    logger.info("Golden Guardian Pro hatasız yapılandırma ile başlatılıyor...")
    
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

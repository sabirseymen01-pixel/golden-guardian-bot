import logging
from datetime import timedelta
import sqlite3
import os
import threading
import html
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram Kütüphaneleri
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, MessageEntity
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        CallbackQueryHandler,
        filters,
    )
except ImportError as e:
    print(f"KRİTİK HATA: python-telegram-bot[job-queue] kütüphanesi eksik! Lütfen requirements.txt dosyanızı kontrol edin. Detay: {e}")
    exit(1)

# --- PORT VE RENDER SAĞLIK SUNUCUSU ---
PORT = int(os.environ.get("PORT", 10000))

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Golden Guardian Pro V2 Ultimate is Running Perfectly!")
    def log_message(self, format, *args):
        pass

def start_dummy_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Sağlık sunucusu hatası: {e}")

TOKEN = os.environ.get("BOT_TOKEN")

# --- LOGLAMA (Artık Hataları Gizlemeyeceğiz) ---
logging.basicConfig(format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("GoldenGuardian")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Sistem Hatası Yakalandı: {context.error}", exc_info=context.error)

# --- VERİTABANI YÖNETİMİ (Thread-Safe Lock ile Kilitlenmeler Önlendi) ---
db_lock = threading.Lock()
DB_NAME = "guardian_pro.db"

def db_execute(query, params=(), fetchone=False, fetchall=False, commit=True):
    """Tüm veritabanı işlemlerini tek bir güvenli kanaldan sırayla geçirir."""
    with db_lock:
        try:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute(query, params)
            res = None
            if fetchone:
                res = cursor.fetchone()
            elif fetchall:
                res = cursor.fetchall()
            if commit:
                conn.commit()
            conn.close()
            return res
        except Exception as e:
            logger.error(f"Veritabanı İşlem Hatası -> Query: {query} | Hata: {e}")
            return None

def db_kur():
    db_execute("CREATE TABLE IF NOT EXISTS uyarilar (user_id INTEGER PRIMARY KEY, uyari_sayisi INTEGER DEFAULT 0)")
    db_execute("CREATE TABLE IF NOT EXISTS bot_loglar (id INTEGER PRIMARY KEY AUTOINCREMENT, zaman TIMESTAMP DEFAULT CURRENT_TIMESTAMP, islem_turu TEXT, detay TEXT)")
    db_execute("CREATE TABLE IF NOT EXISTS fed_banlar (user_id INTEGER PRIMARY KEY, sebep TEXT)")
    db_execute("CREATE TABLE IF NOT EXISTS federasyon_gruplar (chat_id INTEGER PRIMARY KEY, grup_adi TEXT, grup_linki TEXT)")
    db_execute("CREATE TABLE IF NOT EXISTS zamanli_ayar (chat_id INTEGER PRIMARY KEY, sure_dakika INTEGER DEFAULT 30, aktif_durum TEXT DEFAULT 'AKTİF')")
    db_execute("CREATE TABLE IF NOT EXISTS yayin_listesi (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, tip TEXT, icerik TEXT, aciklama TEXT)")
    logger.info("Veritabanı tabloları başarıyla senkronize edildi.")

db_kur()

def log_kaydet(islem_turu, detay):
    db_execute("INSERT INTO bot_loglar (islem_turu, detay) VALUES (?, ?)", (islem_turu, detay))
    logger.info(f"[{islem_turu}] {detay}")

# --- YARDIMCI FONKSİYONLAR ---
async def yonetici_mi(update: Update, user_id: int) -> bool:
    try:
        chat = update.effective_chat
        member = await chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"Yönetici kontrol hatası: {e}")
        return False

async def bot_mesajini_sil_planla(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, saniye: float = 15.0):
    async def sil_gorevi(ctx: ContextTypes.DEFAULT_TYPE):
        try:
            await ctx.bot.delete_message(chat_id=ctx.job.data["chat_id"], message_id=ctx.job.data["message_id"])
        except Exception:
            pass
    try:
        if context.job_queue:
            context.job_queue.run_once(sil_gorevi, saniye, data={"chat_id": chat_id, "message_id": message_id})
    except Exception as e:
        logger.error(f"Mesaj silme planlama hatası: {e}")

# --- GELİŞMİŞ HEDEF BULUCU (Telegram Kısıtlamalarını Aşan Sistem) ---
async def hedef_kullanici_bul(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return None, ""

    # 1. YÖNTEM: Mesaja Yanıt (En Güvenilir Yöntem)
    if msg.reply_to_message:
        user = msg.reply_to_message.from_user
        return user.id, f"Yanıtlanan {user.full_name}"

    # 2. YÖNTEM: Komut ile argüman girilmesi (Mention veya ID)
    if context.args:
        hedef_girdi = context.args[0]
        
        # Telegram Entity Taraması (Mavi etiketlenen kullanıcılar)
        if msg.entities:
            for ent in msg.entities:
                if ent.type == MessageEntity.TEXT_MENTION:
                    return ent.user.id, f"Etiketlenen {ent.user.full_name}"
                elif ent.type == MessageEntity.MENTION:
                    # Geleneksel @username araması (Bot kullanıcıyı daha önce gördüyse çalışır)
                    try:
                        user_info = await context.bot.get_chat(hedef_girdi)
                        return user_info.id, hedef_girdi
                    except Exception as e:
                        logger.warning(f"Kullanıcı adı API'den bulunamadı ({hedef_girdi}): {e}")
                        # API bulamazsa grup içindeki üyelerden veriyi zorlar
                        try:
                            member = await context.bot.get_chat_member(msg.chat_id, hedef_girdi)
                            return member.user.id, hedef_girdi
                        except Exception:
                            pass
        
        # ID girilmişse direkt al
        try:
            if hedef_girdi.isdigit() or (hedef_girdi.startswith("-") and hedef_girdi[1:].isdigit()):
                return int(hedef_girdi), f"ID: {hedef_girdi}"
        except Exception:
            pass

    return None, ""

# --- KÜFÜR VE FEDERASYON OTO-KONTROL ---
async def mesaj_denetimi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    user = update.message.from_user
    chat = update.effective_chat

    if user.id == context.bot.id: return # Botun kendisini denetlemesini engeller

    # 1. Federasyon Ban Kontrolü
    fed_yasakli = db_execute("SELECT * FROM fed_banlar WHERE user_id = ?", (user.id,), fetchone=True)
    if fed_yasakli:
        try:
            await update.message.delete()
            await chat.ban_member(user.id)
            logger.info(f"Oto-Federasyon Ban: {user.id} {chat.title} grubundan atıldı.")
        except Exception: pass
        return

    # Yöneticiyse küfür filtresini atla
    if await yonetici_mi(update, user.id): return

    # 2. Küfür ve Kelime Filtresi
    text = update.message.text.lower()
    for kelime in YASAKLI_KELIMELER:
        if kelime in text:
            try:
                await update.message.delete()
                # Uyarı sayısını veritabanında artır
                eski_uyari = db_execute("SELECT uyari_sayisi FROM uyarilar WHERE user_id = ?", (user.id,), fetchone=True)
                yeni_sayi = 1 if not eski_uyari else eski_uyari[0] + 1
                db_execute("INSERT OR REPLACE INTO uyarilar (user_id, uyari_sayisi) VALUES (?, ?)", (user.id, yeni_sayi))
                
                # Kullanıcıyı sustur
                await chat.restrict_member(user.id, permissions=ChatPermissions(can_send_messages=False), until_date=timedelta(minutes=10))
                
                guvenli_isim = html.escape(user.full_name)
                msj = await context.bot.send_message(chat.id, f"⚠️ {guvenli_isim}, yasaklı kelime kullandığı için 10 dk susturuldu! (İhlal: {yeni_sayi})", parse_mode="HTML")
                await bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
                log_kaydet("KÜFÜR", f"{user.id} yasaklı kelime kullandı: {kelime}")
            except Exception as e:
                logger.error(f"Mesaj denetiminde hata: {e}")
            break

# --- ANA YÖNETİM ARAYÜZÜ (Çalışan Buton Sistemi) ---
async def start_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if not await yonetici_mi(update, user.id):
        return

    keyboard = [
        [InlineKeyboardButton("📊 İstatistikler", callback_data="btn_istatistik"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="btn_chat")],
        [InlineKeyboardButton("🌐 Federasyon Ağım", callback_data="btn_fed"), InlineKeyboardButton("⏱️ Yayın Motoru", callback_data="btn_zaman")],
        [InlineKeyboardButton("📜 Log Kayıtları", callback_data="btn_log"), InlineKeyboardButton("🛠️ Komutlar", callback_data="btn_yardim")],
        [InlineKeyboardButton("📌 Paneli Sabitle", callback_data="btn_sabitle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        guvenli_isim = html.escape(user.full_name)
        msj = await context.bot.send_message(
            chat.id,
            f"🛡️ **Golden Guardian Pro - Yönetim Terminali**\n\n"
            f"Yetkili: {guvenli_isim}\n\nAşağıdaki etkileşimli butonları kullanarak botun tüm donanımını kontrol edebilirsiniz.",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        await bot_mesajini_sil_planla(context, chat.id, msj.message_id, 60.0)
    except Exception as e:
        logger.error(f"/start komutu arayüz basma hatası: {e}")

# BUTON MOTORU (Kesin ve Hızlı Yanıt Sistemi)
async def buton_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # Butona tıklandığında dönen yükleme ikonunu durdurmak için zorunlu (Aksi halde işlem yapmaz)
    try:
        await query.answer()
    except Exception as e:
        logger.error(f"Query Answer Hatası: {e}")

    data = query.data
    chat = update.effective_chat
    user = update.effective_user

    # Güvenlik Kontrolü: Butona basan yönetici mi?
    if not await yonetici_mi(update, user.id):
        await context.bot.answer_callback_query(query.id, "❌ Bu butonları sadece yöneticiler kullanabilir!", show_alert=True)
        return

    log_kaydet("ARAYÜZ", f"Tıklanan Buton: {data} | Yapan: {user.id}")

    ana_menu_keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="btn_ana")]]

    if data == "btn_istatistik":
        g_sayisi = db_execute("SELECT COUNT(*) FROM federasyon_gruplar", fetchone=True)
        b_sayisi = db_execute("SELECT COUNT(*) FROM fed_banlar", fetchone=True)
        metin = f"📊 **Bot ve Ağ İstatistikleri:**\n\n• Toplam Federasyon Grubu: `{g_sayisi[0] if g_sayisi else 0}`\n• Küresel İdam Edilenler: `{b_sayisi[0] if b_sayisi else 0}`\n• Motor Durumu: `Online & Stabil`"
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_keyboard))

    elif data == "btn_chat":
        metin = "💬 **Portal Yöneticisi:**\n\n`/portal` komutunu gruba yazarak veya aşağıdaki butona tıklayarak gruba 'Sohbete Katıl' butonu gönderebilirsiniz."
        kb = [[InlineKeyboardButton("🚀 Gruba Portal At", callback_data="islem_portal_at")], [InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="btn_ana")]]
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "islem_portal_at":
        grup_linki = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
        kb = [[InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl", url=grup_linki)], [InlineKeyboardButton("📜 Kuralları Oku", callback_data="btn_kurallar")]]
        grup_adi = html.escape(chat.title or "Sohbet Grubu")
        await context.bot.send_message(chat.id, f"👑 **{grup_adi} İletişim Portalı**\n\nSohbete dahil olmak için butona tıklayın.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        await context.bot.answer_callback_query(query.id, "Portal gruba gönderildi!", show_alert=True)

    elif data == "btn_fed":
        gruplar = db_execute("SELECT grup_adi, grup_linki FROM federasyon_gruplar", fetchall=True)
        metin = "🌐 **Aktif Federasyon Ağı:**\n\nYeni grup eklemek için gruba gidip `/fedbagla` yazın.\n\n"
        kb = []
        if not gruplar:
            metin += "Kayıtlı grup bulunamadı."
        else:
            for g in gruplar:
                g_adi = g[0] or "Grup"
                if g[1] and "t.me" in g[1]: kb.append([InlineKeyboardButton(f"🔗 {g_adi}", url=g[1])])
                else: metin += f"• {g_adi}\n"
        kb.append([InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="btn_ana")])
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "btn_zaman":
        metin = "⏱️ **Zamanlı Yayın Motoru:**\n\nYayın eklemek için bir mesaja/fotoğrafa yanıt vererek `/ekle` yazın. Sistem her 30 dakikada bir otomatik gönderim yapar."
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_keyboard))

    elif data == "btn_log":
        kayitlar = db_execute("SELECT zaman, islem_turu, detay FROM bot_loglar ORDER BY id DESC LIMIT 5", fetchall=True)
        metin = "📜 **Sistem Güvenlik Logları (Son 5):**\n\n"
        if not kayitlar: metin += "Kayıt yok."
        else:
            for k in kayitlar: metin += f"⏱ `{k[0]}`\n**{k[1]}**: {k[2]}\n\n"
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_keyboard))

    elif data == "btn_yardim":
        metin = "🛠️ **Tam Yetki Komut Kılavuzu:**\n\n`/defol` - Gruptan atar.\n`/itaat` - 10 dk susturur.\n`/kralice` - Tüm kısıtlamaları kesin olarak çözer.\n`/idam` - Ağa bağlı tüm gruplardan banlar.\n`/genelaf` - Ağdaki yasağı kaldırır.\n`/portal` - Sohbet butonu atar.\n`/fedbagla` - Ağa bağlar.\n`/ekle` & `/yayinlar` & `/sil` - Otomatik mesaj yönetimi."
        await query.edit_message_text(text=metin, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_keyboard))

    elif data == "btn_sabitle":
        try:
            await query.message.pin()
            await context.bot.answer_callback_query(query.id, "Panel gruba sabitlendi!", show_alert=True)
        except Exception:
            await context.bot.answer_callback_query(query.id, "Sabitleme izni yok!", show_alert=True)

    elif data == "btn_ana":
        kb = [
            [InlineKeyboardButton("📊 İstatistikler", callback_data="btn_istatistik"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="btn_chat")],
            [InlineKeyboardButton("🌐 Federasyon Ağım", callback_data="btn_fed"), InlineKeyboardButton("⏱️ Yayın Motoru", callback_data="btn_zaman")],
            [InlineKeyboardButton("📜 Log Kayıtları", callback_data="btn_log"), InlineKeyboardButton("🛠️ Komutlar", callback_data="btn_yardim")],
            [InlineKeyboardButton("📌 Paneli Sabitle", callback_data="btn_sabitle")]
        ]
        await query.edit_message_text(text="🛡️ **Golden Guardian Pro - Yönetim Terminali**\n\nİşlem seçiniz:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "btn_kurallar":
        await context.bot.answer_callback_query(query.id, "Kurallar: Argo yasak, Reklam yasak, Saygı zorunlu. İhlaller Küresel İdam ile sonuçlanır.", show_alert=True)

# --- İŞLEVSEL YÖNETİM KOMUTLARI ---

async def portal_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await yonetici_mi(update, update.message.from_user.id): return
    grup_linki = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    kb = [[InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl", url=grup_linki)], [InlineKeyboardButton("📜 Kurallar", callback_data="btn_kurallar")]]
    grup_adi = html.escape(chat.title or "Grup")
    await context.bot.send_message(chat.id, f"👑 **{grup_adi} Canlı Sohbet Portalı**\n\nBağlanmak için butona tıklayın.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

async def fedbagla_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await yonetici_mi(update, update.message.from_user.id): return
    grup_linki = f"https://t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    
    db_execute("INSERT OR REPLACE INTO federasyon_gruplar (chat_id, grup_adi, grup_linki) VALUES (?, ?, ?)", (chat.id, chat.title, grup_linki))
    db_execute("INSERT OR REPLACE INTO zamanli_ayar (chat_id, sure_dakika, aktif_durum) VALUES (?, 30, 'AKTİF')", (chat.id,))
    
    await update.message.reply_text("✅ **Sistem Entegre Edildi!**\nGrup, federasyon ağına bağlandı.")
    log_kaydet("FEDERASYON", f"Bağlantı sağlandı: {chat.title}")

# MÜKEMMEL HEDEF BULMA ALTYAPISI İLE MODERASYON
async def defol_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await yonetici_mi(update, update.message.from_user.id): return
    
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ **Hata:** Hedef bulunamadı.\nLütfen mesajına yanıt verin veya geçerli bir ID yazın.")
        return
    
    try:
        await chat.ban_member(hedef_id)
        await update.message.reply_text(f"🔨 **Sürgün Edildi!**\nKullanıcı gruptan kalıcı olarak atıldı. ({aciklama})")
        log_kaydet("DEFOL", f"Kalıcı Ban: {hedef_id} - {aciklama}")
    except Exception as e:
        await update.message.reply_text(f"İşlem başarısız (Bot yetkisi veya kullanıcı durumu): {e}")

async def itaat_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await yonetici_mi(update, update.message.from_user.id): return
    
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ **Hata:** Hedef bulunamadı.")
        return
        
    try:
        await chat.restrict_member(hedef_id, permissions=ChatPermissions(can_send_messages=False), until_date=timedelta(minutes=10))
        await update.message.reply_text(f"🤐 **İtaat Sağlandı!**\nKullanıcı 10 dakika susturuldu. ({aciklama})")
        log_kaydet("İTAAT", f"Susturuldu: {hedef_id}")
    except Exception as e:
        await update.message.reply_text(f"Hata: {e}")

async def kralice_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await yonetici_mi(update, update.message.from_user.id): return
    
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ **Hata:** Hedef bulunamadı.")
        return
    
    try:
        # Mute'yi kesin sıfırlayan güçlü izin takımı
        await chat.restrict_member(
            hedef_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True
            )
        )
        # Uyarı puanını da temizle
        db_execute("DELETE FROM uyarilar WHERE user_id = ?", (hedef_id,))
        await update.message.reply_text(f"✨ **Ferman Yayımlandı!**\nKullanıcının tüm mutesi, kısıtlamaları ve uyarıları kesin olarak kaldırıldı. ({aciklama})")
        log_kaydet("KRALİÇE", f"Mute çözüldü: {hedef_id}")
    except Exception as e:
        await update.message.reply_text(f"İşlem başarısız: {e}")

async def idam_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await yonetici_mi(update, update.message.from_user.id): return
    
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ **Hata:** Hedef bulunamadı.")
        return

    db_execute("INSERT OR REPLACE INTO fed_banlar (user_id, sebep) VALUES (?, ?)", (hedef_id, "Küresel İdam"))
    gruplar = db_execute("SELECT chat_id FROM federasyon_gruplar", fetchall=True)

    sayi = 0
    if gruplar:
        for g in gruplar:
            try:
                await context.bot.ban_member(chat_id=g[0], user_id=hedef_id)
                sayi += 1
            except Exception:
                pass

    await update.message.reply_text(f"⚖️ **KÜRESEL İDAM UYGULANDI!**\nHedef, federasyon ağındaki {sayi} gruptan eşzamanlı ve kalıcı olarak banlandı. ({aciklama})")
    log_kaydet("İDAM", f"Küresel Ban: {hedef_id}")

async def genelaf_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await yonetici_mi(update, update.message.from_user.id): return
    
    hedef_id, aciklama = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ **Hata:** Hedef bulunamadı.")
        return

    db_execute("DELETE FROM fed_banlar WHERE user_id = ?", (hedef_id,))
    gruplar = db_execute("SELECT chat_id FROM federasyon_gruplar", fetchall=True)

    if gruplar:
        for g in gruplar:
            try:
                await context.bot.unban_member(chat_id=g[0], user_id=hedef_id)
            except Exception:
                pass

    await update.message.reply_text(f"🕊️ **GENEL AF İLAN EDİLDİ!**\nKullanıcının ağdaki küresel yasakları tamamen kaldırıldı. ({aciklama})")
    log_kaydet("GENELAF", f"Af uygulandı: {hedef_id}")

# --- YAYIN YÖNETİMİ ---
async def ekle_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await yonetici_mi(update, update.message.from_user.id): return
    msg = update.message.reply_to_message
    if not msg:
        await update.message.reply_text("⚠️ Kılavuz: Yayınlamak istediğiniz fotoğraf veya mesaja yanıt vererek `/ekle` yazın.")
        return
    
    tip = "photo" if msg.photo else "text"
    icerik = msg.photo[-1].file_id if msg.photo else msg.text
    aciklama = msg.caption or "" if msg.photo else ""

    if not icerik: return

    db_execute("INSERT INTO yayin_listesi (chat_id, tip, icerik, aciklama) VALUES (?, ?, ?, ?)", (update.effective_chat.id, tip, icerik, aciklama))
    await update.message.reply_text("✅ Medya/Metin başarılı şekilde zamanlanmış yayın listesine kaydedildi!")
    log_kaydet("YAYIN", "Yeni içerik eklendi")

async def yayinlar_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await yonetici_mi(update, update.message.from_user.id): return
    yayinlar = db_execute("SELECT id, tip FROM yayin_listesi WHERE chat_id = ?", (update.effective_chat.id,), fetchall=True)
    
    if not yayinlar:
        await update.message.reply_text("Aktif zamanlanmış yayın bulunmuyor.")
        return
    
    metin = "📜 **Aktif Yayınlar:**\n"
    for y in yayinlar:
        metin += f"• Yayın ID: `{y[0]}` | Tip: {y[1]}\n"
    metin += "\nSilmek için `/sil <ID>` kullanın."
    await update.message.reply_text(metin, parse_mode="Markdown")

async def sil_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await yonetici_mi(update, update.message.from_user.id): return
    if not context.args: 
        await update.message.reply_text("Doğru kullanım: `/sil Yayın_ID`")
        return
    
    yayin_id = context.args[0]
    db_execute("DELETE FROM yayin_listesi WHERE id = ? AND chat_id = ?", (yayin_id, update.effective_chat.id))
    await update.message.reply_text(f"✅ Yayın {yayin_id} başarıyla silindi.")

# OTO-YAYIN ZAMANLAYICI FONKSİYONU
async def otomatik_yayin_motoru(context: ContextTypes.DEFAULT_TYPE):
    try:
        yayinlar = db_execute("SELECT chat_id, tip, icerik, aciklama FROM yayin_listesi", fetchall=True)
        aktif_gruplar_raw = db_execute("SELECT chat_id FROM zamanli_ayar WHERE aktif_durum = 'AKTİF'", fetchall=True)
        if not yayinlar or not aktif_gruplar_raw: return
        
        aktif_gruplar = [row[0] for row in aktif_gruplar_raw]

        for y in yayinlar:
            y_chat_id, y_tip, y_icerik, y_aciklama = y
            if y_chat_id in aktif_gruplar:
                grup_linki = f"https://t.me/c/{str(y_chat_id).replace('-100','')}/1"
                kb = [[InlineKeyboardButton("💬 Sohbete Katıl / Chat", url=grup_linki)]]
                try:
                    if y_tip == "photo":
                        await context.bot.send_photo(chat_id=y_chat_id, photo=y_icerik, caption=y_aciklama, reply_markup=InlineKeyboardMarkup(kb))
                    elif y_tip == "text":
                        await context.bot.send_message(chat_id=y_chat_id, text=y_icerik, reply_markup=InlineKeyboardMarkup(kb))
                except Exception as e:
                    logger.error(f"Oto-Yayın hatası: {e}")
    except Exception as e:
        logger.error(f"Zamanlayıcı çekirdek hatası: {e}")

# --- BOT BAŞLATICI VE İZİN YAPILANDIRMASI ---
async def post_init(application):
    try:
        # Telegram Webhook bağlantılarını tamamen temizler ve botu temiz bir sayfa ile dinlemeye alır
        await application.bot.delete_webhook(drop_pending_updates=True)
        
        # Yayın motorunu 1800 saniyede (30 dk) bir çalışacak şekilde ayarlar.
        if application.job_queue:
            application.job_queue.run_repeating(otomatik_yayin_motoru, interval=1800, first=20)
            logger.info("Zamanlayıcı motoru (JobQueue) aktif edildi.")
        else:
            logger.error("DİKKAT: JobQueue bulunamadı! 'python-telegram-bot[job-queue]' yüklü olduğundan emin olun.")
            
        logger.info("Sistem Tam Kurulumla Başlatıldı!")
    except Exception as e:
        logger.error(f"Başlatma (post_init) hatası: {e}")

def main():
    if not TOKEN:
        print("KRİTİK HATA: BOT_TOKEN bulunamadı!")
        return

    threading.Thread(target=start_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # Yönetim Komutları
    app.add_handler(CommandHandler("start", start_komutu))
    app.add_handler(CommandHandler("portal", portal_komutu))
    app.add_handler(CommandHandler("fedbagla", fedbagla_komutu))
    app.add_handler(CommandHandler("ekle", ekle_komutu))
    app.add_handler(CommandHandler("sil", sil_komutu))
    app.add_handler(CommandHandler("yayinlar", yayinlar_komutu))
    app.add_handler(CommandHandler("defol", defol_komutu))
    app.add_handler(CommandHandler("itaat", itaat_komutu))
    app.add_handler(CommandHandler("kralice", kralice_komutu))
    app.add_handler(CommandHandler("idam", idam_komutu))
    app.add_handler(CommandHandler("genelaf", genelaf_komutu))
    
    # Arayüz Buton Yöneticisi
    app.add_handler(CallbackQueryHandler(buton_yoneticisi))
    
    # Denetim ve Küfür Filtresi (Komut olmayan tüm metin mesajlarını yakalar)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), mesaj_denetimi))
    
    app.add_error_handler(global_error_handler)

    logger.info("Bot geniş dinleme yetkileriyle (ALL_TYPES) çalışmaya başlıyor...")
    # allowed_updates=Update.ALL_TYPES parametresi, botun CallbackQuery (butonlar) dahil tüm veriyi çekmesini garanti eder.
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

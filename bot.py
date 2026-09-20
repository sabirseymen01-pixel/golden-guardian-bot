import logging
from datetime import timedelta
import sqlite3
import os
import threading
import html
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram kütüphanelerinin güvenli yüklenmesi
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
    print(f"KRİTİK İMPORT HATASI: python-telegram-bot kütüphanesi yüklenemedi! Detay: {e}")
    exit(1)

# --- RENDER PORT VE SAĞLIK KONTROL SUNUCUSU ---
PORT = int(os.environ.get("PORT", 10000))

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Golden Guardian Pro Functional Bot is active and running!")
    def log_message(self, format, *args):
        pass

def start_dummy_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Sağlık sunucusu başlatılamadı: {e}")

TOKEN = os.environ.get("BOT_TOKEN")

# --- LOGLAMA ---
logging.basicConfig(format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("GoldenGuardianBot")

# --- GLOBAL HATA YAKALAYICI ---
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"İstisna Yakalandı: {context.error}", exc_info=context.error)

# --- VERİTABANI YÖNETİMİ ---
def db_kur():
    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS uyarilar (user_id INTEGER PRIMARY KEY, uyari_sayisi INTEGER DEFAULT 0)")
        cursor.execute("CREATE TABLE IF NOT EXISTS bot_loglar (id INTEGER PRIMARY KEY AUTOINCREMENT, zaman TIMESTAMP DEFAULT CURRENT_TIMESTAMP, islem_turu TEXT, detay TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS fed_banlar (user_id INTEGER PRIMARY KEY, sebep TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS federasyon_gruplar (chat_id INTEGER PRIMARY KEY, grup_adi TEXT, grup_linki TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS zamanli_ayar (chat_id INTEGER PRIMARY KEY, sure_dakika INTEGER DEFAULT 30, aktif_durum TEXT DEFAULT 'AKTİF')")
        cursor.execute("CREATE TABLE IF NOT EXISTS yayin_listesi (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, tip TEXT, icerik TEXT, aciklama TEXT)")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Kurulum hatası: {e}")

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
    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT uyari_sayisi FROM uyarilar WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        yeni_sayi = 1 if res is None else res[0] + 1
        cursor.execute("INSERT OR REPLACE INTO uyarilar (user_id, uyari_sayisi) VALUES (?, ?)", (user_id, yeni_sayi))
        conn.commit()
        conn.close()
        return yeni_sayi
    except Exception:
        return 1

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
        return True

async def bot_mesajini_sil_planla(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, saniye: float = 15.0):
    async def sil_gorevi(ctx: ContextTypes.DEFAULT_TYPE):
        try:
            await ctx.bot.delete_message(chat_id=ctx.job.data["chat_id"], message_id=ctx.job.data["message_id"])
        except Exception:
            pass
    try:
        if context.job_queue:
            context.job_queue.run_once(sil_gorevi, saniye, data={"chat_id": chat_id, "message_id": message_id})
    except Exception:
        pass

# --- HEDEF BULUCU ---
async def hedef_kullanici_bul(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hedef_id = None
    aciklama = ""
    msg = update.message
    
    if not msg:
        return None, ""
    
    if msg.reply_to_message:
        return msg.reply_to_message.from_user.id, f"Yanıtlanan {msg.reply_to_message.from_user.full_name}"

    if msg.entities:
        for entity in msg.entities:
            if entity.type == MessageEntity.TEXT_MENTION:
                return entity.user.id, f"Etiketlenen {entity.user.full_name}"
            
    if context.args:
        girdi = context.args[0]
        aciklama = girdi
        try:
            if girdi.startswith("@"):
                try:
                    user = await context.bot.get_chat(girdi)
                    hedef_id = user.id
                except Exception:
                    chat_member = await context.bot.get_chat_member(msg.chat_id, girdi)
                    hedef_id = chat_member.user.id
            else:
                hedef_id = int(girdi)
        except Exception as e:
            hedef_id = None

    return hedef_id, aciklama

# --- OTOMATİK YAYIN MOTORU ---
async def otomatik_yayin_motoru(context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, tip, icerik, aciklama FROM yayin_listesi")
        yayinlar = cursor.fetchall()
        cursor.execute("SELECT chat_id, aktif_durum FROM zamanli_ayar WHERE aktif_durum = 'AKTİF'")
        aktif_gruplar = [row[0] for row in cursor.fetchall()]
        conn.close()

        for y in yayinlar:
            y_chat_id, y_tip, y_icerik, y_aciklama = y
            if y_chat_id in aktif_gruplar:
                grup_linki = f"https://t.me/c/{str(y_chat_id).replace('-100','')}/1"
                keyboard = [[InlineKeyboardButton("💬 Sohbete Katıl / Chat", url=grup_linki)]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    if y_tip == "photo":
                        await context.bot.send_photo(chat_id=y_chat_id, photo=y_icerik, caption=y_aciklama, reply_markup=reply_markup)
                    elif y_tip == "text":
                        await context.bot.send_message(chat_id=y_chat_id, text=y_icerik, reply_markup=reply_markup)
                except Exception as e:
                    logger.error(f"Yayın gönderilemedi {y_chat_id}: {e}")
    except Exception:
        pass

# --- KÜFÜR VE KÜRESEL BAN KONTROLÜ ---
async def mesaj_denetimi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.message.from_user
    chat = update.effective_chat

    if user.id == context.bot.id:
        return

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

    if await yonetici_mi(update, user.id): return

    text = update.message.text.lower()
    for kelime in YASAKLI_KELIMELER:
        if kelime in text:
            try:
                await update.message.delete()
                toplam_uyari = uyari_arttir(user.id)
                await chat.restrict_member(user.id, permissions=ChatPermissions(can_send_messages=False), until_date=timedelta(minutes=10))
                guvenli_isim = html.escape(user.full_name)
                msj = await context.bot.send_message(chat.id, f"⚠️ {guvenli_isim}, küfür/yasaklı kelime nedeniyle 10 dk susturuldu! (İhlal: {toplam_uyari})")
                await bot_mesajini_sil_planla(context, chat.id, msj.message_id, 10.0)
                log_kaydet("KÜFÜR", f"Kullanıcı susturuldu: {user.id}")
            except Exception:
                pass
            break

# --- İŞLEVSEL İNTERAKTİF YÖNETİM PANELİ ---
async def start_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    log_kaydet("KOMUT", f"/start tetiklendi: {user.full_name} ({user.id})")

    keyboard = [
        [InlineKeyboardButton("📊 İstatistikler", callback_data="menu_istatistik"), InlineKeyboardButton("💬 Sohbet Butonu Paneli", callback_data="menu_chatbuton")],
        [InlineKeyboardButton("🌐 Federasyon Ağı", callback_data="menu_federasyon"), InlineKeyboardButton("⏱️ Zamanlayıcı", callback_data="menu_zamanlayici")],
        [InlineKeyboardButton("📜 Log Kayıtları", callback_data="menu_loglar"), InlineKeyboardButton("🛠️ Yardım", callback_data="menu_yardim")],
        [InlineKeyboardButton("📌 Bu Paneli Gruba Sabitle", callback_data="menu_sabitle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        guvenli_isim = html.escape(user.full_name)
        msj = await context.bot.send_message(
            chat.id,
            f"🛡️ **Golden Guardian Pro - İşlevsel Yönetim Paneli**\n\n"
            f"Yetkili: {guvenli_isim}\nAşağıdaki düğmeleri kullanarak tüm sistemi yönetebilirsiniz.",
            reply_markup=reply_markup
        )
        await bot_mesajini_sil_planla(context, chat.id, msj.message_id, 30.0)
    except Exception as e:
        logger.error(f"/start hata: {e}")

async def buton_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat = update.effective_chat

    log_kaydet("BUTON", f"Tetiklenen callback: {data}")

    if data == "menu_istatistik":
        try:
            conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM federasyon_gruplar")
            grup_sayisi = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM fed_banlar")
            ban_sayisi = cursor.fetchone()[0]
            conn.close()
        except Exception:
            grup_sayisi, ban_sayisi = 0, 0

        metin = (
            "📊 **Sistem İstatistikleri Raporu**\n\n"
            f"• Bağlı Federasyon Grup Sayısı: {grup_sayisi}\n"
            f"• Küresel Banlı (İdam) Üye: {ban_sayisi}\n"
            "• Durum: Mükemmel, Kesintisiz Çalışıyor"
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_chatbuton":
        metin = (
            "💬 **Sohbete Atıl / Chat Butonu Yönetimi**\n\n"
            "Bu özellik, üyelerin doğrudan sohbete katılması için şık bir portal butonu oluşturmanızı sağlar.\n\n"
            "• **Nasıl Kullanılır?**\n"
            "Aşağıdaki butona basarak gruba anında sohbet butonu gönderebilirsiniz."
        )
        keyboard = [
            [InlineKeyboardButton("🚀 Bu Gruba Sohbet Butonu Gönder", callback_data="islem_portalat")],
            [InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]
        ]
        await query.edit_message_text(text=metin, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "islem_portalat":
        grup_linki = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
        keyboard = [
            [InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl / Chat", url=grup_linki)],
            [InlineKeyboardButton("📜 Grup Kuralları", callback_data="btn_kurallar")]
        ]
        grup_adi = html.escape(chat.title or "Grup")
        await context.bot.send_message(
            chat.id,
            f"👑 **{grup_adi} - Canlı Sohbet Portalı**\n\nSohbete katılmak ve aktif tartışmalara dahil olmak için aşağıdaki butona tıklayın.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.answer("Sohbet butonu başarıyla gruba gönderildi!", show_alert=True)

    elif data == "menu_federasyon":
        try:
            conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT grup_adi, grup_linki FROM federasyon_gruplar")
            gruplar = cursor.fetchall()
            conn.close()
        except Exception:
            gruplar = []

        metin = "🌐 **Federasyon Ağ Paneli**\n\nSisteme bağlı gruplar:\n\n"
        keyboard = []
        if not gruplar:
            metin += "Henüz bağlı grup yok. /fedbagla komutunu kullanın."
        else:
            for g in gruplar:
                g_adi = g[0] or "Grup"
                if g[1] and "t.me" in g[1]:
                    keyboard.append([InlineKeyboardButton(f"🔗 {g_adi}", url=g[1])])
                else:
                    metin += f"• {g_adi}\n"

        keyboard.append([InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")])
        await query.edit_message_text(text=metin, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_zamanlayici":
        metin = (
            "⏱️ **Zamanlayıcı ve Periyodik Yayın Paneli**\n\n"
            "Otomatik periyodik paylaşımlar her 30 dakikada bir aktif gruplara gönderilir.\n"
            "• İçerik eklemek için resme/metne yanıt verip /ekle yazın."
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_loglar":
        try:
            conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT zaman, islem_turu, detay FROM bot_loglar ORDER BY id DESC LIMIT 5")
            kayitlar = cursor.fetchall()
            conn.close()
        except Exception:
            kayitlar = []

        metin = "📜 **Son 5 Sistem İşlem Logu:**\n\n"
        if not kayitlar:
            metin += "Kayıt yok."
        else:
            for k in kayitlar:
                metin += f"⏱ {k[0]} | **{k[1]}**: {k[2]}\n\n"

        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_yardim":
        metin = (
            "🛠️ **Golden Guardian Pro - Komut Kılavuzu**\n\n"
            "• /portal : Sohbet butonu oluşturur.\n"
            "• /fedbagla : Grubu federasyona bağlar.\n"
            "• /defol [ID/@user/Yanıt] : Banlar.\n"
            "• /itaat [ID/@user/Yanıt] : Susturur.\n"
            "• /kralice [ID/@user/Yanıt] : Muteyi kaldırır.\n"
            "• /idam [ID/@user/Yanıt] : Küresel ban atar.\n"
            "• /ekle : Periyodik yayın ekler."
        )
        keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]
        await query.edit_message_text(text=metin, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_sabitle":
        try:
            await query.message.pin()
            await query.answer("Panel başarıyla gruba sabitlendi!", show_alert=True)
        except Exception:
            await query.answer("Sabitleme yetkisi eksik!", show_alert=True)

    elif data == "menu_ana":
        keyboard = [
            [InlineKeyboardButton("📊 İstatistikler", callback_data="menu_istatistik"), InlineKeyboardButton("💬 Sohbet Butonu Paneli", callback_data="menu_chatbuton")],
            [InlineKeyboardButton("🌐 Federasyon Ağı", callback_data="menu_federasyon"), InlineKeyboardButton("⏱️ Zamanlayıcı", callback_data="menu_zamanlayici")],
            [InlineKeyboardButton("📜 Log Kayıtları", callback_data="menu_loglar"), InlineKeyboardButton("🛠️ Yardım", callback_data="menu_yardim")],
            [InlineKeyboardButton("📌 Bu Paneli Gruba Sabitle", callback_data="menu_sabitle")]
        ]
        await query.edit_message_text(text="🛡️ **Golden Guardian Pro - Ana Yönetim Paneli**\n\nİşlem seçiniz:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "btn_kurallar":
        await query.message.reply_text("📜 **Grup Kuralları:**\n1. Küfür ve argo kesinlikle yasaktır.\n2. Reklam yapmak küresel idam sebebidir.\n3. Saygı esastır.")

# --- KOMUTLAR ---

async def portal_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    grup_linki = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    keyboard = [
        [InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl / Chat", url=grup_linki)],
        [InlineKeyboardButton("📜 Kurallar", callback_data="btn_kurallar")]
    ]
    grup_adi = html.escape(chat.title or "Grup")
    await context.bot.send_message(
        chat.id, 
        f"👑 **{grup_adi} Canlı Sohbet Portalı**\n\nSohbete hızlıca bağlanmak için butonu kullanın.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    log_kaydet("PORTAL", f"Sohbet butonu gönderildi: {chat.title}")

async def fedbagla_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    grup_linki = f"https://t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    
    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO federasyon_gruplar (chat_id, grup_adi, grup_linki) VALUES (?, ?, ?)", (chat.id, chat.title, grup_linki))
    cursor.execute("INSERT OR REPLACE INTO zamanli_ayar (chat_id, sure_dakika, aktif_durum) VALUES (?, 30, 'AKTİF')", (chat.id,))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ **Bu grup başarıyla Federasyon Ağına bağlandı ve aktif edildi!**")
    log_kaydet("FEDERASYON", f"Grup bağlandı: {chat.title}")

async def ekle_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.reply_to_message
    if not msg:
        await update.message.reply_text("⚠️ Hata: Eklemek istediğiniz resme veya metne yanıt vererek /ekle yazmalısınız.")
        return
    
    tip = "text"
    icerik = ""
    aciklama = ""

    if msg.photo:
        tip = "photo"
        icerik = msg.photo[-1].file_id
        aciklama = msg.caption or ""
    elif msg.text:
        tip = "text"
        icerik = msg.text
    else:
        return

    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO yayin_listesi (chat_id, tip, icerik, aciklama) VALUES (?, ?, ?, ?)", (update.effective_chat.id, tip, icerik, aciklama))
    yayin_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ Medya/Metin yayın listesine eklendi! (ID: {yayin_id})")
    log_kaydet("YAYIN", f"Yeni içerik eklendi ID: {yayin_id}")

async def yayinlar_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT id, tip FROM yayin_listesi WHERE chat_id = ?", (update.effective_chat.id,))
    yayinlar = cursor.fetchall()
    conn.close()
    
    if not yayinlar:
        await update.message.reply_text("Bu grupta aktif bir periyodik yayın yok.")
        return
    
    metin = "📜 **Aktif Zamanlanmış Yayınlar:**\n"
    for y in yayinlar:
        metin += f"• ID: {y[0]} | Tip: {y[1]}\n"
    metin += "\nSilmek için /sil ID yazabilirsiniz."
    await update.message.reply_text(metin)

async def sil_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: 
        await update.message.reply_text("Kullanım: /sil <yayin_id>")
        return
    yayin_id = context.args[0]
    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM yayin_listesi WHERE id = ? AND chat_id = ?", (yayin_id, update.effective_chat.id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Yayın {yayin_id} silindi.")
    log_kaydet("YAYIN SİL", f"Yayın ID silindi: {yayin_id}")

# Mod Komutları
async def defol_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    hedef_id, _ = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ Hedef kullanıcı bulunamadı (ID, @user yazın veya yanıt verin).")
        return
    await chat.ban_member(hedef_id)
    await update.message.reply_text("🔨 Kullanıcı gruptan defedildi!")
    log_kaydet("DEFOL", f"Kullanıcı banlandı: {hedef_id}")

async def itaat_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    hedef_id, _ = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ Hedef kullanıcı bulunamadı.")
        return
    await chat.restrict_member(hedef_id, permissions=ChatPermissions(can_send_messages=False), until_date=timedelta(minutes=10))
    await update.message.reply_text("🤐 Kullanıcı 10 dakika susturuldu.")
    log_kaydet("İTAAT", f"Kullanıcı susturuldu: {hedef_id}")

async def kralice_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    hedef_id, _ = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ Hedef kullanıcı bulunamadı.")
        return
    
    try:
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
        await update.message.reply_text("✨ Kraliçenin fermanıyla tüm mutesi ve kısıtlamaları kesin olarak kaldırıldı!")
        log_kaydet("KRALİÇE", f"Mute kaldırıldı: {hedef_id}")
    except Exception as e:
        await update.message.reply_text(f"İşlem hatası: {e}")

async def idam_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hedef_id, _ = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ Hedef kullanıcı bulunamadı.")
        return

    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO fed_banlar (user_id, sebep) VALUES (?, ?)", (hedef_id, "Küresel İdam"))
    cursor.execute("SELECT chat_id FROM federasyon_gruplar")
    gruplar = cursor.fetchall()
    conn.commit()
    conn.close()

    sayi = 0
    for g in gruplar:
        try:
            await context.bot.ban_member(chat_id=g[0], user_id=hedef_id)
            sayi += 1
        except Exception:
            pass

    await update.message.reply_text(f"⚖️ **KÜRESEL İDAM!** Kullanıcı federasyon ağındaki {sayi} gruptan eşzamanlı banlandı.")
    log_kaydet("İDAM", f"Küresel idam uygulandı: {hedef_id}")

async def genelaf_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hedef_id, _ = await hedef_kullanici_bul(update, context)
    if not hedef_id: 
        await update.message.reply_text("⚠️ Hedef kullanıcı bulunamadı.")
        return

    conn = sqlite3.connect("guardian_pro.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM fed_banlar WHERE user_id = ?", (hedef_id,))
    cursor.execute("SELECT chat_id FROM federasyon_gruplar")
    gruplar = cursor.fetchall()
    conn.commit()
    conn.close()

    for g in gruplar:
        try:
            await context.bot.unban_member(chat_id=g[0], user_id=hedef_id)
        except Exception:
            pass

    await update.message.reply_text("🕊️ **GENEL AF!** Kullanıcının tüm ağdaki yasakları kaldırıldı.")
    log_kaydet("GENELAF", f"Küresel af uygulandı: {hedef_id}")

# --- BAŞLATMA ---
async def post_init(application):
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        if application.job_queue:
            application.job_queue.run_repeating(otomatik_yayin_motoru, interval=1800, first=10)
        logger.info("Bot başarıyla başlatıldı ve görev kuyruğu aktif!")
    except Exception as e:
        logger.error(f"Post_init hatası: {e}")

def main():
    if not TOKEN:
        print("KRİTİK HATA: BOT_TOKEN ortam değişkeni bulunamadı!")
        return

    threading.Thread(target=start_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # Komut Kayıtları
    app.add_handler(CommandHandler("start", start_komutu))
    app.add_handler(CommandHandler("portal", portal_komutu))
    app.add_handler(CommandHandler("chatbutonu", portal_komutu))
    app.add_handler(CommandHandler("fedbagla", fedbagla_komutu))
    app.add_handler(CommandHandler("ekle", ekle_komutu))
    app.add_handler(CommandHandler("sil", sil_komutu))
    app.add_handler(CommandHandler("yayinlar", yayinlar_komutu))
    
    app.add_handler(CommandHandler("defol", defol_komutu))
    app.add_handler(CommandHandler("itaat", itaat_komutu))
    app.add_handler(CommandHandler("kralice", kralice_komutu))
    app.add_handler(CommandHandler("idam", idam_komutu))
    app.add_handler(CommandHandler("genelaf", genelaf_komutu))
    
    app.add_handler(CallbackQueryHandler(buton_yoneticisi))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), mesaj_denetimi))
    app.add_error_handler(global_error_handler)

    logger.info("Bot tam işlevsel polling modunda dinlemeye başlıyor...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

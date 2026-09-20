import logging
import sqlite3
import os
import asyncio
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import html

# Telegram Kütüphaneleri
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, MessageEntity
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- PORT VE RENDER SAĞLIK SUNUCUSU ---
PORT = int(os.environ.get("PORT", 10000))
TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("GoldenGuardianMasterFinal")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Golden Guardian Final Master Engine is Online and 100% Functional!")
    def log_message(self, format, *args):
        pass

def start_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"Sunucu hatası: {e}")

# --- VERİTABANI VE LOGLAMA ---
DB_NAME = "guardian_pro.db"

def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS uyarilar (user_id INTEGER PRIMARY KEY, uyari_sayisi INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS bot_loglar (id INTEGER PRIMARY KEY AUTOINCREMENT, zaman TIMESTAMP DEFAULT CURRENT_TIMESTAMP, islem_turu TEXT, detay TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS fed_banlar (user_id INTEGER PRIMARY KEY, sebep TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS federasyon_gruplar (chat_id INTEGER PRIMARY KEY, grup_adi TEXT, grup_linki TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS zamanli_ayar (chat_id INTEGER PRIMARY KEY, sure_dakika INTEGER DEFAULT 30, aktif_durum TEXT DEFAULT 'AKTİF')")
    cursor.execute("CREATE TABLE IF NOT EXISTS yayin_listesi (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, tip TEXT, icerik TEXT, aciklama TEXT)")
    conn.commit()
    conn.close()

init_db()

def log_kaydet(islem_turu, detay):
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO bot_loglar (islem_turu, detay) VALUES (?, ?)", (islem_turu, detay))
        conn.commit()
        conn.close()
    except Exception:
        pass
    logger.info(f"[{islem_turu}] {detay}")

# --- YETKİ VE HEDEF KONTROLÜ ---
async def is_admin(update: Update, user_id: int) -> bool:
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return None, "Mesaj yok"
    
    if msg.reply_to_message:
        u = msg.reply_to_message.from_user
        return u.id, f"Yanıtlanan: {u.full_name}"

    if msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.TEXT_MENTION:
                return ent.user.id, f"Etiketlenen: {ent.user.full_name}"

    if context.args:
        arg = context.args[0]
        if arg.lstrip("-").isdigit():
            return int(arg), f"ID: {arg}"
    return None, "Hedef bulunamadı"

# --- GÜVENLİK VE KÜFÜR FİLTRESİ ---
YASAKLI_KELIMELER = ["amina", "orospu", "o.ç", "piç", "sik", "sikerim", "sikik", "göt", "kahpe", "salak", "gerizekalı", "aptal", "discord.gg/", "http://", "https://"]

async def text_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.message.from_user
    chat = update.effective_chat

    if not user or user.id == context.bot.id or chat.type == "private":
        return

    # Federasyon Ban Kontrolü
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM fed_banlar WHERE user_id = ?", (user.id,))
        banned = cursor.fetchone()
        conn.close()
        if banned:
            await update.message.delete()
            await chat.ban_member(user.id)
            return
    except Exception:
        pass

    if await is_admin(update, user.id):
        return

    text = update.message.text.lower()
    for word in YASAKLI_KELIMELER:
        if word in text:
            try:
                await update.message.delete()
                await chat.restrict_member(user.id, permissions=ChatPermissions(can_send_messages=False), until_date=timedelta(minutes=10))
                msj = await update.message.reply_text(f"⚠️ {user.full_name}, yasaklı kelime nedeniyle 10 dakika susturuldu.")
                if context.job_queue:
                    context.job_queue.run_once(lambda ctx: ctx.job.data.delete() if ctx.job.data else None, 10.0, data=msj)
            except Exception:
                pass
            break

# --- ANA YÖNETİM PANELİ (START) ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if not await is_admin(update, user.id):
        return

    keyboard = [
        [InlineKeyboardButton("📊 İstatistikler", callback_data="menu_stats"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="menu_portal")],
        [InlineKeyboardButton("🌐 Federasyon Ağı", callback_data="menu_fed"), InlineKeyboardButton("⏱️ Zamanlı Yayın", callback_data="menu_zaman")],
        [InlineKeyboardButton("📜 Log Kayıtları", callback_data="menu_log"), InlineKeyboardButton("📖 Kullanım Kılavuzu", callback_data="menu_guide")],
        [InlineKeyboardButton("📌 Paneli Sabitle", callback_data="menu_pin")]
    ]
    
    await context.bot.send_message(
        chat.id,
        f"🛡️ **Golden Guardian Pro - Yönetim Paneli**\n\nYetkili: {user.full_name}\nAşağıdaki butonlar tamamen işlevseldir, dilediğiniz işlemi seçebilirsiniz:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# --- İNTERAKTİF BUTON YÖNETİCİSİ (GARANTİ ÇALIŞAN CALLBACK) ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Buton takılmalarını önleyen anlık yanıt
    data = query.data
    chat = update.effective_chat
    user = update.effective_user

    if not await is_admin(update, user.id):
        await query.answer("Bu paneli yalnızca yöneticiler kullanabilir!", show_alert=True)
        return

    ana_menu_kb = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="menu_ana")]]

    if data == "menu_stats":
        try:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM federasyon_gruplar")
            g = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM fed_banlar")
            b = cursor.fetchone()[0]
            conn.close()
        except Exception:
            g, b = 0, 0
        text = f"📊 **Sistem İstatistikleri:**\n\n• Bağlı Federasyon Grubu: `{g}`\n• Küresel Yasaklı (İdam): `{b}`\n• Sistem Durumu: `Stabil ve Sorunsuz Çalışıyor`"
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_kb))

    elif data == "menu_portal":
        link = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
        kb = [[InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl", url=link)], [InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_ana")]]
        await query.edit_message_text(text="💬 **Sohbet Portalı:**\n\nGruba özel kesintisiz sohbet bağlantısı:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "menu_fed":
        kb = [
            [InlineKeyboardButton("➕ Bu Grubu Ağa Bağla (Tek Tık)", callback_data="fed_tek_tik_baglan")],
            [InlineKeyboardButton("🌐 Bağlı Grupları Listele", callback_data="fed_liste")],
            [InlineKeyboardButton("🔙 Ana Menü", callback_data="menu_ana")]
        ]
        await query.edit_message_text(text="🌐 **Federasyon Ağ Yönetimi:**\n\nGrubunuzu tek tıkla ağa bağlayabilir veya bağlı grupları inceleyebilirsiniz.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "fed_tek_tik_baglan":
        link = f"https://t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
        try:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO federasyon_gruplar (chat_id, grup_adi, grup_linki) VALUES (?, ?, ?)", (chat.id, chat.title, link))
            cursor.execute("INSERT OR REPLACE INTO zamanli_ayar (chat_id, sure_dakika, aktif_durum) VALUES (?, 30, 'AKTİF')", (chat.id,))
            conn.commit()
            conn.close()
            await query.answer("✅ Bu grup başarıyla federasyon ağına bağlandı!", show_alert=True)
            log_kaydet("FEDERASYON", f"Grup bağlandı: {chat.title}")
        except Exception as e:
            await query.answer(f"Hata: {e}", show_alert=True)

    elif data == "fed_liste":
        try:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT grup_adi, grup_linki FROM federasyon_gruplar")
            gruplar = cursor.fetchall()
            conn.close()
        except Exception:
            gruplar = []
        
        text = "🌐 **Ağa Bağlı Gruplar:**\n\n"
        kb = []
        if not gruplar:
            text += "Henüz kayıtlı grup yok."
        else:
            for grp in gruplar:
                if grp[1] and "t.me" in grp[1]:
                    kb.append([InlineKeyboardButton(f"🔗 {grp[0]}", url=grp[1])])
                else:
                    text += f"• {grp[0]}\n"
        kb.append([InlineKeyboardButton("🔙 Geri", callback_data="menu_fed")])
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "menu_zaman":
        text = "⏱️ **Zamanlı Yayın Motoru:**\n\nHer 30 dakikada bir otomatik gönderim yapılır.\n• **Nasıl Eklenir?** İstediğiniz medya veya mesaja **yanıt verip** `/ekle` yazın.\n• **Nasıl Listelenir?** `/yayinlar` yazın.\n• **Nasıl Silinir?** `/sil <ID>` yazın."
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_kb))

    elif data == "menu_log":
        try:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT zaman, islem_turu, detay FROM bot_loglar ORDER BY id DESC LIMIT 5")
            kayitlar = cursor.fetchall()
            conn.close()
        except Exception:
            kayitlar = []
        text = "📜 **Son Sistem Logları:**\n\n"
        if not kayitlar:
            text += "Kayıt bulunmuyor."
        else:
            for k in kayitlar:
                text += f"⏱ `{k[0]}` | **{k[1]}**: {k[2]}\n\n"
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_kb))

    elif data == "menu_guide":
        guide_text = (
            "📖 **Golden Guardian Pro - Detaylı Kullanım Kılavuzu**\n\n"
            "1️⃣ **Moderasyon ve Cezalandırma Komutları:**\n"
            "• `/defol` (Mesaja Yanıt Vererek): Kullanıcıyı gruptan kalıcı olarak atar.\n"
            "• `/itaat` (Mesaja Yanıt Vererek): Kullanıcıyı 10 dakika süreyle susturur (Mute).\n"
            "• `/kralice` (Mesaja Yanıt Vererek): Kullanıcının mutesini ve cezalarını kaldırır.\n\n"
            "2️⃣ **Federasyon ve Ağ Komutları:**\n"
            "• `/fedbagla` veya Arayüzden Tek Tık: Grubu federasyona bağlar.\n"
            "• `/idam` (Mesaja Yanıt Vererek): Kullanıcıyı ağdaki **tüm gruplardan** aynı anda banlar.\n"
            "• `/genelaf` (Mesaja Yanıt Vererek): Küresel banı kaldırır.\n\n"
            "3️⃣ **Otomatik Yayın ve Araçlar:**\n"
            "• `/portal`: Sohbet butonu üretir.\n"
            "• `/ekle` (Mesaja Yanıt): Periyodik yayına içerik ekler.\n"
            "• `/yayinlar`: Yayınları listeler.\n"
            "• `/sil <ID>`: Belirtilen yayını siler."
        )
        await query.edit_message_text(text=guide_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(ana_menu_kb))

    elif data == "menu_pin":
        try:
            await query.message.pin()
            await query.answer("Panel gruba başarıyla sabitlendi!", show_alert=True)
        except Exception:
            await query.answer("Sabitleme yetkisi eksik!", show_alert=True)

    elif data == "menu_ana":
        keyboard = [
            [InlineKeyboardButton("📊 İstatistikler", callback_data="menu_stats"), InlineKeyboardButton("💬 Sohbet Portalı", callback_data="menu_portal")],
            [InlineKeyboardButton("🌐 Federasyon Ağı", callback_data="menu_fed"), InlineKeyboardButton("⏱️ Zamanlı Yayın", callback_data="menu_zaman")],
            [InlineKeyboardButton("📜 Log Kayıtları", callback_data="menu_log"), InlineKeyboardButton("📖 Kullanım Kılavuzu", callback_data="menu_guide")],
            [InlineKeyboardButton("📌 Paneli Sabitle", callback_data="menu_pin")]
        ]
        await query.edit_message_text(text="🛡️ **Golden Guardian Pro - Yönetim Paneli**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# --- ESKİ KOMUTLAR (Kusursuz Çalışmaya Devam Eder) ---
async def portal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_admin(update, update.message.from_user.id): return
    link = f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    kb = [[InlineKeyboardButton("💬 Kesintisiz Sohbete Katıl", url=link)]]
    await update.message.reply_text("👑 **Sohbet Portalı**", reply_markup=InlineKeyboardMarkup(kb))

async def fedbagla_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not await is_admin(update, update.message.from_user.id): return
    link = f"https://t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id).replace('-100','')}/1"
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO federasyon_gruplar (chat_id, grup_adi, grup_linki) VALUES (?, ?, ?)", (chat.id, chat.title, link))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Grup federasyon ağına bağlandı!")

async def defol_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    target_id, info = await resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("⚠️ Hedef bulunamadı. Lütfen kullanıcının mesajına yanıt verin.")
        return
    try:
        await update.effective_chat.ban_member(target_id)
        await update.message.reply_text(f"🔨 Kullanıcı gruptan atıldı. ({info})")
    except Exception as e:
        await update.message.reply_text(f"Hata: {e}")

async def itaat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    target_id, info = await resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("⚠️ Hedef bulunamadı. Lütfen kullanıcının mesajına yanıt verin.")
        return
    try:
        await update.effective_chat.restrict_member(target_id, permissions=ChatPermissions(can_send_messages=False), until_date=timedelta(minutes=10))
        await update.message.reply_text(f"🤐 Kullanıcı 10 dk susturuldu. ({info})")
    except Exception as e:
        await update.message.reply_text(f"Hata: {e}")

async def kralice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    target_id, info = await resolve_target(update, context)
    if not target_id: return
    try:
        await update.effective_chat.restrict_member(target_id, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True))
        await update.message.reply_text(f"✨ Mute kaldırıldı. ({info})")
    except Exception as e:
        await update.message.reply_text(f"Hata: {e}")

async def idam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    target_id, info = await resolve_target(update, context)
    if not target_id: return
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO fed_banlar (user_id, sebep) VALUES (?, ?)", (target_id, "Küresel İdam"))
    cursor.execute("SELECT chat_id FROM federasyon_gruplar")
    gruplar = cursor.fetchall()
    conn.commit()
    conn.close()
    sayi = 0
    for g in gruplar:
        try:
            await context.bot.ban_member(chat_id=g[0], user_id=target_id)
            sayi += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await update.message.reply_text(f"⚖️ Küresel idam! {sayi} gruptan banlandı. ({info})")

async def genelaf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    target_id, info = await resolve_target(update, context)
    if not target_id: return
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM fed_banlar WHERE user_id = ?", (target_id,))
    cursor.execute("SELECT chat_id FROM federasyon_gruplar")
    gruplar = cursor.fetchall()
    conn.commit()
    conn.close()
    for g in gruplar:
        try:
            await context.bot.unban_member(chat_id=g[0], user_id=target_id)
            await asyncio.sleep(0.05)
        except Exception: pass
    await update.message.reply_text(f"🕊️ Genel af! Yasaklar kaldırıldı. ({info})")

async def ekle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    msg = update.message.reply_to_message
    if not msg:
        await update.message.reply_text("⚠️ Bir mesaja yanıt verip `/ekle` yazın.")
        return
    tip = "photo" if msg.photo else "text"
    icerik = msg.photo[-1].file_id if msg.photo else msg.text
    aciklama = msg.caption or "" if msg.photo else ""
    if not icerik: return
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO yayin_listesi (chat_id, tip, icerik, aciklama) VALUES (?, ?, ?, ?)", (update.effective_chat.id, tip, icerik, aciklama))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Periyodik yayın listesine eklendi!")

async def yayinlar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT id, tip FROM yayin_listesi WHERE chat_id = ?", (update.effective_chat.id,))
    yayinlar = cursor.fetchall()
    conn.close()
    if not yayinlar:
        await update.message.reply_text("Aktif yayın yok.")
        return
    metin = "📜 **Yayınlar:**\n"
    for y in yayinlar: metin += f"• ID: `{y[0]}` | Tip: {y[1]}\n"
    await update.message.reply_text(metin, parse_mode="Markdown")

async def sil_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, update.message.from_user.id): return
    if not context.args: return
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM yayin_listesi WHERE id = ? AND chat_id = ?", (context.args[0], update.effective_chat.id))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Yayın silindi.")

async def otomatik_yayin_motoru(context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, tip, icerik, aciklama FROM yayin_listesi")
        yayinlar = cursor.fetchall()
        cursor.execute("SELECT chat_id FROM zamanli_ayar WHERE aktif_durum = 'AKTİF'")
        aktif_gruplar = [row[0] for row in cursor.fetchall()]
        conn.close()
        if not yayinlar or not aktif_gruplar: return
        for y in yayinlar:
            if y[0] in aktif_gruplar:
                link = f"https://t.me/c/{str(y[0]).replace('-100','')}/1"
                kb = [[InlineKeyboardButton("💬 Sohbete Katıl", url=link)]]
                try:
                    if y[1] == "photo": await context.bot.send_photo(chat_id=y[0], photo=y[2], caption=y[3], reply_markup=InlineKeyboardMarkup(kb))
                    elif y[1] == "text": await context.bot.send_message(chat_id=y[0], text=y[2], reply_markup=InlineKeyboardMarkup(kb))
                except Exception: pass
    except Exception: pass

async def post_init(application):
    await application.bot.delete_webhook(drop_pending_updates=True)
    if application.job_queue:
        application.job_queue.run_repeating(otomatik_yayin_motoru, interval=1800, first=20)
        logger.info("Zamanlayıcı aktif.")

def main():
    if not TOKEN: return
    threading.Thread(target=start_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("portal", portal_cmd))
    app.add_handler(CommandHandler("fedbagla", fedbagla_cmd))
    app.add_handler(CommandHandler("defol", defol_cmd))
    app.add_handler(CommandHandler("itaat", itaat_cmd))
    app.add_handler(CommandHandler("kralice", kralice_cmd))
    app.add_handler(CommandHandler("idam", idam_cmd))
    app.add_handler(CommandHandler("genelaf", genelaf_cmd))
    app.add_handler(CommandHandler("ekle", ekle_cmd))
    app.add_handler(CommandHandler("yayinlar", yayinlar_cmd))
    app.add_handler(CommandHandler("sil", sil_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_filter))

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

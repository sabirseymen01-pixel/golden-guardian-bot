import os
import logging
import threading
from datetime import datetime
from flask import Flask, request, redirect, url_for, render_template_string, jsonify

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    BigInteger,
    String,
    Boolean,
    DateTime,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# AYARLAR
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PANEL_KEY = os.getenv("PANEL_KEY", "rosefed")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///rosefed.db")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")

PORT = int(os.getenv("PORT", "10000"))


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("RoseFed")


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# DATABASE
# ============================================================

connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False
    }

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

Base = declarative_base()

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


class Federation(Base):
    __tablename__ = "federations"

    id = Column(Integer, primary_key=True)

    chat_id = Column(BigInteger, unique=True, nullable=False)

    chat_title = Column(String(255), default="Bilinmeyen Grup")

    chat_username = Column(String(255), nullable=True)

    portal_username = Column(String(255), nullable=True)

    duration_minutes = Column(Integer, default=0)

    active = Column(Boolean, default=True)

    portal_message_id = Column(Integer, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )


Base.metadata.create_all(engine)


# ============================================================
# GEÇİCİ VERİLER
# ============================================================

timed_automations = {}

telegram_thread = None
telegram_lock = threading.Lock()


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def get_db():
    return SessionLocal()


def is_admin(update: Update) -> bool:

    if not update.effective_chat:
        return False

    if not update.effective_user:
        return False

    try:

        member = update.effective_chat.get_member(
            update.effective_user.id
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception as e:

        logger.error(
            "Admin kontrolü başarısız: %s",
            e
        )

        return False


async def require_admin(update: Update):

    if not is_admin(update):

        if update.message:

            await update.message.reply_text(
                "❌ Bu komutu yalnızca grup yöneticileri kullanabilir."
            )

        return False

    return True


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = """
🌹 <b>RoseFed Bot</b>

Federasyon ve grup yönetim sistemine hoş geldin.

<b>Temel komutlar:</b>

/bagla @portal
/ayir
/sure 60

<b>Moderasyon:</b>

/ban
/unban
/mute
/unmute

<b>Diğer:</b>

/itaat
/kralice
/yardim

🌹 RoseFed
"""

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ============================================================
# /YARDIM
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = """
🌹 <b>RoseFed Komut Merkezi</b>

━━━━━━━━━━━━━━━━━━

🔗 <b>FEDERASYON</b>

/bagla @portal
Portal grubunu bağlar.

/ayir
Federasyon bağlantısını kaldırır.

/sure 60
Federasyon süresini ayarlar.

━━━━━━━━━━━━━━━━━━

🔨 <b>MODERASYON</b>

/ban
Yanıt verilen kullanıcıyı yasaklar.

/unban
Kullanıcının yasağını kaldırır.

/mute
Kullanıcıyı susturur.

/unmute
Susturmayı kaldırır.

━━━━━━━━━━━━━━━━━━

👑 <b>ÖZEL</b>

/itaat
/kraliçe
/kralice

━━━━━━━━━━━━━━━━━━

🌹 RoseFed
"""

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ============================================================
# /BAGLA
# ============================================================

async def bagla_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(update):
        return

    if not update.effective_chat:
        return

    if not context.args:

        await update.message.reply_text(
            "❌ Kullanım:\n\n"
            "/bagla @portalgrup"
        )

        return

    portal = context.args[0].strip()

    if not portal.startswith("@"):

        portal = "@" + portal

    chat = update.effective_chat

    db = get_db()

    try:

        federation = (
            db.query(Federation)
            .filter(
                Federation.chat_id == chat.id
            )
            .first()
        )

        if federation:

            federation.portal_username = portal
            federation.active = True

        else:

            federation = Federation(
                chat_id=chat.id,
                chat_title=chat.title or "Grup",
                chat_username=(
                    chat.username
                    if hasattr(chat, "username")
                    else None
                ),
                portal_username=portal,
                active=True,
            )

            db.add(federation)

        db.commit()

        buttons = [
            [
                InlineKeyboardButton(
                    "🌹 Gruba Git",
                    url=(
                        f"https://t.me/"
                        f"{chat.username}"
                        if getattr(
                            chat,
                            "username",
                            None
                        )
                        else "https://t.me/"
                        + portal.replace("@", "")
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗 Portal",
                    url=(
                        "https://t.me/"
                        + portal.replace("@", "")
                    ),
                )
            ],
        ]

        keyboard = InlineKeyboardMarkup(buttons)

        message = await update.message.reply_text(
            f"""
🌹 <b>FEDERASYON BAĞLANDI</b>

━━━━━━━━━━━━━━━━━━

📌 Grup:
<b>{chat.title}</b>

🔗 Portal:
<b>{portal}</b>

🟢 Durum:
<b>AKTİF</b>

━━━━━━━━━━━━━━━━━━

RoseFed federasyon sistemi aktif.
""",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        try:

            await message.pin(
                disable_notification=True
            )

        except Exception:
            pass

    except Exception as e:

        db.rollback()

        logger.exception(
            "Federasyon bağlama hatası"
        )

        await update.message.reply_text(
            f"❌ Hata oluştu:\n{e}"
        )

    finally:

        db.close()


# ============================================================
# /AYIR
# ============================================================

async def ayir_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(update):
        return

    chat = update.effective_chat

    db = get_db()

    try:

        federation = (
            db.query(Federation)
            .filter(
                Federation.chat_id == chat.id
            )
            .first()
        )

        if not federation:

            await update.message.reply_text(
                "ℹ️ Bu grup herhangi bir federasyona bağlı değil."
            )

            return

        federation.active = False

        db.commit()

        await update.message.reply_text(
            "🔓 Federasyon bağlantısı kaldırıldı."
        )

    except Exception as e:

        db.rollback()

        await update.message.reply_text(
            f"❌ Hata: {e}"
        )

    finally:

        db.close()


# ============================================================
# /SURE
# ============================================================

async def sure_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(update):
        return

    if not context.args:

        await update.message.reply_text(
            "❌ Kullanım:\n\n"
            "/sure 60"
        )

        return

    try:

        minutes = int(context.args[0])

        if minutes < 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ Süre dakika olarak sayı olmalıdır."
        )

        return

    chat = update.effective_chat

    db = get_db()

    try:

        federation = (
            db.query(Federation)
            .filter(
                Federation.chat_id == chat.id
            )
            .first()
        )

        if not federation:

            federation = Federation(
                chat_id=chat.id,
                chat_title=chat.title or "Grup",
                duration_minutes=minutes,
            )

            db.add(federation)

        else:

            federation.duration_minutes = minutes

        db.commit()

        await update.message.reply_text(
            f"⏱️ Federasyon süresi:\n\n"
            f"<b>{minutes} dakika</b>",
            parse_mode="HTML"
        )

    except Exception as e:

        db.rollback()

        await update.message.reply_text(
            f"❌ Hata: {e}"
        )

    finally:

        db.close()


# ============================================================
# /BAN
# ============================================================

async def ban_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(update):
        return

    if not update.message.reply_to_message:

        await update.message.reply_text(
            "❌ Yasaklamak istediğin kullanıcıya "
            "yanıt vererek /ban yaz."
        )

        return

    user = update.message.reply_to_message.from_user

    try:

        await update.effective_chat.ban_member(
            user.id
        )

        await update.message.reply_text(
            f"🔨 <b>{user.first_name}</b> yasaklandı.",
            parse_mode="HTML"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ban işlemi başarısız:\n{e}"
        )


# ============================================================
# /UNBAN
# ============================================================

async def unban_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(update):
        return

    if not update.message.reply_to_message:

        await update.message.reply_text(
            "❌ Kullanıcı mesajına yanıt ver."
        )

        return

    user = update.message.reply_to_message.from_user

    try:

        await update.effective_chat.unban_member(
            user.id
        )

        await update.message.reply_text(
            f"🔓 <b>{user.first_name}</b> yasağı kaldırıldı.",
            parse_mode="HTML"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Unban başarısız:\n{e}"
        )


# ============================================================
# /MUTE
# ============================================================

async def mute_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(update):
        return

    if not update.message.reply_to_message:

        await update.message.reply_text(
            "❌ Susturmak istediğin kullanıcıya "
            "yanıt ver."
        )

        return

    user = update.message.reply_to_message.from_user

    try:

        permissions = ChatPermissions(
            can_send_messages=False
        )

        await update.effective_chat.restrict_member(
            user.id,
            permissions=permissions
        )

        await update.message.reply_text(
            f"🔇 <b>{user.first_name}</b> susturuldu.",
            parse_mode="HTML"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Mute başarısız:\n{e}"
        )


# ============================================================
# /UNMUTE
# ============================================================

async def unmute_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(update):
        return

    if not update.message.reply_to_message:

        await update.message.reply_text(
            "❌ Kullanıcı mesajına yanıt ver."
        )

        return

    user = update.message.reply_to_message.from_user

    try:

        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )

        await update.effective_chat.restrict_member(
            user.id,
            permissions=permissions
        )

        await update.message.reply_text(
            f"🔊 <b>{user.first_name}</b> susturması kaldırıldı.",
            parse_mode="HTML"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Unmute başarısız:\n{e}"
        )


# ============================================================
# /İTAAT
# ============================================================

async def itaat_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👑 İtaat sistemi aktif.\n\n"
        "🌹 RoseFed"
    )


# ============================================================
# /KRALİÇE
# ============================================================

async def kralice_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👑 Kraliçe modu aktif.\n\n"
        "🌹 RoseFed"
    )


# ============================================================
# FEDERASYON İSTATİSTİKLERİ
# ============================================================

def get_stats():

    db = get_db()

    try:

        total = db.query(Federation).count()

        active = (
            db.query(Federation)
            .filter(
                Federation.active == True
            )
            .count()
        )

        return total, active

    finally:

        db.close()


# ============================================================
# WEB PANEL HTML
# ============================================================

PANEL_HTML = """

<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>RoseFed Panel</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    background:
        #08080d;

    color: #fff;

}

.container {

    max-width: 1200px;

    margin: auto;

    padding: 30px;

}

.header {

    display: flex;

    justify-content:
        space-between;

    align-items:
        center;

    margin-bottom: 30px;

}

.logo {

    font-size: 30px;

    font-weight: bold;

}

.logo span {

    color: #ff4fa3;

}

.cards {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(200px, 1fr)
        );

    gap: 20px;

    margin-bottom: 30px;

}

.card {

    background: #13131b;

    border:
        1px solid #292936;

    border-radius: 18px;

    padding: 25px;

}

.card h3 {

    margin-top: 0;

    color: #aaa;

}

.number {

    font-size: 36px;

    font-weight: bold;

    color: #ff4fa3;

}

.panel {

    background: #13131b;

    border:
        1px solid #292936;

    border-radius: 18px;

    padding: 25px;

}

table {

    width: 100%;

    border-collapse:
        collapse;

}

th,
td {

    text-align:
        left;

    padding: 15px;

    border-bottom:
        1px solid #292936;

}

th {

    color: #aaa;

}

.badge {

    display:
        inline-block;

    padding:
        6px 12px;

    border-radius:
        999px;

    background:
        #183c27;

    color:
        #5dff9a;

}

.btn {

    display:
        inline-block;

    padding:
        8px 13px;

    border-radius:
        9px;

    text-decoration:
        none;

    background:
        #ff4fa3;

    color:
        white;

}

.btn.danger {

    background:
        #b72c4d;

}

.empty {

    color:
        #888;

    padding:
        30px;

    text-align:
        center;

}

</style>

</head>

<body>

<div class="container">

<div class="header">

<div class="logo">
🌹 Rose<span>Fed</span>
</div>

<div>
Federation Control Panel
</div>

</div>


<div class="cards">

<div class="card">

<h3>Toplam Federasyon</h3>

<div class="number">
{{ total }}
</div>

</div>


<div class="card">

<h3>Aktif Federasyon</h3>

<div class="number">
{{ active }}
</div>

</div>


<div class="card">

<h3>Bot Durumu</h3>

<div class="number">
🟢
</div>

</div>

</div>


<div class="panel">

<h2>Federasyonlar</h2>

{% if federations %}

<table>

<thead>

<tr>

<th>Grup</th>

<th>Portal</th>

<th>Durum</th>

<th>İşlem</th>

</tr>

</thead>

<tbody>

{% for fed in federations %}

<tr>

<td>
{{ fed.chat_title }}
</td>

<td>
{{ fed.portal_username or "-" }}
</td>

<td>

{% if fed.active %}

<span class="badge">
AKTİF
</span>

{% else %}

Pasif

{% endif %}

</td>

<td>

<a
class="btn danger"
href="/federation/delete/{{ fed.chat_id }}?key={{ key }}"
onclick="return confirm('Federasyonu silmek istediğine emin misin?')"
>
Sil
</a>

</td>

</tr>

{% endfor %}

</tbody>

</table>

{% else %}

<div class="empty">

Henüz federasyon bulunmuyor.

</div>

{% endif %}

</div>

</div>

</body>

</html>

"""


# ============================================================
# WEB PANEL AUTH
# ============================================================

def panel_authorized():

    key = request.args.get("key")

    return key == PANEL_KEY


# ============================================================
# ANA PANEL
# ============================================================

@app.route("/")
def dashboard():

    if not panel_authorized():

        return """
        <html>
        <body style="
            background:#08080d;
            color:white;
            font-family:Arial;
            text-align:center;
            padding:100px;
        ">

        <h1>🌹 RoseFed</h1>

        <p>Panel anahtarı gerekli.</p>

        </body>
        </html>
        """, 401

    db = get_db()

    try:

        federations = (
            db.query(Federation)
            .order_by(
                Federation.created_at.desc()
            )
            .all()
        )

        total, active = get_stats()

        return render_template_string(
            PANEL_HTML,
            federations=federations,
            total=total,
            active=active,
            key=PANEL_KEY,
        )

    finally:

        db.close()


# ============================================================
# FEDERASYON SİL
# ============================================================

@app.route(
    "/federation/delete/<int:chat_id>"
)
def delete_federation(chat_id):

    if not panel_authorized():

        return "Unauthorized", 401

    db = get_db()

    try:

        federation = (
            db.query(Federation)
            .filter(
                Federation.chat_id == chat_id
            )
            .first()
        )

        if federation:

            db.delete(federation)

            db.commit()

        return redirect(
            url_for(
                "dashboard",
                key=PANEL_KEY
            )
        )

    finally:

        db.close()


# ============================================================
# API
# ============================================================

@app.route("/api/federations")
def api_federations():

    if not panel_authorized():

        return jsonify({
            "error": "Unauthorized"
        }), 401

    db = get_db()

    try:

        federations = (
            db.query(Federation)
            .order_by(
                Federation.created_at.desc()
            )
            .all()
        )

        return jsonify([
            {
                "id": fed.id,
                "chat_id": fed.chat_id,
                "chat_title": fed.chat_title,
                "chat_username": fed.chat_username,
                "portal_username": fed.portal_username,
                "duration_minutes":
                    fed.duration_minutes,
                "active": fed.active,
                "created_at":
                    fed.created_at.isoformat()
                    if fed.created_at
                    else None,
            }
            for fed in federations
        ])

    finally:

        db.close()


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "service": "RoseFed",
        "telegram": bool(TOKEN),
        "database": True,
    })


# ============================================================
# TELEGRAM BOT
# ============================================================

def build_bot():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN ayarlanmamış."
        )

    bot = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    bot.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    bot.add_handler(
        CommandHandler(
            ["yardim", "help"],
            help_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "bagla",
            bagla_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "ayir",
            ayir_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "sure",
            sure_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "ban",
            ban_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "unban",
            unban_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "mute",
            mute_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "unmute",
            unmute_command
        )
    )

    bot.add_handler(
        CommandHandler(
            "itaat",
            itaat_command
        )
    )

    bot.add_handler(
        CommandHandler(
            ["kralice", "krallice", "kraliçe"],
            kralice_command
        )
    )

    return bot


# ============================================================
# TELEGRAM POLLING
# ============================================================

def run_telegram():

    try:

        logger.info(
            "Telegram bot başlatılıyor..."
        )

        bot = build_bot()

        bot.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )

    except Exception as e:

        logger.exception(
            "Telegram bot hatası: %s",
            e
        )


# ============================================================
# TELEGRAM THREAD
# ============================================================

def start_telegram_once():

    global telegram_thread

    with telegram_lock:

        if (
            telegram_thread
            and telegram_thread.is_alive()
        ):

            return

        telegram_thread = threading.Thread(
            target=run_telegram,
            name="RoseFedTelegram",
            daemon=True,
        )

        telegram_thread.start()

        logger.info(
            "Telegram thread başlatıldı."
        )


# ============================================================
# RENDER / GUNICORN IMPORT
# ============================================================

if TOKEN:

    start_telegram_once()

else:

    logger.warning(
        "TELEGRAM_BOT_TOKEN bulunamadı. "
        "Web panel çalışacak fakat Telegram botu başlamayacak."
    )


# ============================================================
# LOCAL ÇALIŞTIRMA
# ============================================================

if __name__ == "__main__":

    logger.info(
        "RoseFed web paneli başlıyor: %s",
        PORT
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )

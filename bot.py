import os
import sys
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    request,
    jsonify,
    redirect,
    render_template_string,
)

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
# ROSEFED
# Telegram Federation + Web Panel
# Render Webhook Architecture
# ============================================================


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger("RoseFed")


# ============================================================
# ENV
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

PANEL_KEY = os.getenv(
    "PANEL_KEY",
    "change-this-panel-key",
).strip()

PUBLIC_URL = os.getenv(
    "PUBLIC_URL",
    "",
).strip().rstrip("/")

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    "",
).strip()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///rosefed.db",
).strip()

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# DATABASE
# ============================================================

db_connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    db_connect_args = {
        "check_same_thread": False
    }

engine = create_engine(
    DATABASE_URL,
    connect_args=db_connect_args,
    pool_pre_ping=True,
)

Base = declarative_base()

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


class Federation(Base):

    __tablename__ = "federations"

    id = Column(
        Integer,
        primary_key=True,
    )

    chat_id = Column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )

    chat_title = Column(
        String(255),
        default="Grup",
    )

    chat_username = Column(
        String(255),
        nullable=True,
    )

    portal_username = Column(
        String(255),
        nullable=True,
    )

    duration_minutes = Column(
        Integer,
        default=0,
    )

    active = Column(
        Boolean,
        default=True,
    )

    portal_message_id = Column(
        Integer,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )


class ModerationLog(Base):

    __tablename__ = "moderation_logs"

    id = Column(
        Integer,
        primary_key=True,
    )

    chat_id = Column(
        BigInteger,
        nullable=False,
        index=True,
    )

    user_id = Column(
        BigInteger,
        nullable=False,
    )

    action = Column(
        String(50),
        nullable=False,
    )

    moderator_id = Column(
        BigInteger,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )


Base.metadata.create_all(engine)


# ============================================================
# TELEGRAM APPLICATION
# ============================================================

telegram_app = None

telegram_loop = None

telegram_ready = threading.Event()

telegram_start_error = None

telegram_thread = None

telegram_lock = threading.Lock()


# ============================================================
# DATABASE HELPERS
# ============================================================

def db_session():
    return SessionLocal()


# ============================================================
# TELEGRAM HELPERS
# ============================================================

async def is_group_admin(
    update: Update,
) -> bool:

    if not update.effective_chat:
        return False

    if not update.effective_user:
        return False

    try:

        member = await update.effective_chat.get_member(
            update.effective_user.id
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception as exc:

        logger.exception(
            "Admin kontrolü başarısız: %s",
            exc,
        )

        return False


async def admin_required(
    update: Update,
) -> bool:

    if await is_group_admin(update):
        return True

    if update.effective_message:

        await update.effective_message.reply_text(
            "❌ Bu komutu yalnızca grup yöneticileri kullanabilir."
        )

    return False


def get_target_user(update: Update):

    if not update.effective_message:
        return None

    replied = (
        update.effective_message.reply_to_message
    )

    if not replied:
        return None

    return replied.from_user


async def save_log(
    chat_id,
    user_id,
    action,
    moderator_id=None,
):

    db = db_session()

    try:

        db.add(
            ModerationLog(
                chat_id=chat_id,
                user_id=user_id,
                action=action,
                moderator_id=moderator_id,
            )
        )

        db.commit()

    except Exception:

        db.rollback()

        logger.exception(
            "Moderasyon logu kaydedilemedi."
        )

    finally:

        db.close()


# ============================================================
# START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        """
🌹 RoseFed Bot

Federasyon ve grup yönetim sistemi.

🔗 FEDERASYON

/bagla @portal
/ayir
/sure 60

🔨 MODERASYON

/ban
/unban
/mute
/unmute

👑 DİĞER

/itaat
/kralice
/yardim

🌹 RoseFed aktif.
"""
    )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        """
🌹 ROSEFED KOMUTLARI

━━━━━━━━━━━━━━━━━━

🔗 FEDERASYON

/bagla @portal

Grubu federasyona bağlar.

 /ayir

Federasyon bağlantısını kaldırır.

/sure 60

Federasyon süresini dakika olarak ayarlar.

━━━━━━━━━━━━━━━━━━

🔨 MODERASYON

/ban

Yanıt verilen kullanıcıyı yasaklar.

/unban

Yanıt verilen kullanıcının yasağını kaldırır.

/mute

Yanıt verilen kullanıcıyı susturur.

/unmute

Susturmayı kaldırır.

━━━━━━━━━━━━━━━━━━

👑 ÖZEL

/itaat
/kralice

━━━━━━━━━━━━━━━━━━

🌹 RoseFed
"""
    )


# ============================================================
# BAGLA
# ============================================================

async def bagla_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await admin_required(update):
        return

    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    if not context.args:

        await update.effective_message.reply_text(
            "❌ Kullanım:\n\n/bagla @portal"
        )

        return

    portal = context.args[0].strip()

    if not portal.startswith("@"):
        portal = "@" + portal

    chat = update.effective_chat

    db = db_session()

    try:

        federation = (
            db.query(Federation)
            .filter(
                Federation.chat_id == chat.id
            )
            .first()
        )

        if federation:

            federation.chat_title = (
                chat.title or "Grup"
            )

            federation.chat_username = (
                getattr(
                    chat,
                    "username",
                    None,
                )
            )

            federation.portal_username = portal
            federation.active = True

        else:

            federation = Federation(
                chat_id=chat.id,
                chat_title=chat.title or "Grup",
                chat_username=getattr(
                    chat,
                    "username",
                    None,
                ),
                portal_username=portal,
                active=True,
            )

            db.add(federation)

        db.commit()

        buttons = []

        if getattr(
            chat,
            "username",
            None,
        ):

            buttons.append(
                [
                    InlineKeyboardButton(
                        "🌹 Gruba Git",
                        url=(
                            "https://t.me/"
                            + chat.username
                        ),
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🔗 Portal",
                    url=(
                        "https://t.me/"
                        + portal.lstrip("@")
                    ),
                )
            ]
        )

        keyboard = InlineKeyboardMarkup(
            buttons
        )

        message = await update.effective_message.reply_text(
            f"""
🌹 FEDERASYON BAĞLANDI

━━━━━━━━━━━━━━━━━━

📌 Grup:
{chat.title}

🔗 Portal:
{portal}

🟢 Durum:
AKTİF

━━━━━━━━━━━━━━━━━━

RoseFed federasyon sistemi aktif.
""",
            reply_markup=keyboard,
        )

        federation.portal_message_id = (
            message.message_id
        )

        db.commit()

        try:

            await message.pin(
                disable_notification=True
            )

        except Exception as exc:

            logger.info(
                "Mesaj sabitlenemedi: %s",
                exc,
            )

    except Exception as exc:

        db.rollback()

        logger.exception(
            "Bagla hatası: %s",
            exc,
        )

        await update.effective_message.reply_text(
            "❌ Federasyon bağlanırken bir hata oluştu."
        )

    finally:

        db.close()


# ============================================================
# AYIR
# ============================================================

async def ayir_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await admin_required(update):
        return

    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    db = db_session()

    try:

        federation = (
            db.query(Federation)
            .filter(
                Federation.chat_id
                == update.effective_chat.id
            )
            .first()
        )

        if not federation:

            await update.effective_message.reply_text(
                "ℹ️ Bu grup bir federasyona bağlı değil."
            )

            return

        federation.active = False

        db.commit()

        await update.effective_message.reply_text(
            "🔓 Federasyon bağlantısı kaldırıldı."
        )

    except Exception as exc:

        db.rollback()

        logger.exception(
            "Ayır hatası: %s",
            exc,
        )

        await update.effective_message.reply_text(
            "❌ Federasyon ayrılırken hata oluştu."
        )

    finally:

        db.close()


# ============================================================
# SURE
# ============================================================

async def sure_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await admin_required(update):
        return

    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    if not context.args:

        await update.effective_message.reply_text(
            "❌ Kullanım:\n\n/sure 60"
        )

        return

    try:

        minutes = int(
            context.args[0]
        )

        if minutes < 0:
            raise ValueError

    except ValueError:

        await update.effective_message.reply_text(
            "❌ Süre pozitif bir sayı olmalıdır."
        )

        return

    db = db_session()

    try:

        federation = (
            db.query(Federation)
            .filter(
                Federation.chat_id
                == update.effective_chat.id
            )
            .first()
        )

        if not federation:

            federation = Federation(
                chat_id=update.effective_chat.id,
                chat_title=(
                    update.effective_chat.title
                    or "Grup"
                ),
                duration_minutes=minutes,
                active=True,
            )

            db.add(federation)

        else:

            federation.duration_minutes = minutes

        db.commit()

        await update.effective_message.reply_text(
            f"⏱️ Federasyon süresi "
            f"<b>{minutes} dakika</b> olarak ayarlandı.",
            parse_mode="HTML",
        )

    except Exception as exc:

        db.rollback()

        logger.exception(
            "Sure hatası: %s",
            exc,
        )

        await update.effective_message.reply_text(
            "❌ Süre ayarlanamadı."
        )

    finally:

        db.close()


# ============================================================
# BAN
# ============================================================

async def ban_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await admin_required(update):
        return

    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    target = get_target_user(update)

    if not target:

        await update.effective_message.reply_text(
            "❌ Yasaklamak istediğin kişiye "
            "yanıt vererek /ban yaz."
        )

        return

    try:

        await update.effective_chat.ban_member(
            target.id
        )

        await save_log(
            update.effective_chat.id,
            target.id,
            "ban",
            update.effective_user.id,
        )

        await update.effective_message.reply_text(
            f"🔨 {target.first_name} yasaklandı."
        )

    except Exception as exc:

        logger.exception(
            "Ban hatası: %s",
            exc,
        )

        await update.effective_message.reply_text(
            f"❌ Ban işlemi başarısız.\n\n{exc}"
        )


# ============================================================
# UNBAN
# ============================================================

async def unban_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await admin_required(update):
        return

    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    target = get_target_user(update)

    if not target:

        await update.effective_message.reply_text(
            "❌ Yasağı kaldırmak istediğin kişiye "
            "yanıt vererek /unban yaz."
        )

        return

    try:

        await update.effective_chat.unban_member(
            target.id
        )

        await save_log(
            update.effective_chat.id,
            target.id,
            "unban",
            update.effective_user.id,
        )

        await update.effective_message.reply_text(
            f"🔓 {target.first_name} yasağı kaldırıldı."
        )

    except Exception as exc:

        logger.exception(
            "Unban hatası: %s",
            exc,
        )

        await update.effective_message.reply_text(
            f"❌ Unban başarısız.\n\n{exc}"
        )


# ============================================================
# MUTE
# ============================================================

async def mute_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await admin_required(update):
        return

    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    target = get_target_user(update)

    if not target:

        await update.effective_message.reply_text(
            "❌ Susturmak istediğin kişiye "
            "yanıt vererek /mute yaz."
        )

        return

    try:

        permissions = ChatPermissions(
            can_send_messages=False
        )

        await update.effective_chat.restrict_member(
            target.id,
            permissions=permissions,
        )

        await save_log(
            update.effective_chat.id,
            target.id,
            "mute",
            update.effective_user.id,
        )

        await update.effective_message.reply_text(
            f"🔇 {target.first_name} susturuldu."
        )

    except Exception as exc:

        logger.exception(
            "Mute hatası: %s",
            exc,
        )

        await update.effective_message.reply_text(
            f"❌ Mute başarısız.\n\n{exc}"
        )


# ============================================================
# UNMUTE
# ============================================================

async def unmute_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await admin_required(update):
        return

    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    target = get_target_user(update)

    if not target:

        await update.effective_message.reply_text(
            "❌ Susturmasını kaldırmak istediğin "
            "kişiye yanıt vererek /unmute yaz."
        )

        return

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
            target.id,
            permissions=permissions,
        )

        await save_log(
            update.effective_chat.id,
            target.id,
            "unmute",
            update.effective_user.id,
        )

        await update.effective_message.reply_text(
            f"🔊 {target.first_name} artık konuşabilir."
        )

    except Exception as exc:

        logger.exception(
            "Unmute hatası: %s",
            exc,
        )

        await update.effective_message.reply_text(
            f"❌ Unmute başarısız.\n\n{exc}"
        )


# ============================================================
# İTAAT
# ============================================================

async def itaat_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_message:
        return

    user = update.effective_user

    name = (
        user.first_name
        if user
        else "Kullanıcı"
    )

    await update.effective_message.reply_text(
        f"👑 {name}, RoseFed emri aldı."
    )


# ============================================================
# KRALİÇE
# ============================================================

async def kralice_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "👑 Kraliçe modu aktif.\n\n🌹 RoseFed"
    )


# ============================================================
# BOT OLUŞTUR
# ============================================================

def create_telegram_application():

    global telegram_app

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN bulunamadı."
        )

    telegram_app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            ["yardim", "help"],
            help_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "bagla",
            bagla_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "ayir",
            ayir_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "sure",
            sure_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "ban",
            ban_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "unban",
            unban_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "mute",
            mute_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "unmute",
            unmute_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "itaat",
            itaat_command,
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            ["kralice", "krallice"],
            kralice_command,
        )
    )

    return telegram_app


# ============================================================
# TELEGRAM ASYNC LOOP
# ============================================================

async def telegram_worker():

    global telegram_start_error

    try:

        logger.info(
            "Telegram Application hazırlanıyor..."
        )

        application = (
            create_telegram_application()
        )

        await application.initialize()

        await application.start()

        webhook_url = (
            PUBLIC_URL
            + "/telegram/webhook"
        )

        if not PUBLIC_URL:

            raise RuntimeError(
                "PUBLIC_URL ayarlanmamış. "
                "Örnek: https://rosefed-bot.onrender.com"
            )

        webhook_kwargs = {
            "url": webhook_url,
            "allowed_updates": Update.ALL_TYPES,
            "drop_pending_updates": True,
        }

        if WEBHOOK_SECRET:

            webhook_kwargs[
                "secret_token"
            ] = WEBHOOK_SECRET

        await application.bot.set_webhook(
            **webhook_kwargs
        )

        logger.info(
            "Telegram webhook aktif: %s",
            webhook_url,
        )

        telegram_ready.set()

        # Event loop'u canlı tut.
        await asyncio.Event().wait()

    except Exception as exc:

        telegram_start_error = str(exc)

        logger.exception(
            "Telegram başlatılamadı: %s",
            exc,
        )

        telegram_ready.clear()


def telegram_thread_target():

    global telegram_loop

    telegram_loop = asyncio.new_event_loop()

    asyncio.set_event_loop(
        telegram_loop
    )

    try:

        telegram_loop.run_until_complete(
            telegram_worker()
        )

    except Exception as exc:

        logger.exception(
            "Telegram event loop hatası: %s",
            exc,
        )

    finally:

        telegram_loop.close()


def start_telegram():

    global telegram_thread

    if not TOKEN:

        logger.error(
            "TELEGRAM_BOT_TOKEN yok."
        )

        return

    with telegram_lock:

        if (
            telegram_thread
            and telegram_thread.is_alive()
        ):

            return

        telegram_thread = threading.Thread(
            target=telegram_thread_target,
            name="RoseFedTelegram",
            daemon=True,
        )

        telegram_thread.start()

        logger.info(
            "Telegram thread başlatıldı."
        )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.post("/telegram/webhook")
def telegram_webhook():

    if not TOKEN:

        return jsonify({
            "ok": False,
            "error": "Bot token missing",
        }), 503

    if not telegram_ready.is_set():

        return jsonify({
            "ok": False,
            "error": "Telegram bot not ready",
        }), 503

    if WEBHOOK_SECRET:

        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )

        if received_secret != WEBHOOK_SECRET:

            logger.warning(
                "Geçersiz Telegram webhook secret."
            )

            return jsonify({
                "ok": False,
                "error": "Unauthorized",
            }), 403

    try:

        data = request.get_json(
            silent=True
        )

        if not data:

            return jsonify({
                "ok": False,
                "error": "Invalid JSON",
            }), 400

        update = Update.de_json(
            data,
            telegram_app.bot,
        )

        future = asyncio.run_coroutine_threadsafe(
            telegram_app.update_queue.put(
                update
            ),
            telegram_loop,
        )

        # Kuyruğa gönderme işleminin kabul edildiğini
        # kontrol et.
        future.result(
            timeout=4
        )

        return jsonify({
            "ok": True,
        })

    except Exception as exc:

        logger.exception(
            "Webhook işleme hatası: %s",
            exc,
        )

        return jsonify({
            "ok": False,
            "error": "Webhook processing failed",
        }), 500


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    db_ok = False

    try:

        db = db_session()

        db.execute(
            "SELECT 1"
        )

        db.close()

        db_ok = True

    except Exception as exc:

        logger.error(
            "Health DB hatası: %s",
            exc,
        )

    return jsonify({
        "status": "ok",
        "service": "RoseFed",
        "web": True,
        "database": db_ok,
        "telegram_configured": bool(TOKEN),
        "telegram_ready": (
            telegram_ready.is_set()
        ),
        "webhook": bool(
            PUBLIC_URL
        ),
    }), 200


# ============================================================
# PANEL AUTH
# ============================================================

def panel_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        key = request.args.get(
            "key",
            "",
        )

        if key != PANEL_KEY:

            return (
                """
                <!DOCTYPE html>
                <html>
                <body style="
                    background:#08080d;
                    color:white;
                    font-family:Arial;
                    text-align:center;
                    padding:100px;
                ">
                <h1>🌹 RoseFed</h1>
                <p>Yetkisiz erişim.</p>
                </body>
                </html>
                """,
                401,
            )

        return func(*args, **kwargs)

    return wrapper


# ============================================================
# PANEL
# ============================================================

PANEL_HTML = """
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
>

<title>RoseFed Control Panel</title>

<style>

* {
    box-sizing:border-box;
}

body {

    margin:0;

    background:#07070b;

    color:#fff;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.container {

    max-width:1200px;

    margin:auto;

    padding:30px;
}

.header {

    display:flex;

    justify-content:space-between;

    align-items:center;

    margin-bottom:30px;
}

.logo {

    font-size:32px;

    font-weight:800;
}

.logo span {

    color:#ff4fa3;
}

.status {

    padding:8px 14px;

    background:#123a24;

    color:#63ff9b;

    border-radius:999px;

}

.cards {

    display:grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(200px,1fr)
        );

    gap:20px;

    margin-bottom:25px;
}

.card {

    background:#12121a;

    border:
        1px solid #272733;

    border-radius:18px;

    padding:25px;
}

.card-title {

    color:#92929f;

    margin-bottom:10px;
}

.number {

    font-size:36px;

    font-weight:bold;

    color:#ff4fa3;
}

.panel {

    background:#12121a;

    border:
        1px solid #272733;

    border-radius:18px;

    padding:25px;

    overflow:auto;
}

table {

    width:100%;

    border-collapse:collapse;
}

th,
td {

    text-align:left;

    padding:14px;

    border-bottom:
        1px solid #272733;
}

th {

    color:#888895;
}

.badge {

    padding:6px 11px;

    border-radius:999px;

    background:#123a24;

    color:#63ff9b;
}

.badge.off {

    background:#3b2028;

    color:#ff718b;
}

.btn {

    display:inline-block;

    padding:8px 12px;

    border-radius:9px;

    background:#ff4fa3;

    color:#fff;

    text-decoration:none;
}

.btn-danger {

    background:#a52f4d;
}

.empty {

    color:#777;

    padding:30px;

    text-align:center;
}

.small {

    color:#888;

    font-size:13px;
}

</style>

</head>

<body>

<div class="container">

<div class="header">

<div class="logo">
🌹 Rose<span>Fed</span>
</div>

<div class="status">
🟢 WEB ONLINE
</div>

</div>


<div class="cards">

<div class="card">

<div class="card-title">
Toplam Federasyon
</div>

<div class="number">
{{ total }}
</div>

</div>


<div class="card">

<div class="card-title">
Aktif Federasyon
</div>

<div class="number">
{{ active }}
</div>

</div>


<div class="card">

<div class="card-title">
Telegram
</div>

<div class="number">
{{ telegram }}
</div>

</div>


<div class="card">

<div class="card-title">
Database
</div>

<div class="number">
{{ database }}
</div>

</div>

</div>


<div class="panel">

<h2>
Federasyonlar
</h2>

{% if federations %}

<table>

<thead>

<tr>

<th>Grup</th>

<th>Chat ID</th>

<th>Portal</th>

<th>Süre</th>

<th>Durum</th>

<th>İşlem</th>

</tr>

</thead>

<tbody>

{% for fed in federations %}

<tr>

<td>

<strong>
{{ fed.chat_title }}
</strong>

<br>

<span class="small">
{{ fed.chat_username or "username yok" }}
</span>

</td>

<td>
{{ fed.chat_id }}
</td>

<td>
{{ fed.portal_username or "-" }}
</td>

<td>
{{ fed.duration_minutes }} dk
</td>

<td>

{% if fed.active %}

<span class="badge">
AKTİF
</span>

{% else %}

<span class="badge off">
PASİF
</span>

{% endif %}

</td>

<td>

<a
class="btn btn-danger"
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


@app.get("/")
@panel_required
def dashboard():

    db = db_session()

    try:

        federations = (
            db.query(Federation)
            .order_by(
                Federation.created_at.desc()
            )
            .all()
        )

        total = (
            db.query(Federation)
            .count()
        )

        active = (
            db.query(Federation)
            .filter(
                Federation.active.is_(True)
            )
            .count()
        )

        database_status = "🟢"

        telegram_status = (
            "🟢"
            if telegram_ready.is_set()
            else "🔴"
        )

        return render_template_string(
            PANEL_HTML,
            federations=federations,
            total=total,
            active=active,
            telegram=telegram_status,
            database=database_status,
            key=PANEL_KEY,
        )

    finally:

        db.close()


# ============================================================
# FEDERATION DELETE
# ============================================================

@app.get(
    "/federation/delete/<int:chat_id>"
)
@panel_required
def federation_delete(chat_id):

    db = db_session()

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
            "/?key="
            + PANEL_KEY
        )

    except Exception as exc:

        db.rollback()

        logger.exception(
            "Federasyon silme hatası: %s",
            exc,
        )

        return (
            "Federasyon silinemedi.",
            500,
        )

    finally:

        db.close()


# ============================================================
# API
# ============================================================

@app.get("/api/federations")
@panel_required
def api_federations():

    db = db_session()

    try:

        rows = (
            db.query(Federation)
            .order_by(
                Federation.created_at.desc()
            )
            .all()
        )

        return jsonify([
            {
                "id": row.id,
                "chat_id": row.chat_id,
                "chat_title": row.chat_title,
                "chat_username":
                    row.chat_username,
                "portal_username":
                    row.portal_username,
                "duration_minutes":
                    row.duration_minutes,
                "active":
                    row.active,
                "created_at":
                    (
                        row.created_at.isoformat()
                        if row.created_at
                        else None
                    ),
            }
            for row in rows
        ])

    finally:

        db.close()


# ============================================================
# WEBHOOK STATUS
# ============================================================

@app.get("/telegram/status")
@panel_required
def telegram_status():

    return jsonify({
        "configured": bool(TOKEN),
        "ready": telegram_ready.is_set(),
        "public_url": PUBLIC_URL,
        "webhook_url": (
            PUBLIC_URL
            + "/telegram/webhook"
            if PUBLIC_URL
            else None
        ),
        "error": telegram_start_error,
    })


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "ok": False,
        "error": "Not found",
    }), 404


@app.errorhandler(500)
def server_error(error):

    logger.exception(
        "Flask 500 hatası"
    )

    return jsonify({
        "ok": False,
        "error": "Internal server error",
    }), 500


# ============================================================
# STARTUP
# ============================================================

def startup():

    if not TOKEN:

        logger.error(
            "================================================"
        )

        logger.error(
            "TELEGRAM_BOT_TOKEN AYARLANMAMIŞ!"
        )

        logger.error(
            "Telegram botu başlatılmayacak."
        )

        logger.error(
            "================================================"
        )

        return

    start_telegram()


startup()


# ============================================================
# LOCAL
# ============================================================

if __name__ == "__main__":

    logger.info(
        "RoseFed başlıyor..."
    )

    logger.info(
        "PORT = %s",
        PORT,
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )

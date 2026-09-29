import os
import logging
import threading
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Flask, request, redirect, url_for, render_template_string, abort
from sqlalchemy import create_engine, String, Integer, Boolean, DateTime, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# 🌹 ROSEFED BOT
# Telegram Bot + Federasyon Sistemi + Render Panel
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("RoseFedBot")


# ============================================================
# AYARLAR
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN çevresel değişkeni bulunamadı."
    )

PORT = int(os.getenv("PORT", "10000"))

PANEL_KEY = os.getenv(
    "PANEL_KEY",
    "change-me-now"
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///rosefed.db"
)


# Render/PostgreSQL bağlantısını düzelt
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+psycopg://",
        1
    )

elif (
    DATABASE_URL.startswith("postgresql://")
    and "+psycopg" not in DATABASE_URL
):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1
    )


connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False
    }


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args
)

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False
)


# ============================================================
# DATABASE
# ============================================================

class Base(DeclarativeBase):
    pass


class Federation(Base):

    __tablename__ = "federations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    chat_id: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        index=True
    )

    chat_title: Mapped[str] = mapped_column(
        String(255),
        default=""
    )

    chat_username: Mapped[str] = mapped_column(
        String(255),
        default=""
    )

    portal_username: Mapped[str] = mapped_column(
        String(255),
        default=""
    )

    duration_minutes: Mapped[int] = mapped_column(
        Integer,
        default=15
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True
    )

    portal_message_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(engine)


# ============================================================
# FLASK WEB PANEL
# ============================================================

app = Flask(__name__)


# ============================================================
# DATABASE FONKSİYONLARI
# ============================================================

def get_federation(chat_id: int):

    with SessionLocal() as db:

        return db.scalar(
            select(Federation).where(
                Federation.chat_id == chat_id
            )
        )


def upsert_federation(
    chat_id: int,
    title: str,
    username: str,
    portal: str
):

    with SessionLocal() as db:

        row = db.scalar(
            select(Federation).where(
                Federation.chat_id == chat_id
            )
        )

        if row is None:

            row = Federation(
                chat_id=chat_id
            )

            db.add(row)

        row.chat_title = title or ""

        row.chat_username = username or ""

        row.portal_username = (
            portal
            .lstrip("@")
            .strip()
        )

        row.active = True

        db.commit()

        return row


def set_duration_db(
    chat_id: int,
    minutes: int
):

    with SessionLocal() as db:

        row = db.scalar(
            select(Federation).where(
                Federation.chat_id == chat_id
            )
        )

        if row is None:

            row = Federation(
                chat_id=chat_id
            )

            db.add(row)

        row.duration_minutes = minutes

        db.commit()


def delete_federation(
    chat_id: int
):

    with SessionLocal() as db:

        row = db.scalar(
            select(Federation).where(
                Federation.chat_id == chat_id
            )
        )

        if row:

            db.delete(row)

            db.commit()


def list_federations():

    with SessionLocal() as db:

        return list(
            db.scalars(
                select(Federation)
                .order_by(Federation.id)
            ).all()
        )


# ============================================================
# TELEGRAM YETKİ KONTROLÜ
# ============================================================

async def is_user_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat = update.effective_chat

    user = update.effective_user

    if not chat or not user:
        return False

    if chat.type == "private":
        return True

    try:

        member = await chat.get_member(
            user.id
        )

        return member.status in (
            "creator",
            "administrator"
        )

    except Exception as exc:

        logger.exception(
            "Admin kontrolü başarısız: %s",
            exc
        )

        return False


async def require_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_user_admin(
        update,
        context
    ):

        if update.message:

            await update.message.reply_text(
                "⚠️ Bu komut yalnızca grup yöneticileri içindir."
            )

        return False

    return True


# ============================================================
# TELEGRAM LİNKLERİ
# ============================================================

def telegram_link(
    username: str,
    chat_id: int | None = None
):

    if username:

        return (
            "https://t.me/"
            + username.lstrip("@")
        )

    if (
        chat_id
        and str(chat_id).startswith("-100")
    ):

        return (
            "https://t.me/c/"
            + str(chat_id)[4:]
        )

    return ""


# ============================================================
# FEDERASYON BAĞLAMA
# ============================================================

async def bind_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    if update.effective_chat.type not in (
        "group",
        "supergroup"
    ):

        await update.message.reply_text(
            "⚠️ /bagla komutunu bir grup içinde kullanın."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "📌 Kullanım:\n\n"
            "/bagla @arayisgrubu\n\n"
            "Örnek:\n"
            "/bagla @RoseFedArayis"
        )

        return

    target = (
        context.args[0]
        .lstrip("@")
        .strip()
    )

    if not target:

        await update.message.reply_text(
            "⚠️ Hedef grup kullanıcı adı boş olamaz."
        )

        return

    chat = update.effective_chat

    # Bot bilgisi
    me = await context.bot.get_me()

    # Botun yönetici olup olmadığını kontrol et
    try:

        bot_member = await chat.get_member(
            me.id
        )

        if bot_member.status not in (
            "administrator",
            "creator"
        ):

            await update.message.reply_text(
                "⚠️ Portalı sabitlemek ve "
                "moderasyon işlemleri için "
                "botu gruba yönetici olarak ekleyin."
            )

            return

    except Exception:
        pass

    row = upsert_federation(
        chat.id,
        chat.title or str(chat.id),
        chat.username or "",
        target
    )

    current_link = telegram_link(
        chat.username or "",
        chat.id
    )

    target_link = (
        "https://t.me/"
        + target
    )

    buttons = []

    if current_link:

        buttons.append(
            InlineKeyboardButton(
                "💬 Sohbet Grubuna Git",
                url=current_link
            )
        )

    buttons.append(
        InlineKeyboardButton(
            "🔎 Arayış / Portal",
            url=target_link
        )
    )

    markup = InlineKeyboardMarkup(
        [buttons]
    )

    text = (
        "🌹 *ROSEFED FEDERASYON PORTALI*\n\n"
        f"🏛️ *Sohbet:* "
        f"{chat.title or chat.id}\n\n"
        f"🔎 *Portal:* @{target}\n\n"
        f"⏱️ *Akış süresi:* "
        f"{row.duration_minutes} dakika\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "Üyeler aşağıdaki butonlardan "
        "federasyon ağları arasında geçiş yapabilir."
    )

    sent = await update.message.reply_text(
        text,
        reply_markup=markup,
        parse_mode="Markdown"
    )

    # Mesajı sabitle
    try:

        await sent.pin(
            disable_notification=True
        )

        with SessionLocal() as db:

            saved = db.scalar(
                select(Federation).where(
                    Federation.chat_id == chat.id
                )
            )

            if saved:

                saved.portal_message_id = (
                    sent.message_id
                )

                db.commit()

    except Exception as exc:

        logger.warning(
            "Portal sabitlenemedi: %s",
            exc
        )

    await update.message.reply_text(
        "✅ Federasyon bağlantısı oluşturuldu.\n\n"
        "🖥 /panel — yönetim paneli\n"
        "❌ /ayir — federasyon bağlantısını kaldır"
    )


# ============================================================
# FEDERASYON AYIR
# ============================================================

async def unbind_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    delete_federation(
        update.effective_chat.id
    )

    await update.message.reply_text(
        "❌ Bu grubun federasyon bağlantısı kaldırıldı."
    )


# ============================================================
# SÜRE
# ============================================================

async def set_duration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    if (
        not context.args
        or not context.args[0].isdigit()
    ):

        await update.message.reply_text(
            "⏱️ Kullanım:\n"
            "/sure 15"
        )

        return

    minutes = max(
        1,
        min(
            1440,
            int(context.args[0])
        )
    )

    set_duration_db(
        update.effective_chat.id,
        minutes
    )

    await update.message.reply_text(
        f"✅ Federasyon süresi "
        f"{minutes} dakika olarak ayarlandı."
    )


# ============================================================
# HEDEF KULLANICI
# ============================================================

async def resolve_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # Yanıtlanan mesaj
    if (
        update.message
        and update.message.reply_to_message
    ):

        return (
            update.message
            .reply_to_message
            .from_user
            .id
        )

    # Telegram ID
    if context.args:

        value = (
            context.args[0]
            .lstrip("@")
        )

        if (
            value.isdigit()
            or (
                value.startswith("-")
                and value[1:].isdigit()
            )
        ):

            return int(value)

    return None


# ============================================================
# BAN
# ============================================================

async def ban_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    target = await resolve_target(
        update,
        context
    )

    if target is None:

        await update.message.reply_text(
            "⚠️ Kullanıcıya yanıt vererek "
            "veya Telegram ID ile kullanın."
        )

        return

    try:

        await context.bot.ban_chat_member(
            update.effective_chat.id,
            target
        )

        await update.message.reply_text(
            "🔨 Kullanıcı banlandı."
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Ban başarısız:\n{exc}"
        )


# ============================================================
# UNBAN
# ============================================================

async def unban_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    target = await resolve_target(
        update,
        context
    )

    if target is None:

        await update.message.reply_text(
            "⚠️ Kullanıcıya yanıt vererek "
            "veya Telegram ID ile kullanın."
        )

        return

    try:

        await context.bot.unban_chat_member(
            update.effective_chat.id,
            target,
            only_if_banned=True
        )

        await update.message.reply_text(
            "🔓 Ban kaldırıldı."
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Ban kaldırma başarısız:\n{exc}"
        )


# ============================================================
# MUTE
# ============================================================

async def mute_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    target = await resolve_target(
        update,
        context
    )

    if target is None:

        await update.message.reply_text(
            "⚠️ Kullanıcıya yanıt vererek "
            "veya Telegram ID ile kullanın."
        )

        return

    try:

        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target,
            permissions=ChatPermissions(
                can_send_messages=False
            )
        )

        await update.message.reply_text(
            "🔇 Kullanıcı susturuldu."
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Susturma başarısız:\n{exc}"
        )


# ============================================================
# UNMUTE
# ============================================================

async def unmute_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    target = await resolve_target(
        update,
        context
    )

    if target is None:

        await update.message.reply_text(
            "⚠️ Kullanıcıya yanıt vererek "
            "veya Telegram ID ile kullanın."
        )

        return

    try:

        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )

        await update.message.reply_text(
            "🔊 Susturma kaldırıldı."
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Susturma kaldırma başarısız:\n{exc}"
        )


# ============================================================
# ÖZEL KOMUTLAR
# ============================================================

async def custom_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):
        return

    command = (
        update.message
        .text
        .split()[0]
        .split("@")[0]
        .lstrip("/")
    )

    target = "Bilinmeyen hedef"

    if update.message.reply_to_message:

        target = (
            update.message
            .reply_to_message
            .from_user
            .first_name
        )

    elif context.args:

        target = " ".join(
            context.args
        )

    await update.message.reply_text(
        f"👑 {command.capitalize()} uygulandı.\n\n"
        f"🎯 Hedef: {target}"
    )


# ============================================================
# YARDIM
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🌹 ROSEFED BOT\n\n"

        "🌐 FEDERASYON\n"
        "/bagla @grup\n"
        "→ Portal oluşturur\n\n"

        "/ayir\n"
        "→ Federasyonu kaldırır\n\n"

        "/sure 15\n"
        "→ Süreyi ayarlar\n\n"

        "🛡 MODERASYON\n"
        "/ban\n"
        "/unban\n"
        "/mute\n"
        "/unmute\n\n"

        "👑 ÖZEL\n"
        "/itaat\n"
        "/kralice\n"
        "/krallice\n\n"

        "🖥 PANEL\n"
        "/panel"
    )


# ============================================================
# PANEL KOMUTU
# ============================================================

async def panel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    panel_url = (
        os.getenv(
            "PUBLIC_URL",
            ""
        )
        .rstrip("/")
    )

    if not panel_url:

        await update.message.reply_text(
            "⚠️ PUBLIC_URL Render Environment "
            "Variables bölümünde ayarlanmamış."
        )

        return

    await update.message.reply_text(
        "🖥 ROSEFED CONTROL CENTER\n\n"
        f"{panel_url}/?key="
        f"{quote(PANEL_KEY)}"
    )


# ============================================================
# WEB PANEL TASARIMI
# ============================================================

HTML = """

<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>RoseFed Control Center</title>

<style>

*{
box-sizing:border-box;
}

body{

margin:0;

background:
radial-gradient(
circle at top right,
#3b1946,
#0d0a10 45%
);

color:#eee;

font-family:
Inter,
Arial,
sans-serif;

min-height:100vh;

}

.container{

max-width:1150px;

margin:auto;

padding:30px;

}

.header{

background:
linear-gradient(
135deg,
#28142f,
#151017
);

border:
1px solid #5b3768;

border-radius:25px;

padding:30px;

box-shadow:
0 20px 60px
rgba(0,0,0,.35);

}

.logo{

font-size:34px;

font-weight:800;

}

.subtitle{

color:#aaa;

margin-top:8px;

}

.grid{

display:grid;

grid-template-columns:
repeat(
auto-fit,
minmax(
280px,
1fr
)
);

gap:18px;

margin-top:20px;

}

.card{

background:#171219;

border:
1px solid #34263a;

border-radius:20px;

padding:22px;

}

.card h2{

margin-top:0;

}

input{

width:100%;

padding:13px;

margin:
7px 0 13px;

border-radius:10px;

border:
1px solid #4a3852;

background:#0d0a10;

color:white;

outline:none;

}

input:focus{

border-color:#d27cff;

}

button{

background:
linear-gradient(
135deg,
#dc82ff,
#a84dd0
);

border:none;

border-radius:10px;

padding:12px 20px;

font-weight:bold;

cursor:pointer;

}

table{

width:100%;

border-collapse:collapse;

margin-top:15px;

}

th,td{

padding:13px;

border-bottom:
1px solid #302632;

text-align:left;

}

.badge{

display:inline-block;

padding:
5px 10px;

border-radius:20px;

font-size:12px;

background:#193923;

color:#9df5b0;

}

.muted{

color:#999;

font-size:13px;

}

a{

color:#df9cff;

}

</style>

</head>

<body>

<div class="container">

<div class="header">

<div class="logo">
🌹 RoseFed Control Center
</div>

<div class="subtitle">

Telegram Federasyon Yönetim Merkezi

</div>

</div>


<div class="grid">


<div class="card">

<h2>➕ Federasyon Ekle</h2>

<form
method="post"
action="/federation"
>

<input
type="hidden"
name="key"
value="{{key}}"
>

<label>
Grup ID
</label>

<input
name="chat_id"
placeholder="-1001234567890"
required
>

<label>
Grup adı
</label>

<input
name="title"
placeholder="RoseFed Sohbet"
>

<label>
Grup kullanıcı adı
</label>

<input
name="username"
placeholder="rosefedchat"
>

<label>
Portal kullanıcı adı
</label>

<input
name="portal"
placeholder="rosefedarayis"
required
>

<label>
Süre
</label>

<input
name="duration"
type="number"
min="1"
max="1440"
value="15"
>

<button>
Federasyonu Kaydet
</button>

</form>

</div>


<div class="card">

<h2>📊 Sistem</h2>

<p>
Bot:
<span class="badge">
ÇALIŞIYOR
</span>
</p>

<p>
Federasyon:
<b>
{{rows|length}}
</b>
</p>

<p>
Database:
<b>
{{db_type}}
</b>
</p>

<p>
Render Port:
<b>
{{port}}
</b>
</p>

</div>

</div>


<div
class="card"
style="margin-top:20px"
>

<h2>
🌐 Federasyonlar
</h2>

<table>

<tr>

<th>
Grup
</th>

<th>
Portal
</th>

<th>
Süre
</th>

<th>
Durum
</th>

<th>
İşlem
</th>

</tr>


{% for r in rows %}

<tr>

<td>

{{r.chat_title or r.chat_id}}

<br>

<span class="muted">

{{r.chat_id}}

</span>

</td>

<td>

@{{r.portal_username}}

</td>

<td>

{{r.duration_minutes}}
dk

</td>

<td>

<span class="badge">
AKTİF
</span>

</td>

<td>

<a
href="/federation/delete/{{r.chat_id}}?key={{key}}"
onclick="
return confirm(
'Federasyon bağlantısı silinsin mi?'
)
"
>

Sil

</a>

</td>

</tr>

{% else %}

<tr>

<td colspan="5">

Henüz federasyon eklenmedi.

</td>

</tr>

{% endfor %}

</table>

</div>


</div>

</body>

</html>

"""


# ============================================================
# PANEL GÜVENLİĞİ
# ============================================================

def panel_guard():

    key = (
        request.args.get("key")
        or request.form.get("key")
    )

    return key == PANEL_KEY


# ============================================================
# ANA PANEL
# ============================================================

@app.get("/")
def dashboard():

    if not panel_guard():

        abort(403)

    return render_template_string(

        HTML,

        rows=list_federations(),

        key=PANEL_KEY,

        db_type=(
            "PostgreSQL"
            if "postgres" in DATABASE_URL
            else "SQLite"
        ),

        port=PORT
    )


# ============================================================
# FEDERASYON KAYDET
# ============================================================

@app.post("/federation")
def federation_save():

    if not panel_guard():

        abort(403)

    try:

        chat_id = int(
            request.form["chat_id"]
        )

        duration = max(
            1,
            min(
                1440,
                int(
                    request.form.get(
                        "duration",
                        15
                    )
                )
            )
        )

    except ValueError:

        abort(
            400,
            "Geçersiz chat ID veya süre"
        )

    upsert_federation(

        chat_id,

        request.form.get(
            "title",
            ""
        ),

        request.form.get(
            "username",
            ""
        ),

        request.form["portal"]
    )

    set_duration_db(
        chat_id,
        duration
    )

    return redirect(
        url_for(
            "dashboard",
            key=PANEL_KEY
        )
    )


# ============================================================
# FEDERASYON SİL
# ============================================================

@app.get(
    "/federation/delete/<int:chat_id>"
)
def federation_delete(
    chat_id: int
):

    if not panel_guard():

        abort(403)

    delete_federation(
        chat_id
    )

    return redirect(
        url_for(
            "dashboard",
            key=PANEL_KEY
        )
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    return {

        "status": "ok",

        "service":
        "rosefed-bot"

    }, 200


# ============================================================
# API
# ============================================================

@app.get("/api/federations")
def api_federations():

    if not panel_guard():

        abort(403)

    rows = list_federations()

    return {

        "count":
        len(rows),

        "items": [

            {

                "chat_id":
                r.chat_id,

                "title":
                r.chat_title,

                "username":
                r.chat_username,

                "portal":
                r.portal_username,

                "duration_minutes":
                r.duration_minutes,

                "active":
                r.active

            }

            for r in rows

        ]

    }


# ============================================================
# FLASK SUNUCUSU
# ============================================================

def run_web():

    logger.info(
        "RoseFed web panel başlatılıyor: "
        "0.0.0.0:%s",
        PORT
    )

    app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False,

        use_reloader=False

    )


# ============================================================
# TELEGRAM BOT
# ============================================================

def build_bot():

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )


    # Yardım

    application.add_handler(
        CommandHandler(
            ["yardim", "help"],
            help_command
        )
    )


    # Panel

    application.add_handler(
        CommandHandler(
            "panel",
            panel_command
        )
    )


    # Federasyon

    application.add_handler(
        CommandHandler(
            "bagla",
            bind_group
        )
    )

    application.add_handler(
        CommandHandler(
            "ayir",
            unbind_group
        )
    )

    application.add_handler(
        CommandHandler(
            "sure",
            set_duration
        )
    )


    # Moderasyon

    application.add_handler(
        CommandHandler(
            "ban",
            ban_user
        )
    )

    application.add_handler(
        CommandHandler(
            "unban",
            unban_user
        )
    )

    application.add_handler(
        CommandHandler(
            "mute",
            mute_user
        )
    )

    application.add_handler(
        CommandHandler(
            "unmute",
            unmute_user
        )
    )


    # Özel komutlar

    application.add_handler(

        CommandHandler(

            [
                "itaat",
                "kralice",
                "krallice"
            ],

            custom_command

        )

    )


    return application


# ============================================================
# MAIN
# ============================================================

def main():

    # Render web sunucusunu arka planda çalıştır

    web_thread = threading.Thread(

        target=run_web,

        daemon=True

    )

    web_thread.start()


    # Telegram bot

    bot = build_bot()


    logger.info(
        "🌹 RoseFed Telegram Bot başlatılıyor..."
    )


    bot.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ÇALIŞTIR
# ============================================================

if __name__ == "__main__":

    main()

"""/start, /help, inline menüler, sabit klavye (iki grup arası hızlı geçiş) ve duyuru akışı."""
import html
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import database as db
from utils import is_admin

log = logging.getLogger(__name__)
HTML = ParseMode.HTML

BTN_A = f"🅰️ {config.GROUP_A_NAME}"
BTN_B = f"🅱️ {config.GROUP_B_NAME}"
BTN_ANN = "📢 Duyuru"
BTN_FED = "📊 Fed Durumu"
ALL_BUTTONS = [BTN_A, BTN_B, BTN_ANN, BTN_FED]

GROUPS = {BTN_A: config.GROUP_A_ID, BTN_B: config.GROUP_B_ID}
GROUP_NAMES = {config.GROUP_A_ID: config.GROUP_A_NAME, config.GROUP_B_ID: config.GROUP_B_NAME}


def main_keyboard() -> ReplyKeyboardMarkup:
    """Sabit (kalıcı) klavye: iki grup arasında hızlı geçiş + kısayollar."""
    return ReplyKeyboardMarkup(
        [[BTN_A, BTN_B], [BTN_ANN, BTN_FED]],
        resize_keyboard=True,
        is_persistent=True,
    )


def help_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🛡 Yönetici", callback_data="help:admin"),
                InlineKeyboardButton("🌐 Fed", callback_data="help:fed"),
            ],
            [InlineKeyboardButton("❌ Kapat", callback_data="help:close")],
        ]
    )


HELP_TEXT = {
    "main": "<b>Yardım Menüsü</b>\nBir kategori seç:",
    "admin": (
        "<b>🛡 Yönetici Komutları</b> (grupta)\n\n"
        "/ban [sebep] — banla (yanıtla ya da ID)\n"
        "/unban — banı kaldır\n"
        "/kick — gruptan at\n"
        "/mute [10m|2h|1d|1w] [sebep] — sustur\n"
        "/unmute — susturmayı kaldır\n\n"
        "Bu komutlar için 'üyeleri kısıtlama' yetkisi gerekir."
    ),
    "fed": (
        "<b>🌐 Fed Komutları</b>\n\n"
        "/newfed &lt;isim&gt; — fed oluştur (özelde)\n"
        "/delfed — fed'i sil\n"
        "/joinfed &lt;id&gt; — grubu fed'e kat (grup sahibi)\n"
        "/leavefed — grubu fed'den çıkar\n"
        "/fedinfo — fed bilgisi\n"
        "/fban &lt;id&gt; [sebep] — tüm fed gruplarında banla\n"
        "/unfban &lt;id&gt; — fed banını kaldır\n"
        "/fbanlist — son fed banları\n"
        "/fpromote, /fdemote — fed yöneticisi ata/al\n"
        "/fedannounce &lt;metin&gt; — tüm fed gruplarına duyuru"
    ),
}


async def _allowed(user_id: int) -> bool:
    return user_id == config.OWNER_ID or bool(await db.get_managed_fed(user_id))


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.effective_message.reply_text(
            "👋 Merhaba! Ben FED moderasyon botuyum.\n"
            "Alttaki butonlarla gruplar arasında geçiş yapabilir, duyuru gönderebilirsin.",
            reply_markup=main_keyboard(),
        )
        await update.effective_message.reply_text(
            HELP_TEXT["main"], parse_mode=HTML, reply_markup=help_menu()
        )
    else:
        await update.effective_message.reply_text("Hazırım! Komutlar için /help")


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        HELP_TEXT["main"], parse_mode=HTML, reply_markup=help_menu()
    )


async def help_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    key = q.data.split(":")[1]
    if key == "close":
        await q.message.delete()
        return await q.answer()
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Geri", callback_data="help:main")]])
    await q.edit_message_text(
        HELP_TEXT[key], parse_mode=HTML, reply_markup=help_menu() if key == "main" else back
    )
    await q.answer()


# ---------- Sabit klavye: grup seçimi ----------
async def select_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    if not await _allowed(user.id):
        return await msg.reply_text("⛔ Bu butonlar yalnızca fed yöneticileri içindir.")
    gid = GROUPS.get(msg.text)
    if not gid:
        return await msg.reply_text("Bu grup ortam değişkenlerinde tanımlı değil (GROUP_A_ID / GROUP_B_ID).")
    ctx.user_data["target"] = gid
    await msg.reply_text(f"📍 Aktif grup: <b>{html.escape(GROUP_NAMES[gid])}</b>", parse_mode=HTML)


# ---------- Duyuru akışı ----------
async def announce_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await _allowed(update.effective_user.id):
        return await msg.reply_text("⛔ Yetkin yok.")
    if not ctx.user_data.get("target"):
        return await msg.reply_text("Önce alttaki butonlardan bir grup seç.")
    ctx.user_data["await_ann"] = True
    name = GROUP_NAMES.get(ctx.user_data["target"], "seçili grup")
    await msg.reply_text(f"✍️ <b>{html.escape(name)}</b> için duyuru metnini yaz:", parse_mode=HTML)


async def announce_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.pop("await_ann", False):
        return
    ctx.user_data["ann_text"] = update.effective_message.text
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Gönder", callback_data="ann:ok"),
            InlineKeyboardButton("❌ İptal", callback_data="ann:no"),
        ]]
    )
    await update.effective_message.reply_text(
        f"<b>Önizleme:</b>\n\n{html.escape(update.effective_message.text)}",
        parse_mode=HTML,
        reply_markup=kb,
    )


async def announce_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    text = ctx.user_data.pop("ann_text", None)
    target = ctx.user_data.get("target")
    if q.data == "ann:no" or not text or not target:
        await q.edit_message_text("İptal edildi.")
        return await q.answer()
    if not await is_admin(await ctx.bot.get_chat(target), q.from_user.id, ctx):
        await q.edit_message_text("⛔ Bu grupta yönetici değilsin.")
        return await q.answer()
    try:
        await ctx.bot.send_message(
            target, f"📢 <b>DUYURU</b>\n\n{html.escape(text)}", parse_mode=HTML
        )
        await q.edit_message_text("✅ Duyuru gönderildi.")
    except TelegramError as e:
        await q.edit_message_text(f"Hata: {e.message}")
    await q.answer()


# ---------- Fed durumu ----------
async def fed_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    fed = await db.get_managed_fed(update.effective_user.id)
    if not fed:
        return await msg.reply_text("Yönettiğin bir fed yok. /newfed <isim>")
    chats = await db.fed_chats(fed["fed_id"])
    bans = await db.count_fbans(fed["fed_id"])
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 Bağlı Gruplar", callback_data=f"fedst:chats:{fed['fed_id']}")]]
    )
    await msg.reply_text(
        f"<b>{html.escape(fed['name'])}</b>\nID: <code>{fed['fed_id']}</code>\n"
        f"Grup: {len(chats)} | Fed ban: {bans}",
        parse_mode=HTML,
        reply_markup=kb,
    )


async def fed_status_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    fed_id = q.data.split(":")[2]
    fed = await db.get_fed(fed_id)
    if not fed or q.from_user.id not in {fed["owner_id"], config.OWNER_ID, *fed.get("admins", [])}:
        return await q.answer("⛔ Yetkin yok.", show_alert=True)
    rows = await db.fed_chats(fed_id)
    text = "\n".join(f"• {html.escape(r.get('title', '?'))}" for r in rows) or "Bağlı grup yok."
    await q.message.reply_text(f"<b>Bağlı gruplar:</b>\n{text}", parse_mode=HTML)
    await q.answer()


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(help_cb, pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(announce_cb, pattern=r"^ann:"))
    app.add_handler(CallbackQueryHandler(fed_status_cb, pattern=r"^fedst:chats:"))

    app.add_handler(MessageHandler(private & filters.Text([BTN_A, BTN_B]), select_group))
    app.add_handler(MessageHandler(private & filters.Text([BTN_ANN]), announce_button))
    app.add_handler(MessageHandler(private & filters.Text([BTN_FED]), fed_status))
    app.add_handler(
        MessageHandler(private & filters.TEXT & ~filters.COMMAND & ~filters.Text(ALL_BUTTONS), announce_text)
    )

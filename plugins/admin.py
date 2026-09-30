"""Grup içi yönetici komutları: ban, unban, kick, mute, unmute."""
import html
import logging

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from utils import can_restrict, extract_target, is_admin, mention, parse_duration

log = logging.getLogger(__name__)


async def _guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE, need_target=True):
    """Ortak kontroller. Başarılıysa (target_id, target_name, args) döner."""
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message

    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("Bu komut yalnızca gruplarda kullanılabilir.")
        return None
    if not await can_restrict(chat, user.id, ctx):
        await msg.reply_text("⛔ Bu komut için 'üyeleri kısıtlama' yetkin olmalı.")
        return None

    target, name, args = await extract_target(update, ctx)
    if need_target and not target:
        await msg.reply_text("Bir mesajı yanıtla ya da kullanıcı ID'si yaz.")
        return None
    if target and (target == ctx.bot.id or await is_admin(chat, target, ctx)):
        await msg.reply_text("Yöneticilere ya da bota bu işlem uygulanamaz.")
        return None
    return target, name, args


def _undo_kb(action: str, uid: int, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"{action}:{uid}")]])


async def ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    r = await _guard(update, ctx)
    if not r:
        return
    target, name, args = r
    reason = " ".join(args) or "Belirtilmedi"
    try:
        await ctx.bot.ban_chat_member(update.effective_chat.id, target)
    except TelegramError as e:
        return await update.effective_message.reply_text(f"Hata: {e.message}")
    await update.effective_message.reply_text(
        f"🔨 {mention(target, name)} banlandı.\nSebep: {html.escape(reason)}",
        parse_mode=ParseMode.HTML,
        reply_markup=_undo_kb("unban", target, "🔓 Banı Kaldır"),
    )


async def unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if chat.type not in ("group", "supergroup") or not await can_restrict(chat, user.id, ctx):
        return await msg.reply_text("⛔ Yetkin yok ya da bu bir grup değil.")
    target, name, _ = await extract_target(update, ctx)
    if not target:
        return await msg.reply_text("Bir mesajı yanıtla ya da kullanıcı ID'si yaz.")
    try:
        await ctx.bot.unban_chat_member(chat.id, target, only_if_banned=True)
    except TelegramError as e:
        return await msg.reply_text(f"Hata: {e.message}")
    await msg.reply_text(f"✅ {mention(target, name)} banı kaldırıldı.", parse_mode=ParseMode.HTML)


async def kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    r = await _guard(update, ctx)
    if not r:
        return
    target, name, _ = r
    chat_id = update.effective_chat.id
    try:
        await ctx.bot.ban_chat_member(chat_id, target)
        await ctx.bot.unban_chat_member(chat_id, target)
    except TelegramError as e:
        return await update.effective_message.reply_text(f"Hata: {e.message}")
    await update.effective_message.reply_text(
        f"👢 {mention(target, name)} gruptan atıldı.", parse_mode=ParseMode.HTML
    )


async def mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/mute [süre: 10m, 2h, 1d, 1w] [sebep]  — süre yoksa süresiz."""
    r = await _guard(update, ctx)
    if not r:
        return
    target, name, args = r
    until = None
    if args:
        until = parse_duration(args[0])
        if until:
            args = args[1:]
    reason = " ".join(args) or "Belirtilmedi"
    try:
        await ctx.bot.restrict_chat_member(
            update.effective_chat.id,
            target,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except TelegramError as e:
        return await update.effective_message.reply_text(f"Hata: {e.message}")
    sure = until.strftime("%d.%m.%Y %H:%M UTC") if until else "süresiz"
    await update.effective_message.reply_text(
        f"🔇 {mention(target, name)} susturuldu ({sure}).\nSebep: {html.escape(reason)}",
        parse_mode=ParseMode.HTML,
        reply_markup=_undo_kb("unmute", target, "🔊 Susturmayı Kaldır"),
    )


async def _unmute(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, uid: int):
    full = await ctx.bot.get_chat(chat_id)
    perms = full.permissions or ChatPermissions(can_send_messages=True)
    await ctx.bot.restrict_chat_member(chat_id, uid, perms)


async def unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if chat.type not in ("group", "supergroup") or not await can_restrict(chat, user.id, ctx):
        return await msg.reply_text("⛔ Yetkin yok ya da bu bir grup değil.")
    target, name, _ = await extract_target(update, ctx)
    if not target:
        return await msg.reply_text("Bir mesajı yanıtla ya da kullanıcı ID'si yaz.")
    try:
        await _unmute(ctx, chat.id, target)
    except TelegramError as e:
        return await msg.reply_text(f"Hata: {e.message}")
    await msg.reply_text(f"🔊 {mention(target, name)} artık konuşabilir.", parse_mode=ParseMode.HTML)


async def undo_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inline 'geri al' butonları. Yalnızca yetkili yöneticiler kullanabilir."""
    q = update.callback_query
    chat = q.message.chat
    if not await can_restrict(chat, q.from_user.id, ctx):
        return await q.answer("⛔ Bu butonu kullanma yetkin yok.", show_alert=True)

    action, uid = q.data.split(":")
    uid = int(uid)
    try:
        if action == "unban":
            await ctx.bot.unban_chat_member(chat.id, uid, only_if_banned=True)
            text = "✅ Ban kaldırıldı."
        else:
            await _unmute(ctx, chat.id, uid)
            text = "🔊 Susturma kaldırıldı."
    except TelegramError as e:
        return await q.answer(f"Hata: {e.message}", show_alert=True)

    await q.edit_message_text(f"{text} (işlemi yapan: {q.from_user.full_name})")
    await q.answer()


def register(app: Application) -> None:
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CallbackQueryHandler(undo_callback, pattern=r"^(unban|unmute):\d+$"))

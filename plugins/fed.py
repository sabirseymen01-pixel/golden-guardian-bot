"""FED sistemi: gruplar arası ortak ban listesi ve duyuru."""
import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from utils import extract_target, is_chat_creator, mention

log = logging.getLogger(__name__)
HTML = ParseMode.HTML


def _managers(fed: dict) -> set[int]:
    return {fed["owner_id"], config.OWNER_ID, *fed.get("admins", [])}


async def _my_fed(update: Update):
    """Komutu kullanan kişinin yönettiği fed'i döner; yoksa uyarı verir."""
    fed = await db.get_managed_fed(update.effective_user.id)
    if not fed:
        await update.effective_message.reply_text(
            "Yönettiğin bir fed yok. /newfed <isim> ile oluşturabilirsin."
        )
    return fed


# ---------- Fed oluşturma / silme ----------
async def newfed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_chat.type != "private":
        return await msg.reply_text("Fed'i bana özelden oluştur.")
    name = " ".join(ctx.args).strip()
    if not name:
        return await msg.reply_text("Kullanım: /newfed <fed adı>")
    if await db.get_owned_fed(update.effective_user.id):
        return await msg.reply_text("Zaten bir fed'in var. Önce /delfed ile silmelisin.")
    fed = await db.create_fed(update.effective_user.id, name)
    await msg.reply_text(
        f"✅ Fed oluşturuldu!\n\n<b>{html.escape(name)}</b>\nID: <code>{fed['fed_id']}</code>\n\n"
        f"Gruba katmak için grupta: <code>/joinfed {fed['fed_id']}</code>",
        parse_mode=HTML,
    )


async def delfed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fed = await db.get_owned_fed(update.effective_user.id)
    if not fed:
        return await update.effective_message.reply_text("Sahibi olduğun bir fed yok.")
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🗑 Evet, sil", callback_data=f"delfed:yes:{fed['fed_id']}"),
            InlineKeyboardButton("❌ Vazgeç", callback_data="delfed:no:0"),
        ]]
    )
    await update.effective_message.reply_text(
        f"<b>{html.escape(fed['name'])}</b> fed'i ve tüm ban kayıtları silinecek. Emin misin?",
        parse_mode=HTML,
        reply_markup=kb,
    )


async def delfed_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, choice, fed_id = q.data.split(":")
    if choice == "no":
        await q.edit_message_text("İşlem iptal edildi.")
        return await q.answer()
    fed = await db.get_fed(fed_id)
    if not fed or q.from_user.id not in (fed["owner_id"], config.OWNER_ID):
        return await q.answer("⛔ Yetkin yok.", show_alert=True)
    await db.delete_fed(fed_id)
    await q.edit_message_text("🗑 Fed silindi.")
    await q.answer()


# ---------- Grup katılım ----------
async def joinfed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if chat.type not in ("group", "supergroup"):
        return await msg.reply_text("Bu komut bir grupta kullanılmalı.")
    if not await is_chat_creator(chat, user.id, ctx):
        return await msg.reply_text("⛔ Yalnızca grup sahibi fed'e katabilir.")
    if not ctx.args:
        return await msg.reply_text("Kullanım: /joinfed <fed_id>")
    fed = await db.get_fed(ctx.args[0])
    if not fed:
        return await msg.reply_text("Bu ID ile bir fed bulunamadı.")
    if await db.get_fed_for_chat(chat.id):
        return await msg.reply_text("Bu grup zaten bir fed'e bağlı. Önce /leavefed.")
    await db.join_fed(chat.id, chat.title or str(chat.id), fed["fed_id"])
    await msg.reply_text(
        f"✅ Bu grup <b>{html.escape(fed['name'])}</b> fed'ine katıldı.", parse_mode=HTML
    )


async def leavefed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if chat.type not in ("group", "supergroup"):
        return await msg.reply_text("Bu komut bir grupta kullanılmalı.")
    if not await is_chat_creator(chat, user.id, ctx):
        return await msg.reply_text("⛔ Yalnızca grup sahibi fed'den ayırabilir.")
    if not await db.get_fed_for_chat(chat.id):
        return await msg.reply_text("Bu grup hiçbir fed'e bağlı değil.")
    await db.leave_fed(chat.id)
    await msg.reply_text("👋 Grup fed'den ayrıldı.")


# ---------- Bilgi ----------
async def fedinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_chat.type == "private":
        fed = await db.get_managed_fed(update.effective_user.id)
    else:
        fed = await db.get_fed_for_chat(update.effective_chat.id)
    if not fed:
        return await msg.reply_text("Fed bilgisi bulunamadı.")
    n_chats = len(await db.fed_chats(fed["fed_id"]))
    n_bans = await db.count_fbans(fed["fed_id"])
    await msg.reply_text(
        f"<b>{html.escape(fed['name'])}</b>\n"
        f"ID: <code>{fed['fed_id']}</code>\n"
        f"Sahip: {mention(fed['owner_id'], str(fed['owner_id']))}\n"
        f"Yönetici sayısı: {len(fed.get('admins', []))}\n"
        f"Bağlı grup: {n_chats}\n"
        f"Fed banlı kullanıcı: {n_bans}",
        parse_mode=HTML,
    )


async def fbanlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fed = await _my_fed(update)
    if not fed:
        return
    rows = await db.list_fbans(fed["fed_id"], 20)
    if not rows:
        return await update.effective_message.reply_text("Fed ban listesi boş.")
    lines = [f"• <code>{r['user_id']}</code> — {html.escape(r.get('reason', '-'))}" for r in rows]
    await update.effective_message.reply_text(
        f"<b>Son {len(rows)} fed ban:</b>\n" + "\n".join(lines), parse_mode=HTML
    )


# ---------- Fed yöneticileri ----------
async def fpromote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fed = await db.get_owned_fed(update.effective_user.id)
    if not fed:
        return await update.effective_message.reply_text("Yalnızca fed sahibi yönetici atayabilir.")
    target, name, _ = await extract_target(update, ctx)
    if not target:
        return await update.effective_message.reply_text("Yanıtla ya da ID yaz.")
    await db.add_fed_admin(fed["fed_id"], target)
    await update.effective_message.reply_text(
        f"✅ {mention(target, name)} fed yöneticisi yapıldı.", parse_mode=HTML
    )


async def fdemote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fed = await db.get_owned_fed(update.effective_user.id)
    if not fed:
        return await update.effective_message.reply_text("Yalnızca fed sahibi yetkiyi alabilir.")
    target, name, _ = await extract_target(update, ctx)
    if not target:
        return await update.effective_message.reply_text("Yanıtla ya da ID yaz.")
    await db.remove_fed_admin(fed["fed_id"], target)
    await update.effective_message.reply_text(
        f"✅ {mention(target, name)} fed yöneticiliğinden alındı.", parse_mode=HTML
    )


# ---------- Fed ban / unban ----------
async def fban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    fed = await _my_fed(update)
    if not fed:
        return
    target, name, args = await extract_target(update, ctx)
    if not target:
        return await msg.reply_text("Kullanım: /fban <ID> [sebep] (ya da mesajı yanıtla)")
    if target == ctx.bot.id or target in _managers(fed):
        return await msg.reply_text("Bu kullanıcıya fed ban uygulanamaz.")

    reason = " ".join(args) or "Belirtilmedi"
    await db.fban_user(fed["fed_id"], target, reason, update.effective_user.id)

    status = await msg.reply_text("⏳ Fed ban uygulanıyor...")
    ok = fail = 0
    for row in await db.fed_chats(fed["fed_id"]):
        try:
            await ctx.bot.ban_chat_member(row["chat_id"], target)
            ok += 1
        except TelegramError as e:
            fail += 1
            log.warning("fban %s @ %s: %s", target, row["chat_id"], e.message)
        await asyncio.sleep(0.05)  # flood limitine takılmamak için

    await status.edit_text(
        f"🚫 <b>FED BAN</b> — {html.escape(fed['name'])}\n"
        f"Kullanıcı: {mention(target, name)} (<code>{target}</code>)\n"
        f"Sebep: {html.escape(reason)}\n"
        f"Banlanan grup: {ok} | Başarısız: {fail}",
        parse_mode=HTML,
    )


async def unfban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    fed = await _my_fed(update)
    if not fed:
        return
    target, name, _ = await extract_target(update, ctx)
    if not target:
        return await msg.reply_text("Kullanım: /unfban <ID> (ya da mesajı yanıtla)")
    if not await db.unfban_user(fed["fed_id"], target):
        return await msg.reply_text("Bu kullanıcı fed ban listesinde değil.")
    ok = 0
    for row in await db.fed_chats(fed["fed_id"]):
        try:
            await ctx.bot.unban_chat_member(row["chat_id"], target, only_if_banned=True)
            ok += 1
        except TelegramError:
            pass
        await asyncio.sleep(0.05)
    await msg.reply_text(
        f"✅ {mention(target, name)} fed banı kaldırıldı ({ok} grupta).", parse_mode=HTML
    )


# ---------- Fed duyurusu ----------
async def fedannounce(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    fed = await _my_fed(update)
    if not fed:
        return
    text = " ".join(ctx.args).strip()
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if not text:
        return await msg.reply_text("Kullanım: /fedannounce <metin> (ya da bir mesajı yanıtla)")

    body = f"📢 <b>FED DUYURUSU — {html.escape(fed['name'])}</b>\n\n{html.escape(text)}"
    ok = fail = 0
    for row in await db.fed_chats(fed["fed_id"]):
        try:
            await ctx.bot.send_message(row["chat_id"], body, parse_mode=HTML)
            ok += 1
        except TelegramError:
            fail += 1
        await asyncio.sleep(0.05)
    await msg.reply_text(f"📢 Duyuru gönderildi: {ok} grup | Başarısız: {fail}")


# ---------- Otomatik uygulama: fed banlı biri katılırsa banla ----------
async def on_new_members(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    fed = await db.get_fed_for_chat(chat.id)
    if not fed:
        return
    for member in update.effective_message.new_chat_members or []:
        fb = await db.get_fban(fed["fed_id"], member.id)
        if not fb:
            continue
        try:
            await ctx.bot.ban_chat_member(chat.id, member.id)
            await ctx.bot.send_message(
                chat.id,
                f"🚫 {mention(member.id, member.full_name)} fed ban listesinde olduğu için banlandı.\n"
                f"Sebep: {html.escape(fb.get('reason', '-'))}",
                parse_mode=HTML,
            )
        except TelegramError as e:
            log.warning("Otomatik fban başarısız (%s): %s", chat.id, e.message)


def register(app: Application) -> None:
    for name, fn in [
        ("newfed", newfed), ("delfed", delfed), ("joinfed", joinfed),
        ("leavefed", leavefed), ("fedinfo", fedinfo), ("fbanlist", fbanlist),
        ("fpromote", fpromote), ("fdemote", fdemote), ("fban", fban),
        ("unfban", unfban), ("fedannounce", fedannounce),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(delfed_cb, pattern=r"^delfed:"))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))

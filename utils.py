"""Ortak yardımcı fonksiyonlar: yetki kontrolü, hedef kullanıcı, süre ayrıştırma."""
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import Chat, Update
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config

_DUR = re.compile(r"^(\d+)([smhdw])$", re.I)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def parse_duration(text: str) -> Optional[datetime]:
    """'10m', '2h', '1d', '1w' -> gelecekteki bir datetime. Geçersizse None."""
    m = _DUR.match(text or "")
    if not m:
        return None
    seconds = int(m.group(1)) * _UNITS[m.group(2).lower()]
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def is_admin(chat: Chat, user_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id == config.OWNER_ID:
        return True
    try:
        m = await ctx.bot.get_chat_member(chat.id, user_id)
    except TelegramError:
        return False
    return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


async def can_restrict(chat: Chat, user_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ban/susturma yetkisi: grup sahibi ya da 'üyeleri kısıtla' yetkili yönetici."""
    if user_id == config.OWNER_ID:
        return True
    try:
        m = await ctx.bot.get_chat_member(chat.id, user_id)
    except TelegramError:
        return False
    if m.status == ChatMemberStatus.OWNER:
        return True
    return m.status == ChatMemberStatus.ADMINISTRATOR and bool(
        getattr(m, "can_restrict_members", False)
    )


async def is_chat_creator(chat: Chat, user_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id == config.OWNER_ID:
        return True
    try:
        m = await ctx.bot.get_chat_member(chat.id, user_id)
    except TelegramError:
        return False
    return m.status == ChatMemberStatus.OWNER


async def extract_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Hedefi bulur: yanıtlanan mesaj > text_mention > sayısal ID.

    Dönüş: (user_id | None, görünen_ad, kalan_argümanlar)
    Not: Bot, @kullanıcıadı'ndan ID çözemez; yanıt ya da ID gerekir.
    """
    msg = update.effective_message
    args = list(ctx.args or [])

    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, u.full_name, args

    for ent in msg.entities or []:
        if ent.type == "text_mention" and ent.user:
            rest = [a for a in args if a != ent.user.full_name]
            return ent.user.id, ent.user.full_name, rest[1:] if args else rest

    if args and args[0].lstrip("-").isdigit():
        uid = int(args[0])
        return uid, str(uid), args[1:]

    return None, "", args

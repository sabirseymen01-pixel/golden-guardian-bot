"""Asenkron MongoDB (motor) veri katmanı."""
import secrets
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient

import config

_client = AsyncIOMotorClient(config.MONGO_URI)
_db = _client[config.DB_NAME]

feds = _db["feds"]    # {fed_id, name, owner_id, admins: [int]}
fbans = _db["fbans"]  # {fed_id, user_id, reason, by, date}
chats = _db["chats"]  # {chat_id, title, fed_id}


async def init() -> None:
    await feds.create_index("fed_id", unique=True)
    await fbans.create_index([("fed_id", 1), ("user_id", 1)], unique=True)
    await chats.create_index("chat_id", unique=True)
    await chats.create_index("fed_id")


# ---------- Fed yönetimi ----------
async def create_fed(owner_id: int, name: str) -> dict:
    fed = {
        "fed_id": secrets.token_hex(4),
        "name": name,
        "owner_id": owner_id,
        "admins": [],
    }
    await feds.insert_one(fed)
    return fed


async def get_fed(fed_id: str) -> Optional[dict]:
    return await feds.find_one({"fed_id": fed_id})


async def get_owned_fed(owner_id: int) -> Optional[dict]:
    return await feds.find_one({"owner_id": owner_id})


async def get_managed_fed(user_id: int) -> Optional[dict]:
    """Kullanıcının sahibi ya da yöneticisi olduğu ilk fed."""
    return await feds.find_one({"$or": [{"owner_id": user_id}, {"admins": user_id}]})


async def get_fed_for_chat(chat_id: int) -> Optional[dict]:
    row = await chats.find_one({"chat_id": chat_id, "fed_id": {"$ne": None}})
    return await get_fed(row["fed_id"]) if row else None


async def delete_fed(fed_id: str) -> None:
    await feds.delete_one({"fed_id": fed_id})
    await fbans.delete_many({"fed_id": fed_id})
    await chats.update_many({"fed_id": fed_id}, {"$set": {"fed_id": None}})


async def join_fed(chat_id: int, title: str, fed_id: str) -> None:
    await chats.update_one(
        {"chat_id": chat_id},
        {"$set": {"title": title, "fed_id": fed_id}},
        upsert=True,
    )


async def leave_fed(chat_id: int) -> None:
    await chats.update_one({"chat_id": chat_id}, {"$set": {"fed_id": None}})


async def fed_chats(fed_id: str) -> list[dict]:
    return await chats.find({"fed_id": fed_id}).to_list(length=None)


async def add_fed_admin(fed_id: str, user_id: int) -> None:
    await feds.update_one({"fed_id": fed_id}, {"$addToSet": {"admins": user_id}})


async def remove_fed_admin(fed_id: str, user_id: int) -> None:
    await feds.update_one({"fed_id": fed_id}, {"$pull": {"admins": user_id}})


# ---------- Fed ban ----------
async def fban_user(fed_id: str, user_id: int, reason: str, by: int) -> None:
    await fbans.update_one(
        {"fed_id": fed_id, "user_id": user_id},
        {"$set": {"reason": reason, "by": by, "date": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def unfban_user(fed_id: str, user_id: int) -> bool:
    res = await fbans.delete_one({"fed_id": fed_id, "user_id": user_id})
    return res.deleted_count > 0


async def get_fban(fed_id: str, user_id: int) -> Optional[dict]:
    return await fbans.find_one({"fed_id": fed_id, "user_id": user_id})


async def count_fbans(fed_id: str) -> int:
    return await fbans.count_documents({"fed_id": fed_id})


async def list_fbans(fed_id: str, limit: int = 20) -> list[dict]:
    return await fbans.find({"fed_id": fed_id}).sort("date", -1).to_list(length=limit)

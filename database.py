import os
import datetime
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.environ.get("MONGO_DB_NAME", "golden_guardian_db")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

users_col = db["users"]
logs_col = db["logs"]
staff_col = db["staff"]
settings_col = db["group_settings"]
groups_col = db["bot_groups"]

def init_db():
    try:
        users_col.create_index("user_id", unique=True)
        staff_col.create_index("user_id", unique=True)
        settings_col.create_index("chat_id", unique=True)
        groups_col.create_index("chat_id", unique=True)
    except Exception:
        pass

def save_user(chat_id, user_id, username):
    try:
        clean_uname = username.lstrip("@").lower() if username else ""
        users_col.update_one(
            {"user_id": int(user_id)},
            {"$set": {"chat_id": chat_id, "username": clean_uname}},
            upsert=True
        )
    except Exception:
        pass

def register_bot_group(chat_id, title):
    try:
        groups_col.update_one(
            {"chat_id": int(chat_id)},
            {"$set": {"title": title, "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")}},
            upsert=True
        )
    except Exception:
        pass

def get_bot_groups():
    try:
        return list(groups_col.find({}, {"_id": 0}))
    except Exception:
        return []

def log_action(chat_id, action_type, target_id, username, details):
    try:
        now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        log_doc = {
            "chat_id": int(chat_id) if chat_id else 0,
            "action_type": action_type,
            "target_id": str(target_id),
            "username": username.lstrip("@") if username else "Bilinmiyor",
            "details": details,
            "timestamp": now,
            "datetime_obj": datetime.datetime.now()
        }
        logs_col.insert_one(log_doc)
    except Exception:
        pass

def get_recent_logs(limit=100, log_filter="all"):
    try:
        query = {}
        if log_filter == "admin":
            query["action_type"] = {"$in": ["BAN", "MUTE", "WEB_BAN", "WEB_MUTE", "UNBAN", "UNMUTE"]}
        elif log_filter == "general":
            query["action_type"] = {"$nin": ["BAN", "MUTE", "WEB_BAN", "WEB_MUTE", "UNBAN", "UNMUTE"]}
            
        rows = list(logs_col.find(query, {"_id": 0, "datetime_obj": 0}).sort("_id", -1).limit(limit))
        return rows
    except Exception:
        return []

def clear_logs_by_retention(days):
    try:
        if days > 0:
            threshold = datetime.datetime.now() - datetime.timedelta(days=days)
            logs_col.delete_many({"datetime_obj": {"$lt": threshold}})
        else:
            logs_col.delete_many({})
    except Exception:
        pass

def get_bot_statistics():
    try:
        total_actions = logs_col.count_documents({})
        pipeline = [{"$group": {"_id": "$action_type", "count": {"$sum": 1}}}]
        breakdown = {doc["_id"]: doc["count"] for doc in logs_col.aggregate(pipeline)}
        return {"total_actions": total_actions, "action_breakdown": breakdown}
    except Exception:
        return {"total_actions": 0, "action_breakdown": {}}

def get_admin_performance_stats():
    try:
        pipeline = [
            {"$match": {"action_type": {"$in": ["BAN", "MUTE", "WEB_BAN", "WEB_MUTE", "UNBAN", "UNMUTE"]}}},
            {"$group": {"_id": "$username", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        rows = list(logs_col.aggregate(pipeline))
        return [{"username": r["_id"], "count": r["count"]} for r in rows]
    except Exception:
        return []

def get_group_settings(chat_id):
    try:
        row = settings_col.find_one({"chat_id": int(chat_id)})
        if row:
            return {
                "welcome_msg": row.get("welcome_msg", "Hoş geldin!"),
                "btn_text": row.get("btn_text", "Buton"),
                "btn_url": row.get("btn_url", "https://t.me/"),
                "media_url": row.get("media_url", "")
            }
    except Exception:
        pass
    return {
        "welcome_msg": "Grup düzenini sağlamak için aktifim.",
        "btn_text": "🛡️ Golden Panel",
        "btn_url": "https://golden-guardian-bot.onrender.com/",
        "media_url": ""
    }

def update_group_setting(chat_id, field, value):
    try:
        settings = get_group_settings(chat_id)
        settings[field] = value
        settings_col.update_one(
            {"chat_id": int(chat_id)},
            {"$set": {
                "welcome_msg": settings["welcome_msg"],
                "btn_text": settings["btn_text"],
                "btn_url": settings["btn_url"],
                "media_url": settings.get("media_url", "")
            }},
            upsert=True
        )
    except Exception:
        pass

def is_authorized_staff(user_id):
    try:
        res = staff_col.find_one({"user_id": int(user_id)})
        return res is not None
    except Exception:
        return False

def get_staff_permissions(user_id):
    try:
        row = staff_col.find_one({"user_id": int(user_id)})
        if row:
            return {"can_ban": row.get("can_ban", 1), "warnings": row.get("warnings", 0)}
    except Exception:
        pass
    return {"can_ban": 1, "warnings": 0}

def get_all_staff():
    try:
        rows = list(staff_col.find({}, {"_id": 0}))
        return rows
    except Exception:
        return []

def add_staff_member(user_id, username, full_name, added_by):
    try:
        clean_uname = username.lstrip("@").lower() if username else ""
        staff_doc = {
            "user_id": int(user_id),
            "username": clean_uname,
            "full_name": full_name,
            "can_ban": 1,
            "warnings": 0,
            "added_by": added_by
        }
        staff_col.update_one({"user_id": int(user_id)}, {"$set": staff_doc}, upsert=True)
    except Exception:
        pass

def remove_staff_member(user_id):
    try:
        staff_col.delete_one({"user_id": int(user_id)})
    except Exception:
        pass

def get_user_id_by_username(chat_id, username):
    try:
        clean_uname = username.lstrip("@").lower() if username else ""
        row = users_col.find_one({"username": {"$regex": f"^{clean_uname}$", "$options": "i"}})
        if row:
            return row.get("user_id")
    except Exception:
        pass
    return None

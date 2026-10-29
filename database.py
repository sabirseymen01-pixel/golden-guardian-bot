import os
import datetime
from pymongo import MongoClient

# Render ortamındaki MONGO_URI değişkenini alır
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.environ.get("MONGO_DB_NAME", "golden_guardian_db")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

users_col = db["users"]
logs_col = db["logs"]
staff_col = db["staff"]
settings_col = db["group_settings"]

def init_db():
    try:
        users_col.create_index("user_id", unique=True)
        staff_col.create_index("user_id", unique=True)
        settings_col.create_index("chat_id", unique=True)
    except Exception:
        pass

def save_user(chat_id, user_id, username):
    try:
        users_col.update_one(
            {"user_id": int(user_id)},
            {"$set": {"chat_id": chat_id, "username": (username or "").lower()}},
            upsert=True
        )
    except Exception:
        pass

def log_action(chat_id, action_type, target_id, username, details):
    try:
        now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        log_doc = {
            "chat_id": chat_id,
            "action_type": action_type,
            "target_id": str(target_id),
            "username": username,
            "details": details,
            "timestamp": now
        }
        logs_col.insert_one(log_doc)
    except Exception:
        pass

def get_recent_logs(limit=50):
    try:
        rows = list(logs_col.find().sort("_id", -1).limit(limit))
        return [{"action_type": r.get("action_type"), "username": r.get("username"), "details": r.get("details"), "timestamp": r.get("timestamp")} for r in rows]
    except Exception:
        return []

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
            {"$group": {"_id": "$username", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]
        rows = list(logs_col.aggregate(pipeline))
        return [{"username": r["_id"], "count": r["count"]} for r in rows]
    except Exception:
        return []

def get_data_cloud_tags():
    try:
        pipeline = [
            {"$group": {"_id": "$action_type", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        rows = list(logs_col.aggregate(pipeline))
        return [{"tag": r["_id"], "count": r["count"]} for r in rows]
    except Exception:
        return []

def get_group_settings(chat_id):
    try:
        row = settings_col.find_one({"chat_id": int(chat_id)})
        if row:
            return {"welcome_msg": row.get("welcome_msg"), "btn_text": row.get("btn_text"), "btn_url": row.get("btn_url")}
    except Exception:
        pass
    return {
        "welcome_msg": "Grup düzenini sağlamak, argo/küfür ve yasaklı içerikleri engellemek için aktifim.",
        "btn_text": "🛡️ Golden Staff Paneli",
        "btn_url": "https://golden-guardian-bot.onrender.com/"
    }

def update_group_setting(chat_id, field, value):
    try:
        settings = get_group_settings(chat_id)
        settings[field] = value
        settings_col.update_one(
            {"chat_id": int(chat_id)},
            {"$set": {"welcome_msg": settings["welcome_msg"], "btn_text": settings["btn_text"], "btn_url": settings["btn_url"]}},
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
            return {
                "can_ban": row.get("can_ban", 1),
                "can_speak": row.get("can_speak", 1),
                "can_write": row.get("can_write", 1),
                "is_muted": row.get("is_muted", 0),
                "warnings": row.get("warnings", 0)
            }
    except Exception:
        pass
    return {"can_ban": 1, "can_speak": 1, "can_write": 1, "is_muted": 0, "warnings": 0}

def add_warning(user_id):
    try:
        staff_col.update_one({"user_id": int(user_id)}, {"$inc": {"warnings": 1}}, upsert=True)
        row = staff_col.find_one({"user_id": int(user_id)})
        return row.get("warnings", 1) if row else 1
    except Exception:
        return 1

def reset_warnings(user_id):
    try:
        staff_col.update_one({"user_id": int(user_id)}, {"$set": {"warnings": 0}})
    except Exception:
        pass

def get_all_staff():
    try:
        rows = list(staff_col.find())
        return [(r.get("user_id"), r.get("username"), r.get("full_name"), r.get("can_ban"), r.get("can_speak"), r.get("can_write"), r.get("is_muted"), r.get("warnings")) for r in rows]
    except Exception:
        return []

def add_staff_member(user_id, username, full_name, added_by):
    try:
        staff_doc = {
            "user_id": int(user_id),
            "username": username,
            "full_name": full_name,
            "can_ban": 1,
            "can_speak": 1,
            "can_write": 1,
            "is_muted": 0,
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

def set_staff_permission(user_id, perm_type, status):
    try:
        if perm_type in ['can_ban', 'can_speak', 'can_write', 'is_muted']:
            staff_col.update_one({"user_id": int(user_id)}, {"$set": {perm_type: status}})
    except Exception:
        pass

def get_user_id_by_username(chat_id, username):
    try:
        row = users_col.find_one({"username": {"$regex": f"^{username}$", "$options": "i"}})
        if row:
            return row.get("user_id")
    except Exception:
        pass
    return None

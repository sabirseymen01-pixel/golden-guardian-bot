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
scheduled_jobs_col = db["scheduled_jobs"]
templates_col = db["templates"]

def init_db():
    try:
        users_col.create_index("user_id", unique=True)
        staff_col.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
        settings_col.create_index("chat_id", unique=True)
        groups_col.create_index("chat_id", unique=True)
        scheduled_jobs_col.create_index("job_id", unique=True)
        templates_col.create_index([("chat_id", 1), ("template_id", 1)], unique=True)
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

def register_bot_group(chat_id, title, photo_url=""):
    try:
        update_data = {
            "title": title,
            "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        }
        if photo_url:
            update_data["photo_url"] = photo_url
            
        groups_col.update_one(
            {"chat_id": int(chat_id)},
            {"$set": update_data},
            upsert=True
        )
    except Exception:
        pass

def get_bot_groups():
    try:
        return list(groups_col.find({}, {"_id": 0}))
    except Exception:
        return []

def get_group_title(chat_id):
    try:
        if not chat_id or int(chat_id) == 0:
            return "Tüm Yörünge Grupları (Global)"
        g = groups_col.find_one({"chat_id": int(chat_id)})
        return g.get("title", f"Grup ID: {chat_id}") if g else f"Grup ID: {chat_id}"
    except Exception:
        return "Bilinmeyen Grup"

def log_action(chat_id, action_type, target_id, username, details):
    try:
        now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        group_name = get_group_title(chat_id)
        log_doc = {
            "chat_id": int(chat_id) if chat_id else 0,
            "group_title": group_name,
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
        return list(logs_col.find({}, {"_id": 0, "datetime_obj": 0}).sort("_id", -1).limit(limit))
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
                "media_url": row.get("media_url", ""),
                "announcement_btn_text": row.get("announcement_btn_text", row.get("btn_text", "Buton")),
                "announcement_btn_url": row.get("announcement_btn_url", row.get("btn_url", "https://t.me/"))
            }
    except Exception:
        pass
    return {
        "welcome_msg": "Grup düzenini sağlamak için aktifim.",
        "btn_text": "🛡️ Golden Panel",
        "btn_url": "https://golden-guardian-bot.onrender.com/",
        "media_url": "",
        "announcement_btn_text": "🛡️ Golden Panel",
        "announcement_btn_url": "https://golden-guardian-bot.onrender.com/"
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
                "media_url": settings.get("media_url", ""),
                "announcement_btn_text": settings.get("announcement_btn_text", settings["btn_text"]),
                "announcement_btn_url": settings.get("announcement_btn_url", settings["btn_url"])
            }},
            upsert=True
        )
    except Exception:
        pass

def get_announcement_settings(chat_id):
    try:
        row = settings_col.find_one({"chat_id": int(chat_id)})
        if row:
            return {
                "btn_text": row.get("announcement_btn_text", row.get("btn_text", "🛡️ Golden Panel")),
                "btn_url": row.get("announcement_btn_url", row.get("btn_url", "https://golden-guardian-bot.onrender.com/"))
            }
    except Exception:
        pass
    return {
        "btn_text": "🛡️ Golden Panel",
        "btn_url": "https://golden-guardian-bot.onrender.com/"
    }

def is_authorized_staff(user_id, chat_id=0):
    try:
        if chat_id and int(chat_id) != 0:
            res = staff_col.find_one({"user_id": int(user_id), "chat_id": int(chat_id)})
            if res: return True
        return staff_col.find_one({"user_id": int(user_id), "chat_id": 0}) is not None
    except Exception:
        return False

def get_staff_permissions(user_id, chat_id=0):
    try:
        if chat_id and int(chat_id) != 0:
            row = staff_col.find_one({"user_id": int(user_id), "chat_id": int(chat_id)})
            if row:
                return {
                    "can_ban": row.get("can_ban", True),
                    "can_mute": row.get("can_mute", True),
                    "warnings": row.get("warnings", 0)
                }
        row = staff_col.find_one({"user_id": int(user_id), "chat_id": 0})
        if row:
            return {
                "can_ban": row.get("can_ban", True),
                "can_mute": row.get("can_mute", True),
                "warnings": row.get("warnings", 0)
            }
    except Exception:
        pass
    return {"can_ban": True, "can_mute": True, "warnings": 0}

def get_all_staff(chat_id=0):
    try:
        query = {}
        if chat_id and int(chat_id) != 0:
            query["chat_id"] = int(chat_id)
        return list(staff_col.find(query, {"_id": 0}))
    except Exception:
        return []

def add_staff_member(user_id, username, full_name, added_by, chat_id=0):
    try:
        clean_uname = username.lstrip("@").lower() if username else ""
        staff_doc = {
            "user_id": int(user_id),
            "chat_id": int(chat_id),
            "username": clean_uname,
            "full_name": full_name,
            "can_ban": True,
            "can_mute": True,
            "warnings": 0,
            "added_by": added_by
        }
        staff_col.update_one({"user_id": int(user_id), "chat_id": int(chat_id)}, {"$set": staff_doc}, upsert=True)
    except Exception:
        pass

def toggle_staff_permission(user_id, chat_id, perm_field):
    try:
        query = {"user_id": int(user_id), "chat_id": int(chat_id)}
        row = staff_col.find_one(query)
        if not row:
            query["chat_id"] = 0
            row = staff_col.find_one(query)
            
        if row:
            current_val = row.get(perm_field, True)
            new_val = not current_val
            staff_col.update_one(query, {"$set": {perm_field: new_val}})
            return new_val
    except Exception:
        pass
    return None

def remove_staff_member(user_id, chat_id=0):
    try:
        query = {"user_id": int(user_id)}
        if chat_id and int(chat_id) != 0: query["chat_id"] = int(chat_id)
        staff_col.delete_one(query)
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

def add_warning(user_id):
    try:
        res = users_col.find_one_and_update(
            {"user_id": int(user_id)},
            {"$inc": {"warnings": 1}},
            upsert=True,
            return_document=True
        )
        return res.get("warnings", 1) if res else 1
    except Exception:
        return 1

def reset_warnings(user_id):
    try:
        users_col.update_one(
            {"user_id": int(user_id)},
            {"$set": {"warnings": 0}},
            upsert=True
        )
        return True
    except Exception:
        return False

def save_scheduled_job(job_id, chat_id, minutes, msg_text, btn_text, btn_url, media_url):
    try:
        scheduled_jobs_col.update_one(
            {"job_id": job_id},
            {"$set": {
                "chat_id": int(chat_id),
                "minutes": int(minutes),
                "msg_text": msg_text,
                "btn_text": btn_text,
                "btn_url": btn_url,
                "media_url": media_url
            }},
            upsert=True
        )
    except Exception:
        pass

def remove_scheduled_job_db(job_id):
    try:
        scheduled_jobs_col.delete_one({"job_id": job_id})
    except Exception:
        pass

def get_all_scheduled_jobs():
    try:
        return list(scheduled_jobs_col.find({}, {"_id": 0}))
    except Exception:
        return []

def get_latest_scheduled_job(chat_id):
    try:
        return scheduled_jobs_col.find_one({"chat_id": int(chat_id)}, sort=[("_id", -1)])
    except Exception:
        return None

def get_group_templates(chat_id):
    try:
        rows = list(templates_col.find({"chat_id": int(chat_id)}, {"_id": 0}))
        announcements = []
        schedules = []
        for r in rows:
            item = {
                "id": r.get("template_id"),
                "name": r.get("name"),
                "msg": r.get("msg"),
                "media": r.get("media", ""),
                "btnText": r.get("btn_text", ""),
                "btnUrl": r.get("btn_url", "")
            }
            if r.get("type") == "schedule":
                item["mins"] = r.get("mins", 60)
                schedules.append(item)
            else:
                announcements.append(item)
        return {"announcements": announcements, "schedules": schedules}
    except Exception:
        return {"announcements": [], "schedules": []}

def save_template_db(chat_id, template_type, name, msg, media, btn_text, btn_url, mins=0, template_id=None):
    try:
        t_id = int(template_id) if template_id else int(datetime.datetime.now().timestamp() * 1000)
        templates_col.update_one(
            {"chat_id": int(chat_id), "template_id": t_id},
            {"$set": {
                "chat_id": int(chat_id),
                "template_id": t_id,
                "type": template_type,
                "name": name,
                "msg": msg,
                "media": media,
                "btn_text": btn_text,
                "btn_url": btn_url,
                "mins": int(mins)
            }},
            upsert=True
        )
        return t_id
    except Exception:
        return None

def database_delete_template_db(chat_id, template_id):
    try:
        templates_col.delete_one({"chat_id": int(chat_id), "template_id": int(template_id)})
        return True
    except Exception:
        return False

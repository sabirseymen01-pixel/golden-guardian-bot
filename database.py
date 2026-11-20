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
events_col = db["events"]
event_votes_col = db["event_votes"]

gv_state_col = db["goldenvision_state"]
gv_weeks_col = db["goldenvision_weeks"]
gv_entries_col = db["goldenvision_entries"]
gv_ballots_col = db["goldenvision_ballots"]
gv_comments_col = db["goldenvision_comments"]
gv_bans_col = db["goldenvision_bans"]
gv_hosts_col = db["goldenvision_hosts"]


def _default_25_weeks():
    week_types = {
        1: ("ÇAKILI HAFTA", "Çakılı Hafta • Tek Ülke / Farklı Şarkı", "3 hafta boyunca tek ülke; her hafta farklı şarkı (75M altı)."),
        2: ("ÇAKILI HAFTA", "Çakılı Hafta • Tek Ülke / Farklı Şarkı", "3 hafta boyunca tek ülke; her hafta farklı şarkı (75M altı)."),
        3: ("ÇAKILI HAFTA", "Çakılı Hafta • Tek Ülke / Farklı Şarkı", "3 hafta boyunca tek ülke; her hafta farklı şarkı (75M altı)."),
        4: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        5: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        6: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        7: ("ÇİFT HAFTASI", "Çift Haftası • İkili Sahne", "Yarışmacılar 2'şer kişilik çift olarak eşleşir; çift 1 ülke ve 1 şarkı ile yarışır."),
        8: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        9: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        10: ("ÖZEL HAFTA", "Özel Konsept / Saygı Haftası", "Anketle seçilen konsept veya sanatçı saygı haftası."),
        11: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        12: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        13: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        14: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        15: ("ÖZEL HAFTA", "Özel Konsept / Saygı Haftası", "Anketle seçilen konsept veya sanatçı saygı haftası."),
        16: ("ÇAKILI HAFTA", "Çakılı Hafta • Tek Ülke / Farklı Şarkı", "3 hafta boyunca tek ülke; her hafta farklı şarkı (75M altı)."),
        17: ("ÇAKILI HAFTA", "Çakılı Hafta • Tek Ülke / Farklı Şarkı", "3 hafta boyunca tek ülke; her hafta farklı şarkı (75M altı)."),
        18: ("ÇAKILI HAFTA", "Çakılı Hafta • Tek Ülke / Farklı Şarkı", "3 hafta boyunca tek ülke; her hafta farklı şarkı (75M altı)."),
        19: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        20: ("ÖZEL HAFTA", "Özel Konsept / Saygı Haftası", "Anketle seçilen konsept veya sanatçı saygı haftası."),
        21: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        22: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        23: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        24: ("NORMAL HAFTA", "Standart Eurovision Haftası", "3 ülke tercihi; her hafta farklı ülke ve şarkıcı (75M altı, ABD haftada 1)."),
        25: ("SECOND CHANCE", "Second Chance • Büyük Şans", "25 hafta boyunca yarışmış ve kazanmasını istediğin şarkıyla sahneye çık."),
    }
    out = []
    for w in range(1, 26):
        wtype, concept, rule = week_types.get(w, ("NORMAL HAFTA", "Standart Eurovision Haftası", "75M izlenme sınırı geçerlidir."))
        out.append({
            "week": w,
            "week_type": wtype,
            "concept": concept,
            "rule_note": rule,
            "song_duration_sec": 120,
            "submissions_open": True,
            "voting_open": False,
            "comments_open": False,
            "results_published": False
        })
    return out


def ensure_season_initialized(season: int):
    season = max(1, int(season))
    state = gv_state_col.find_one({"season": season})
    if not state:
        gv_state_col.insert_one({
            "season": season,
            "current_week": 1,
            "paused": False,
            "max_views_million": 75,
            "song_duration_sec": 120,
            "submissions_open": True,
            "voting_open": False,
            "comments_open": False,
            "results_published": False,
            "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        })
    if gv_weeks_col.count_documents({"season": season}) < 25:
        for wk in _default_25_weeks():
            gv_weeks_col.update_one(
                {"season": season, "week": wk["week"]},
                {"$setOnInsert": {**wk, "season": season}},
                upsert=True
            )


def get_active_season_number() -> int:
    meta = gv_state_col.find_one({"_id": "meta_active"})
    if meta and meta.get("active_season"):
        s = int(meta["active_season"])
        ensure_season_initialized(s)
        return s
    latest = gv_state_col.find_one({"season": {"$exists": True}}, sort=[("season", -1)])
    s = int(latest["season"]) if latest else 3
    gv_state_col.update_one({"_id": "meta_active"}, {"$set": {"active_season": s}}, upsert=True)
    ensure_season_initialized(s)
    return s


def set_active_season(season: int, reset_week_to_one: bool = False):
    season = max(1, int(season))
    ensure_season_initialized(season)
    gv_state_col.update_one({"_id": "meta_active"}, {"$set": {"active_season": season}}, upsert=True)
    if reset_week_to_one:
        gv_state_col.update_one(
            {"season": season},
            {"$set": {"current_week": 1, "paused": False, "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")}}
        )
    return season


def get_gv_overview(season: int = None, user_id: int = 0):
    s = int(season) if season else get_active_season_number()
    ensure_season_initialized(s)
    st = gv_state_col.find_one({"season": s}, {"_id": 0}) or {}
    cur_week = int(st.get("current_week", 1))
    weeks = list(gv_weeks_col.find({"season": s}, {"_id": 0}).sort("week", 1))
    active_week_obj = next((w for w in weeks if int(w.get("week", 1)) == cur_week), weeks[0] if weeks else {})

    entries = list(gv_entries_col.find({"season": s, "week": cur_week}, {"_id": 0}))
    ballots = list(gv_ballots_col.find({"season": s, "week": cur_week}, {"_id": 0}))
    comments = list(gv_comments_col.find({"season": s, "week": cur_week}, {"_id": 0}).sort("_id", -1).limit(50))

    # Puan hesaplama (12, 10, 8, 7, 6, 5, 4, 3, 2, 1)
    # entry_id -> total_score ve voter_id -> {entry_id: pts}
    score_map = {}
    for e in entries:
        eid = e["entry_id"]
        score_map[eid] = {
            "entry_id": eid,
            "country": e.get("country", ""),
            "flag": e.get("flag", "🏳️"),
            "song": e.get("song", ""),
            "artist": e.get("artist", ""),
            "views_m": e.get("views_m", 0),
            "pair_partner": e.get("pair_partner", ""),
            "user_id": e.get("user_id"),
            "username": e.get("username", ""),
            "total": 0,
            "twelve_count": 0,
            "votes_from": {}
        }

    for b in ballots:
        voter_id = str(b.get("voter_id"))
        voter_flag = b.get("voter_flag", "🏳️")
        voter_code = b.get("voter_code", voter_id[-3:])
        allocations = b.get("allocations", {})  # {str(entry_id): pts}
        for eid_str, pts in allocations.items():
            eid = int(eid_str)
            p = int(pts)
            if eid in score_map:
                score_map[eid]["total"] += p
                if p == 12:
                    score_map[eid]["twelve_count"] += 1
                score_map[eid]["votes_from"][voter_id] = {
                    "pts": p,
                    "flag": voter_flag,
                    "code": voter_code
                }

    ranking = sorted(
        score_map.values(),
        key=lambda x: (x["total"], x["twelve_count"]),
        reverse=True
    )

    my_entry = None
    my_ballot = None
    ban_info = None
    if user_id:
        my_entry = next((e for e in entries if int(e.get("user_id", 0)) == int(user_id)), None)
        my_ballot = next((b for b in ballots if int(b.get("voter_id", 0)) == int(user_id)), None)
        ban_doc = gv_bans_col.find_one({"user_id": int(user_id)}, {"_id": 0})
        if ban_doc:
            ban_info = ban_doc

    # Kullanıcının önceki haftalarda kullandığı ülkeler ve ABD kullanımı
    used_countries_season = []
    usa_used_count = 0
    if user_id:
        past_entries = list(gv_entries_col.find({"season": s, "user_id": int(user_id)}, {"_id": 0}))
        for pe in past_entries:
            c_name = (pe.get("country") or "").strip()
            if c_name:
                used_countries_season.append({"week": pe.get("week"), "country": c_name, "artist": pe.get("artist", ""), "song": pe.get("song", "")})
                if c_name.upper() in ["USA", "ABD", "UNITED STATES", "AMERİKA"]:
                    usa_used_count += 1

    all_seasons = sorted([int(x["season"]) for x in gv_state_col.find({"season": {"$exists": True}}, {"season": 1}) if x.get("season")])
    if s not in all_seasons:
        all_seasons.append(s)
        all_seasons.sort()

    return {
        "season": s,
        "available_seasons": all_seasons,
        "state": st,
        "current_week": cur_week,
        "active_week": active_week_obj,
        "weeks": weeks,
        "entries": entries,
        "ranking": ranking,
        "ballots_count": len(ballots),
        "comments": comments,
        "my_entry": my_entry,
        "my_ballot": my_ballot,
        "ban_info": ban_info,
        "used_countries_season": used_countries_season,
        "usa_used_count": usa_used_count
    }


def update_gv_state(season: int, updates: dict):
    season = int(season)
    ensure_season_initialized(season)
    allowed = {"current_week", "paused", "max_views_million", "song_duration_sec", "submissions_open", "voting_open", "comments_open", "results_published"}
    clean = {k: v for k, v in updates.items() if k in allowed}
    clean["updated_at"] = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    gv_state_col.update_one({"season": season}, {"$set": clean}, upsert=True)
    if "current_week" in clean:
        w_num = int(clean["current_week"])
        wk = gv_weeks_col.find_one({"season": season, "week": w_num})
        if wk:
            gv_state_col.update_one(
                {"season": season},
                {"$set": {
                    "submissions_open": wk.get("submissions_open", True),
                    "voting_open": wk.get("voting_open", False),
                    "comments_open": wk.get("comments_open", False),
                    "results_published": wk.get("results_published", False),
                    "song_duration_sec": wk.get("song_duration_sec", 120)
                }}
            )


def update_gv_week(season: int, week: int, updates: dict):
    season = int(season)
    week = int(week)
    ensure_season_initialized(season)
    allowed = {"week_type", "concept", "rule_note", "song_duration_sec", "submissions_open", "voting_open", "comments_open", "results_published"}
    clean = {k: v for k, v in updates.items() if k in allowed}
    gv_weeks_col.update_one({"season": season, "week": week}, {"$set": clean}, upsert=True)
    st = gv_state_col.find_one({"season": season}) or {}
    if int(st.get("current_week", 1)) == week:
        sync_keys = {k: v for k, v in clean.items() if k in {"submissions_open", "voting_open", "comments_open", "results_published", "song_duration_sec"}}
        if sync_keys:
            gv_state_col.update_one({"season": season}, {"$set": sync_keys})


def restart_gv_week(season: int, week: int):
    season = int(season)
    week = int(week)
    ensure_season_initialized(season)
    gv_ballots_col.delete_many({"season": season, "week": week})
    gv_comments_col.delete_many({"season": season, "week": week})
    gv_state_col.update_one(
        {"season": season},
        {"$set": {"paused": False, "results_published": False, "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")}}
    )
    gv_weeks_col.update_one({"season": season, "week": week}, {"$set": {"results_published": False}})


def submit_gv_entry(season: int, week: int, user_id: int, username: str, full_name: str, countries_list: list, country: str, flag: str, artist: str, song: str, views_m: float, pair_partner: str = ""):
    season = int(season)
    week = int(week)
    ensure_season_initialized(season)

    ban_doc = gv_bans_col.find_one({"user_id": int(user_id)})
    if ban_doc and ban_doc.get("banned"):
        return {"ok": False, "error": ban_doc.get("reason", "Yarışma katılım yasağınız bulunuyor.")}

    st = gv_state_col.find_one({"season": season}) or {}
    if st.get("paused"):
        return {"ok": False, "error": "Yarışma şu anda Host tarafından duraklatıldı."}
    if not st.get("submissions_open", True):
        return {"ok": False, "error": "Bu hafta için katılım penceresi kapalı."}

    max_views = float(st.get("max_views_million", 75))
    if float(views_m or 0) > max_views:
        return {"ok": False, "error": f"Şarkı izlenmesi {max_views}M sınırını aşamaz!"}

    wk_doc = gv_weeks_col.find_one({"season": season, "week": week}) or {}
    wtype = (wk_doc.get("week_type") or "").upper()

    clean_countries = [c.strip() for c in (countries_list or []) if str(c).strip()]
    primary_country = (country or (clean_countries[0] if clean_countries else "")).strip()
    if not primary_country or not artist.strip() or not song.strip():
        return {"ok": False, "error": "Ülke, şarkıcı ve şarkı adı zorunludur."}

    if "ÇİFT" not in wtype and "SECOND CHANCE" not in wtype and len(clean_countries) < 3:
        return {"ok": False, "error": "Bu formatta 3 ülke tercihi girilmelidir (Örn: USA, UK, Gana)."}

    # ABD kontrolü (sezon içinde en fazla 1 kez)
    all_user_entries = list(gv_entries_col.find({"season": season, "user_id": int(user_id)}))
    usa_count = sum(1 for x in all_user_entries if (x.get("country") or "").strip().upper() in ["USA", "ABD", "UNITED STATES", "AMERİKA"])
    if primary_country.upper() in ["USA", "ABD", "UNITED STATES", "AMERİKA"]:
        already_this_week = any(int(x.get("week", -1)) == week and (x.get("country") or "").strip().upper() in ["USA", "ABD", "UNITED STATES", "AMERİKA"] for x in all_user_entries)
        if usa_count >= 1 and not already_this_week:
            return {"ok": False, "error": "ABD (USA) her yarışmacı tarafından 1 kez alınabilir!"}

    # Aynı ülkeyi tekrar alma kontrolü (Çakılı Hafta hariç)
    if "ÇAKILI" not in wtype:
        same_country_past = [x for x in all_user_entries if int(x.get("week", -1)) != week and (x.get("country") or "").strip().lower() == primary_country.lower()]
        if same_country_past:
            return {"ok": False, "error": f"{primary_country} ülkesi bu sezonda zaten kullanıldı!"}

    # Aynı şarkıcıyı haftada en fazla 3 kez seçme kontrolü
    same_artist_count = sum(1 for x in all_user_entries if (x.get("artist") or "").strip().lower() == artist.strip().lower())
    if same_artist_count > 3:
        return {"ok": False, "error": f"{artist} sanatçısı sınırını aştı!"}

    existing = gv_entries_col.find_one({"season": season, "week": week, "user_id": int(user_id)})
    entry_id = int(existing["entry_id"]) if existing else int(datetime.datetime.now().timestamp() * 1000)

    doc = {
        "entry_id": entry_id,
        "season": season,
        "week": week,
        "user_id": int(user_id),
        "username": (username or "").lstrip("@"),
        "full_name": full_name or username or f"Üye_{user_id}",
        "countries": clean_countries[:3],
        "country": primary_country,
        "flag": (flag or "🏳️").strip(),
        "artist": artist.strip(),
        "song": song.strip(),
        "views_m": float(views_m or 0),
        "pair_partner": pair_partner.strip(),
        "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    }
    gv_entries_col.update_one({"season": season, "week": week, "user_id": int(user_id)}, {"$set": doc}, upsert=True)
    return {"ok": True, "entry": doc}


def submit_gv_ballot(season: int, week: int, voter_id: int, voter_username: str, voter_flag: str, allocations: dict):
    season = int(season)
    week = int(week)
    ensure_season_initialized(season)

    st = gv_state_col.find_one({"season": season}) or {}
    if st.get("paused"):
        return {"ok": False, "error": "Yarışma duraklatıldı."}
    if not st.get("voting_open", False):
        return {"ok": False, "error": "Oylama şu anda kapalı."}

    allowed_points = {12, 10, 8, 7, 6, 5, 4, 3, 2, 1}
    clean_alloc = {}
    used_pts = []

    entries = list(gv_entries_col.find({"season": season, "week": week}))
    valid_eids = {int(e["entry_id"]) for e in entries}
    own_eids = {int(e["entry_id"]) for e in entries if int(e.get("user_id", 0)) == int(voter_id)}

    for eid_str, pts in (allocations or {}).items():
        if pts is None or str(pts).strip() == "":
            continue
        eid = int(eid_str)
        p = int(pts)
        if eid not in valid_eids:
            return {"ok": False, "error": "Geçersiz şarkı seçimi."}
        if eid in own_eids:
            return {"ok": False, "error": "Kendi şarkınıza oy veremezsiniz!"}
        if p not in allowed_points:
            return {"ok": False, "error": "Puanlar 12, 10, 8, 7, 6, 5, 4, 3, 2, 1 olmalıdır."}
        if p in used_pts:
            return {"ok": False, "error": f"{p} puanı birden fazla şarkıya verilemez!"}
        used_pts.append(p)
        clean_alloc[str(eid)] = p

    if not clean_alloc:
        return {"ok": False, "error": "En az 1 puan atamalısınız."}

    ballot_doc = {
        "season": season,
        "week": week,
        "voter_id": int(voter_id),
        "voter_username": (voter_username or "").lstrip("@"),
        "voter_flag": voter_flag or "🏳️",
        "voter_code": (voter_username or str(voter_id))[:8],
        "allocations": clean_alloc,
        "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    }
    gv_ballots_col.update_one({"season": season, "week": week, "voter_id": int(voter_id)}, {"$set": ballot_doc}, upsert=True)
    return {"ok": True, "ballot": ballot_doc}


def add_gv_comment(season: int, week: int, user_id: int, username: str, entry_id: int, text: str):
    season = int(season)
    week = int(week)
    st = gv_state_col.find_one({"season": season}) or {}
    if not st.get("comments_open", False):
        return {"ok": False, "error": "Yorumlar şu anda Host tarafından kapalı tutuluyor."}
    if not text or not text.strip():
        return {"ok": False, "error": "Yorum metni boş olamaz."}
    doc = {
        "season": season,
        "week": week,
        "entry_id": int(entry_id),
        "user_id": int(user_id),
        "username": (username or "").lstrip("@") or f"Üye_{user_id}",
        "text": text.strip()[:240],
        "timestamp": datetime.datetime.now().strftime("%H:%M")
    }
    gv_comments_col.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, "comment": doc}


def set_gv_host_access(target_user_id: int, can_manage: bool, granted_by: int):
    gv_hosts_col.update_one(
        {"user_id": int(target_user_id)},
        {"$set": {
            "user_id": int(target_user_id),
            "can_manage": bool(can_manage),
            "granted_by": int(granted_by),
            "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        }},
        upsert=True
    )


def can_access_gv_studio(user_id: int, founder_id: str) -> bool:
    if str(user_id) == str(founder_id).strip():
        return True
    doc = gv_hosts_col.find_one({"user_id": int(user_id)})
    return bool(doc and doc.get("can_manage"))


def list_gv_hosts():
    return list(gv_hosts_col.find({}, {"_id": 0}))


def apply_gv_penalty(target_user_id: int, mode: str, reason: str = ""):
    # mode: 'leak_first' (3 hafta yarışma banı) | 'leak_repeat' (tam yarışma/grup banı) | 'clear'
    uid = int(target_user_id)
    if mode == "clear":
        gv_bans_col.delete_one({"user_id": uid})
        return {"banned": False, "weeks_ban": 0}
    if mode == "leak_repeat":
        doc = {
            "user_id": uid,
            "banned": True,
            "permanent": True,
            "weeks_ban": 999,
            "reason": reason or "Gizlilik ihlali tekrarı: Kalıcı yarışma ve grup banı.",
            "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        }
        gv_bans_col.update_one({"user_id": uid}, {"$set": doc}, upsert=True)
        return doc
    doc = {
        "user_id": uid,
        "banned": True,
        "permanent": False,
        "weeks_ban": 3,
        "reason": reason or "Seçim sızdırma cezası: 3 hafta yarışma banı.",
        "updated_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    }
    gv_bans_col.update_one({"user_id": uid}, {"$set": doc}, upsert=True)
    return doc


def list_gv_bans():
    return list(gv_bans_col.find({"banned": True}, {"_id": 0}))


# ==========================================
# MEVCUT SİSTEM & ETKİNLİK FONKSİYONLARI
# ==========================================
def init_db():
    try:
        users_col.create_index("user_id", unique=True)
        staff_col.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
        settings_col.create_index("chat_id", unique=True)
        groups_col.create_index("chat_id", unique=True)
        scheduled_jobs_col.create_index("job_id", unique=True)
        templates_col.create_index([("chat_id", 1), ("template_id", 1)], unique=True)
        events_col.create_index("event_id", unique=True)
        event_votes_col.create_index([("event_id", 1), ("user_id", 1)], unique=True)
        gv_weeks_col.create_index([("season", 1), ("week", 1)], unique=True)
        gv_entries_col.create_index([("season", 1), ("week", 1), ("user_id", 1)], unique=True)
        gv_ballots_col.create_index([("season", 1), ("week", 1), ("voter_id", 1)], unique=True)
        gv_bans_col.create_index("user_id", unique=True)
        gv_hosts_col.create_index("user_id", unique=True)
        ensure_season_initialized(get_active_season_number())
        seed_default_events()
    except Exception:
        pass


def seed_default_events():
    try:
        if events_col.count_documents({}) == 0:
            default_items = [
                {
                    "event_id": 1001,
                    "title": "GOLDENVISION",
                    "subtitle": "25 Haftalık Saray Şarkı Yarışması",
                    "icon": "👑",
                    "badge": "CANLI",
                    "description": "Ülkeni seç, şarkını sahneye taşı, 12-1 oylarıyla gecenin şampiyonunu belirle.",
                    "schedule_time": "Sezon Aktif",
                    "media_url": "",
                    "options": ["Sahneye Gir 👑"],
                    "active": True,
                    "order": 1
                },
                {
                    "event_id": 1002,
                    "title": "Mistik Tarot Gecesi",
                    "subtitle": "Kader & Aşk Kehanetleri",
                    "icon": "🔮",
                    "badge": "VIP",
                    "description": "Kartlar senin için açılıyor. Aşk, kariyer ve gecenin sürpriz enerjisini keşfet.",
                    "schedule_time": "Özel Gala • 23:00",
                    "media_url": "",
                    "options": ["Kart Çek ✨", "Sıraya Gir 🌙"],
                    "active": True,
                    "order": 2
                },
                {
                    "event_id": 1003,
                    "title": "Shade & Tea",
                    "subtitle": "Anonim İtiraf & Dedikodu Köşesi",
                    "icon": "☕",
                    "badge": "HOT",
                    "description": "Kimliğini gizle, gecenin en cesur itirafını veya dedikodusunu sahneye bırak.",
                    "schedule_time": "7/24 Aktif",
                    "media_url": "",
                    "options": ["İtiraf Bırak 💋", "Çay Dök 🔥"],
                    "active": True,
                    "order": 3
                }
            ]
            events_col.insert_many(default_items)
    except Exception:
        pass


def get_all_events(only_active=False):
    try:
        query = {"active": True} if only_active else {}
        events = list(events_col.find(query, {"_id": 0}).sort("order", 1))
        for ev in events:
            ev["votes_count"] = event_votes_col.count_documents({"event_id": ev["event_id"]})
        return events
    except Exception:
        return []


def save_event_db(data: dict):
    try:
        e_id = int(data.get("event_id")) if data.get("event_id") else int(datetime.datetime.now().timestamp() * 1000)
        options_raw = data.get("options", [])
        if isinstance(options_raw, str):
            options_list = [o.strip() for o in options_raw.split(",") if o.strip()]
        else:
            options_list = options_raw or ["Katıl ✨", "Oy Ver 👑"]

        doc = {
            "event_id": e_id,
            "title": str(data.get("title", "Yeni Etkinlik")).strip(),
            "subtitle": str(data.get("subtitle", "Özel Sahne Etkinliği")).strip(),
            "icon": str(data.get("icon", "✨")).strip(),
            "badge": str(data.get("badge", "YENİ")).strip(),
            "description": str(data.get("description", "")).strip(),
            "schedule_time": str(data.get("schedule_time", "Yakında")).strip(),
            "media_url": str(data.get("media_url", "")).strip(),
            "options": options_list,
            "active": bool(data.get("active", True)),
            "order": int(data.get("order", 10))
        }
        events_col.update_one({"event_id": e_id}, {"$set": doc}, upsert=True)
        return e_id
    except Exception:
        return None


def delete_event_db(event_id: int):
    try:
        events_col.delete_one({"event_id": int(event_id)})
        event_votes_col.delete_many({"event_id": int(event_id)})
        return True
    except Exception:
        return False


def vote_event_db(event_id: int, user_id: int, username: str, choice: str):
    try:
        event_votes_col.update_one(
            {"event_id": int(event_id), "user_id": int(user_id)},
            {"$set": {
                "event_id": int(event_id),
                "user_id": int(user_id),
                "username": username,
                "choice": choice,
                "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            }},
            upsert=True
        )
        return event_votes_col.count_documents({"event_id": int(event_id)})
    except Exception:
        return 0


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
        groups_col.update_one({"chat_id": int(chat_id)}, {"$set": update_data}, upsert=True)
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
            if res:
                return True
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
        if chat_id and int(chat_id) != 0:
            query["chat_id"] = int(chat_id)
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
        users_col.update_one({"user_id": int(user_id)}, {"$set": {"warnings": 0}}, upsert=True)
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

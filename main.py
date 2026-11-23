import os
import re
import asyncio
import logging
import hashlib
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ChatMemberHandler,
    filters
)
from telegram.request import HTTPXRequest
from telegram.error import Conflict
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database

try:
    import config
except ImportError:
    config = None

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = (os.environ.get("BOT_TOKEN") or (config and getattr(config, "BOT_TOKEN", None)) or "").strip()
FOUNDER_ID = (os.environ.get("FOUNDER_ID") or (config and getattr(config, "FOUNDER_ID", None)) or "8245373459").strip()
GV_AUDIO_DIR = os.environ.get("GV_AUDIO_DIR", os.path.join("public", "gv_audio"))

scheduler = AsyncIOScheduler()
tg_application = None

_group_meta_cache: dict[int, float] = {}
GROUP_CACHE_TTL_SEC = 300.0


def log_event(level: str, message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def _safe_audio_filename(raw_name: str) -> str:
    base = os.path.basename(raw_name or "song.mp3")
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not cleaned.lower().endswith(".mp3"):
        cleaned = f"{cleaned or 'song'}.mp3"
    return cleaned


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        return
    logger.error(f"Telegram Bot Hatası: {context.error}")


async def _refresh_group_info_if_needed(bot, chat_id: int, fallback_title: str = "Grup", force: bool = False):
    cid = int(chat_id)
    now_ts = asyncio.get_running_loop().time()
    last_ts = _group_meta_cache.get(cid, 0.0)
    if not force and (now_ts - last_ts) < GROUP_CACHE_TTL_SEC:
        return
    _group_meta_cache[cid] = now_ts
    try:
        chat_info = await bot.get_chat(cid)
        photo_url = ""
        if chat_info.photo and BOT_TOKEN:
            file_obj = await bot.get_file(chat_info.photo.small_file_id)
            photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_obj.file_path}"
        database.register_bot_group(cid, chat_info.title or fallback_title, photo_url)
    except Exception:
        database.register_bot_group(cid, fallback_title, "")


async def track_bot_group_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result:
        return
    chat = update.effective_chat
    if not chat:
        return
    new_status = result.new_chat_member.status
    if new_status in ["member", "administrator"]:
        await _refresh_group_info_if_needed(context.bot, chat.id, chat.title or "Yeni Grup", force=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_application
    os.makedirs(GV_AUDIO_DIR, exist_ok=True)
    database.init_db()
    if not scheduler.running:
        scheduler.start()

    saved_jobs = database.get_all_scheduled_jobs()
    for job in saved_jobs:
        try:
            scheduler.add_job(
                send_periodic_announcement,
                "interval",
                minutes=job["minutes"],
                args=[
                    None,
                    job["chat_id"],
                    job["msg_text"],
                    job.get("btn_text", ""),
                    job.get("btn_url", ""),
                    job.get("media_url", ""),
                ],
                id=job["job_id"],
                replace_existing=True,
            )
        except Exception as e:
            logger.error(f"Görev yüklenemedi: {e}")

    if BOT_TOKEN:
        tg_application = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .request(HTTPXRequest(connect_timeout=30.0, read_timeout=30.0))
            .build()
        )
        tg_application.add_error_handler(global_error_handler)

        for cmd, func in COMMAND_MAP.items():
            tg_application.add_handler(CommandHandler(cmd, func))

        tg_application.add_handler(ChatMemberHandler(track_bot_group_join, ChatMemberHandler.MY_CHAT_MEMBER))
        tg_application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, master_message_router))

        await tg_application.initialize()
        await tg_application.start()
        await tg_application.updater.start_polling(drop_pending_updates=True)
        log_event("SYS", "Telegram Bot ve FastAPI aktif!")

    yield

    if tg_application:
        try:
            if tg_application.updater and tg_application.updater.running:
                await tg_application.updater.stop()
        except Exception:
            pass
        try:
            if tg_application.running:
                await tg_application.stop()
        except Exception:
            pass
        try:
            await tg_application.shutdown()
        except Exception:
            pass
    if scheduler.running:
        scheduler.shutdown()
    log_event("SYS", "Sunucu kapatıldı.")


app = FastAPI(lifespan=lifespan)

if os.path.exists("public"):
    app.mount("/static", StaticFiles(directory="public"), name="static")


def _is_founder_id(user_id: int) -> bool:
    return bool(FOUNDER_ID) and str(int(user_id)) == str(FOUNDER_ID).strip()


@app.get("/health")
async def health_check():
    return HTMLResponse("GOLDEN_ENTERPRISE_FASTAPI_OK")


@app.get("/api/proxy-image")
async def proxy_image(url: str):
    try:
        if not url or not url.startswith("https://api.telegram.org/"):
            return Response(status_code=400)
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/jpeg"))
        return Response(status_code=404)
    except Exception:
        return Response(status_code=500)


@app.get("/api/stats")
async def api_get_stats():
    return database.get_bot_statistics()


@app.get("/api/logs")
async def api_get_logs(filter: str = "all", limit: int = 100):
    try:
        return database.get_recent_logs(limit=limit, log_filter=filter)
    except Exception:
        return []


@app.post("/api/logs/clear")
async def api_clear_logs(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        days = int(data.get("days", 0))
        if not database.is_authorized_staff(user_id) and not _is_founder_id(user_id):
            return {"success": False, "error": "Yetkisiz!"}
        database.clear_logs_by_retention(days)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/admin-stats")
async def api_get_admin_stats():
    return database.get_admin_performance_stats()


@app.get("/api/groups")
async def api_get_groups():
    return database.get_bot_groups()


@app.post("/api/verify-admin")
async def api_verify_admin(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    gv_access = database.can_access_gv_studio(user_id, FOUNDER_ID)
    if _is_founder_id(user_id):
        return {"authorized": True, "is_founder": True, "can_gv_studio": True}
    if database.is_authorized_staff(user_id):
        return {
            "authorized": True,
            "is_founder": False,
            "can_gv_studio": gv_access,
            "permissions": database.get_staff_permissions(user_id),
        }
    if not FOUNDER_ID:
        return {"authorized": True, "is_founder": True, "can_gv_studio": True}
    return {"authorized": False, "can_gv_studio": gv_access}


@app.get("/api/goldenvision/overview")
async def api_gv_overview(season: Optional[int] = None, user_id: int = 0):
    data = database.get_gv_overview(season=season, user_id=user_id)
    data["can_gv_studio"] = database.can_access_gv_studio(user_id, FOUNDER_ID) if user_id else False
    data["is_founder"] = _is_founder_id(user_id) if user_id else False
    return data


@app.get("/api/goldenvision/audio/{filename}")
async def api_gv_audio_stream(filename: str):
    safe_name = _safe_audio_filename(filename)
    full_path = os.path.abspath(os.path.join(GV_AUDIO_DIR, safe_name))
    base_abs = os.path.abspath(GV_AUDIO_DIR)
    if not full_path.startswith(base_abs) or not os.path.exists(full_path):
        return Response(status_code=404)
    return FileResponse(full_path, media_type="audio/mpeg", filename=safe_name)


@app.post("/api/goldenvision/studio/media")
async def api_gv_studio_media(request: Request):
    content_type = (request.headers.get("content-type") or "").lower()
    uid = 0
    s = database.get_active_season_number()
    w = 1
    entry_id = 0
    pending_id = 0
    final_doc_name = ""
    start_sec = 0
    dur_val = 20.0
    unit_norm = "sec"
    raw_bytes = b""
    upload_filename = ""

    if "application/json" in content_type:
        data = await request.json()
        uid = int(data.get("user_id", 0) or 0)
        s = int(data.get("season") or s)
        overview = database.get_gv_overview(season=s, user_id=uid)
        w = int(data.get("week") or overview["current_week"])
        entry_id = int(data.get("entry_id", 0) or 0)
        pending_id = int(data.get("pending_id", 0) or 0)
        final_doc_name = str(data.get("doc_name", "") or "").strip()
        start_sec = max(0, int(data.get("clip_start_sec", 0) or 0))
        dur_val = max(1.0, float(data.get("clip_duration_value", 20.0) or 20.0))
        unit_norm = str(data.get("clip_duration_unit", "sec") or "sec").strip().lower()
    else:
        body = await request.body()
        boundary_match = re.search(r"boundary=([^\s;]+)", content_type)
        if boundary_match:
            boundary = boundary_match.group(1).strip('"').encode("utf-8")
            fields = {}
            for part in body.split(b"--" + boundary):
                if not part or part in [b"--", b"--\r\n"]:
                    continue
                if b"\r\n\r\n" not in part:
                    continue
                head_bin, val_bin = part.split(b"\r\n\r\n", 1)
                val_bin = val_bin.rstrip(b"\r\n")
                head_txt = head_bin.decode("utf-8", errors="ignore")
                name_m = re.search(r'name="([^"]+)"', head_txt)
                if not name_m:
                    continue
                fname = name_m.group(1)
                file_m = re.search(r'filename="([^"]*)"', head_txt)
                if file_m and file_m.group(1):
                    upload_filename = file_m.group(1)
                    raw_bytes = val_bin
                else:
                    fields[fname] = val_bin.decode("utf-8", errors="ignore").strip()

            uid = int(fields.get("user_id", "0") or 0)
            s = int(fields.get("season") or s)
            overview = database.get_gv_overview(season=s, user_id=uid)
            w = int(fields.get("week") or overview["current_week"])
            entry_id = int(fields.get("entry_id", "0") or 0)
            pending_id = int(fields.get("pending_id", "0") or 0)
            final_doc_name = fields.get("doc_name", "")
            start_sec = max(0, int(float(fields.get("clip_start_sec", "0") or 0)))
            dur_val = max(1.0, float(fields.get("clip_duration_value", "20.0") or 20.0))
            unit_norm = (fields.get("clip_duration_unit", "sec") or "sec").strip().lower()

    if not database.can_access_gv_studio(uid, FOUNDER_ID):
        database.record_suspicious_ai_alert(uid, 0, "GV_MEDIA", "Yetkisiz MP3/doküman yükleme girişimi", 90)
        return {"success": False, "error": "Bu masaya erişim yetkiniz yok!"}

    dur_sec = max(1, int(round(dur_val * 60.0 if unit_norm in ["min", "dk", "dakika"] else dur_val)))
    audio_url = ""
    stored_filename = ""

    if raw_bytes and upload_filename:
        os.makedirs(GV_AUDIO_DIR, exist_ok=True)
        ts_prefix = int(datetime.now().timestamp() * 1000)
        clean_orig = _safe_audio_filename(upload_filename)
        stored_filename = f"s{s}_w{w}_{ts_prefix}_{clean_orig}"
        save_path = os.path.join(GV_AUDIO_DIR, stored_filename)
        with open(save_path, "wb") as out_f:
            out_f.write(raw_bytes)
        audio_url = f"/api/goldenvision/audio/{stored_filename}"
        if not final_doc_name:
            final_doc_name = upload_filename

    media_doc = database.save_gv_song_media(
        season=s,
        week=w,
        entry_id=entry_id,
        pending_id=pending_id,
        audio_url=audio_url,
        audio_filename=stored_filename,
        doc_name=final_doc_name,
        clip_start_sec=start_sec,
        clip_duration_sec=dur_sec,
    )
    database.log_action(
        0,
        "GV_MEDIA_SAVE",
        uid,
        "Host",
        f"Sezon {s} H{w} medya kaydı ({start_sec}sn başlangıç / {dur_sec}sn kesit).",
    )
    return {"success": True, "media": media_doc}


@app.post("/api/goldenvision/studio/clear-week-songs")
async def api_gv_studio_clear_week_songs(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not database.can_access_gv_studio(user_id, FOUNDER_ID):
        database.record_suspicious_ai_alert(user_id, 0, "GV_CLEAR_SONGS", "Yetkisiz şarkı listesi silme girişimi", 95)
        return {"success": False, "error": "Bu masaya erişim yetkiniz yok!"}

    season = int(data.get("season") or database.get_active_season_number())
    overview = database.get_gv_overview(season=season, user_id=user_id)
    week = int(data.get("week") or overview["current_week"])

    result = database.clear_gv_week_songs_and_audio(season, week)
    for fname in result.get("removed_files", []):
        if not fname:
            continue
        safe_name = _safe_audio_filename(fname)
        full_path = os.path.abspath(os.path.join(GV_AUDIO_DIR, safe_name))
        base_abs = os.path.abspath(GV_AUDIO_DIR)
        if full_path.startswith(base_abs) and os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception:
                pass

    database.log_action(
        0,
        "GV_CLEAR_SONGS",
        user_id,
        "Host",
        f"Sezon {season} H{week} şarkı ve MP3 kayıtları temizlendi ({result['deleted_entries']} şarkı).",
    )
    return {"success": True, "result": result}


@app.post("/api/goldenvision/entry")
async def api_gv_entry(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not user_id:
        return {"success": False, "error": "Kullanıcı kimliği bulunamadı."}
    season = int(data.get("season") or database.get_active_season_number())
    overview = database.get_gv_overview(season=season, user_id=user_id)
    week = int(data.get("week") or overview["current_week"])

    views_mil = database.normalize_views_to_millions(
        float(data.get("views_amount", data.get("views_m", 0)) or 0),
        str(data.get("views_unit", "M")),
    )

    res = database.submit_gv_entry(
        season=season,
        week=week,
        user_id=user_id,
        username=str(data.get("username", "")),
        full_name=str(data.get("full_name", "")),
        countries_list=data.get("countries", []),
        country=str(data.get("country", "")),
        flag=str(data.get("flag", "🏳️")),
        artist=str(data.get("artist", "")),
        song=str(data.get("song", "")),
        song_url=str(data.get("song_url", "")),
        views_m=views_mil,
        pair_partner=str(data.get("pair_partner", "")),
    )
    if not res.get("ok"):
        return {"success": False, "error": res.get("error")}
    database.log_action(
        0,
        "GV_PENDING_ENTRY",
        user_id,
        str(data.get("username", "")),
        f"Sezon {season} H{week} onay bekleyen başvuru.",
    )
    return {"success": True, "pending": res["pending"]}


@app.post("/api/goldenvision/studio/flag")
async def api_gv_studio_flag(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not database.can_access_gv_studio(user_id, FOUNDER_ID):
        return {"success": False, "error": "Bu masaya erişim yetkiniz yok!"}

    season = int(data.get("season") or database.get_active_season_number())
    overview = database.get_gv_overview(season=season, user_id=user_id)
    week = int(data.get("week") or overview["current_week"])
    entry_id = int(data.get("entry_id", 0) or 0)
    pending_id = int(data.get("pending_id", 0) or 0)
    new_flag = str(data.get("flag", "🏳️")).strip() or "🏳️"

    res = database.update_gv_entry_flag(season, week, entry_id=entry_id, pending_id=pending_id, new_flag=new_flag)
    if not res.get("ok"):
        return {"success": False, "error": res.get("error")}

    database.log_action(0, "GV_FLAG_SET", user_id, "Host", f"Bayrak güncellendi: {new_flag}")
    return {"success": True, "flag": res["flag"]}


@app.post("/api/goldenvision/studio/review")
async def api_gv_studio_review(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not database.can_access_gv_studio(user_id, FOUNDER_ID):
        database.record_suspicious_ai_alert(user_id, 0, "GV_REVIEW", "Yetkisiz başvuru onay/red girişimi", 90)
        return {"success": False, "error": "Bu masaya erişim yetkiniz yok!"}

    season = int(data.get("season") or database.get_active_season_number())
    overview = database.get_gv_overview(season=season, user_id=user_id)
    week = int(data.get("week") or overview["current_week"])
    pending_id = int(data.get("pending_id", 0))
    approve = bool(data.get("approve", False))
    reject_reason = str(data.get("reject_reason", ""))

    dur_val = max(1.0, float(data.get("clip_duration_value", data.get("clip_duration_sec", 20)) or 20))
    dur_unit = str(data.get("clip_duration_unit", "sec")).strip().lower()
    dur_sec = max(1, int(round(dur_val * 60.0 if dur_unit in ["min", "dk", "dakika"] else dur_val)))
    start_sec = max(0, int(data.get("clip_start_sec", 0) or 0))

    res = database.review_gv_pending_entry(
        season=season,
        week=week,
        pending_id=pending_id,
        approve=approve,
        reject_reason=reject_reason,
        audio_url=str(data.get("audio_url", "")),
        audio_filename=str(data.get("audio_filename", "")),
        doc_name=str(data.get("doc_name", "")),
        clip_start_sec=start_sec,
        clip_duration_sec=dur_sec,
    )
    if not res.get("ok"):
        return {"success": False, "error": res.get("error")}

    if not approve and res.get("rejected_user_id") and tg_application and tg_application.bot:
        try:
            await tg_application.bot.send_message(
                chat_id=int(res["rejected_user_id"]),
                text=f"❌ GOLDENVISION Başvuru Reddedildi\n\nSebep: {reject_reason or 'Kurallara uygun bulunmadı.'}",
            )
        except Exception:
            pass

    database.log_action(
        0,
        "GV_REVIEW",
        user_id,
        "Host",
        f"Başvuru #{pending_id} -> {'ONAYLANDI' if approve else 'REDDEDİLDİ'}",
    )
    return {"success": True, "approved": res.get("approved"), "entry": res.get("entry")}


@app.post("/api/goldenvision/studio/finale")
async def api_gv_studio_finale(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not database.can_access_gv_studio(user_id, FOUNDER_ID):
        database.record_suspicious_ai_alert(user_id, 0, "GV_FINALE", "Yetkisiz final başlatma girişimi", 90)
        return {"success": False, "error": "Bu masaya erişim yetkiniz yok!"}

    season = int(data.get("season") or database.get_active_season_number())
    overview = database.get_gv_overview(season=season, user_id=user_id)
    week = int(data.get("week") or overview["current_week"])

    database.trigger_grand_finale(season, week)
    database.log_action(
        0,
        "GV_GRAND_FINALE",
        user_id,
        "Host",
        f"Sezon {season} Hafta {week} BİTİR / ŞENLİK FİNALİ başlatıldı.",
    )
    return {"success": True}


@app.post("/api/goldenvision/ballot")
async def api_gv_ballot(request: Request):
    data = await request.json()
    voter_id = int(data.get("user_id", 0))
    if not voter_id:
        return {"success": False, "error": "Kullanıcı kimliği bulunamadı."}
    season = int(data.get("season") or database.get_active_season_number())
    overview = database.get_gv_overview(season=season, user_id=voter_id)
    week = int(data.get("week") or overview["current_week"])

    res = database.submit_gv_ballot(
        season=season,
        week=week,
        voter_id=voter_id,
        voter_username=str(data.get("username", "")),
        voter_flag=str(data.get("voter_flag", "🏳️")),
        allocations=data.get("allocations", {}),
    )
    if not res.get("ok"):
        return {"success": False, "error": res.get("error")}
    database.log_action(
        0,
        "GV_VOTE",
        voter_id,
        str(data.get("username", "")),
        f"Sezon {season} H{week} oyları mühürlendi.",
    )
    return {"success": True, "ballot": res["ballot"]}


@app.post("/api/goldenvision/comment")
async def api_gv_comment(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    season = int(data.get("season") or database.get_active_season_number())
    overview = database.get_gv_overview(season=season, user_id=user_id)
    week = int(data.get("week") or overview["current_week"])
    entry_id = int(data.get("entry_id", 0))
    text = str(data.get("text", ""))
    res = database.add_gv_comment(season, week, user_id, str(data.get("username", "")), entry_id, text)
    if not res.get("ok"):
        return {"success": False, "error": res.get("error")}
    return {"success": True, "comment": res["comment"]}


@app.post("/api/goldenvision/studio/state")
async def api_gv_studio_state(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not database.can_access_gv_studio(user_id, FOUNDER_ID):
        return {"success": False, "error": "Bu masaya erişim yetkiniz yok!"}

    season = int(data.get("season") or database.get_active_season_number())
    action = str(data.get("action", "update"))

    if action == "restart_week":
        week = int(data.get("week") or 1)
        database.restart_gv_week(season, week)
        database.log_action(0, "GV_RESTART_WEEK", user_id, "Host", f"Sezon {season} Hafta {week} baştan alındı.")
        return {"success": True, "season": season}

    if action == "next_season":
        new_s = season + 1
        database.set_active_season(new_s, reset_week_to_one=True)
        database.log_action(0, "GV_NEW_SEASON", user_id, "Host", f"Yeni sezona geçildi: Sezon {new_s}")
        return {"success": True, "season": new_s}

    if action == "jump_season":
        target_s = max(1, int(data.get("target_season") or 1))
        database.set_active_season(target_s, reset_week_to_one=False)
        database.log_action(0, "GV_JUMP_SEASON", user_id, "Host", f"Sezon {target_s} aktifleştirildi.")
        return {"success": True, "season": target_s}

    database.update_gv_state(season, data.get("updates", {}))
    return {"success": True, "season": season}


@app.post("/api/goldenvision/studio/week")
async def api_gv_studio_week(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    if not database.can_access_gv_studio(user_id, FOUNDER_ID):
        return {"success": False, "error": "Bu masaya erişim yetkiniz yok!"}
    season = int(data.get("season") or database.get_active_season_number())
    week = int(data.get("week", 1))
    database.update_gv_week(season, week, data.get("updates", {}))
    return {"success": True}


@app.post("/api/goldenvision/studio/hosts")
async def api_gv_studio_hosts(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    sub = str(data.get("sub", "list"))

    if sub == "list":
        if not database.can_access_gv_studio(user_id, FOUNDER_ID) and not database.is_authorized_staff(user_id):
            return {"success": False, "error": "Yetkisiz!"}
        return {"success": True, "hosts": database.list_gv_hosts(), "bans": database.list_gv_bans()}

    if not _is_founder_id(user_id):
        database.record_suspicious_ai_alert(user_id, 0, "GV_HOSTS_SET", "Kurucu olmayan kullanıcı host/ceza değiştirmeye çalıştı", 95)
        return {"success": False, "error": "Host yetki ataması ve ceza işlemleri sadece Kurucu (Founder) tarafından yapılabilir!"}

    if sub == "set":
        target_id = int(data.get("target_user_id", 0))
        can_manage = bool(data.get("can_manage", True))
        if not target_id:
            return {"success": False, "error": "Geçerli kullanıcı ID girin."}
        database.set_gv_host_access(target_id, can_manage, user_id)
        return {"success": True, "can_manage": can_manage, "hosts": database.list_gv_hosts()}

    if sub == "penalty":
        target_id = int(data.get("target_user_id", 0))
        mode = str(data.get("mode", "leak_first"))
        reason = str(data.get("reason", ""))
        database.apply_gv_penalty(target_id, mode, reason)
        return {"success": True, "bans": database.list_gv_bans()}

    return {"success": True, "hosts": database.list_gv_hosts(), "bans": database.list_gv_bans()}


@app.get("/api/events/list")
async def api_list_events(only_active: bool = False):
    return database.get_all_events(only_active=only_active)


@app.post("/api/events/save")
async def api_save_event(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        if not _is_founder_id(user_id) and not database.is_authorized_staff(user_id):
            return {"success": False, "error": "Yetkisiz!"}
        e_id = database.save_event_db(data)
        database.log_action(0, "EVENT_SAVE", user_id, "Admin", f"Etkinlik kaydedildi: {data.get('title', '')}")
        return {"success": True, "event_id": e_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/events/delete")
async def api_delete_event(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        event_id = int(data.get("event_id", 0))
        if not _is_founder_id(user_id) and not database.is_authorized_staff(user_id):
            return {"success": False, "error": "Yetkisiz!"}
        database.delete_event_db(event_id)
        database.log_action(0, "EVENT_DELETE", user_id, "Admin", f"Etkinlik silindi ID: {event_id}")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/events/vote")
async def api_vote_event(request: Request):
    try:
        data = await request.json()
        event_id = int(data.get("event_id", 0))
        user_id = int(data.get("user_id", 0))
        username = str(data.get("username", "Üye")).strip()
        choice = str(data.get("choice", "Katıldı")).strip()
        total_votes = database.vote_event_db(event_id, user_id, username, choice)
        database.log_action(0, "EVENT_INTERACT", user_id, f"@{username}", f"Etkinlik #{event_id} aksiyon: {choice}")
        return {"success": True, "votes_count": total_votes}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/staff/list")
async def api_get_staff_list(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    chat_id = int(data.get("chat_id", 0))
    if not database.is_authorized_staff(user_id) and not _is_founder_id(user_id):
        return {"success": False, "error": "Yetkisiz!"}
    return database.get_all_staff(chat_id)


@app.post("/api/staff/add")
async def api_add_staff(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    chat_id = int(data.get("chat_id", 0))
    input_value = str(data.get("staff_input", "")).strip()
    full_name = str(data.get("full_name", "🛡️ Muhafız")).strip() or "🛡️ Muhafız"
    if not _is_founder_id(user_id) and not database.is_authorized_staff(user_id):
        return {"success": False, "error": "Yetkisiz!"}
    if not input_value:
        return {"success": False, "error": "Boş değer"}
    clean_val = input_value.lstrip("@").lower()
    target_id = (
        int(clean_val)
        if clean_val.isdigit() or (clean_val.startswith("-") and clean_val[1:].isdigit())
        else (
            database.get_user_id_by_username(chat_id, clean_val)
            or int(hashlib.md5(clean_val.encode()).hexdigest()[:8], 16)
        )
    )
    database.add_staff_member(target_id, clean_val, full_name, user_id, chat_id)
    database.log_action(chat_id, "STAFF_ADD", user_id, clean_val, f"Yetkili eklendi: {full_name} (ID: {target_id})")
    return {"success": True, "target_id": target_id}


@app.post("/api/staff/toggle")
async def api_toggle_staff_perm(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    target_id = int(data.get("target_id", 0))
    chat_id = int(data.get("chat_id", 0))
    perm_field = str(data.get("perm", ""))
    if not _is_founder_id(user_id):
        database.record_suspicious_ai_alert(user_id, chat_id, "STAFF_TOGGLE", f"Yetkisiz yetki değiştirme girişimi ({perm_field})", 95)
        return {"success": False, "error": "Sadece Kurucu yetki değiştirebilir!"}
    if perm_field in ["can_ban", "can_mute"]:
        new_val = database.toggle_staff_permission(target_id, chat_id, perm_field)
        if new_val is not None:
            database.log_action(
                chat_id,
                "STAFF_PERM",
                user_id,
                str(target_id),
                f"{perm_field} -> {'AÇIK' if new_val else 'KAPALI'}",
            )
            return {"success": True, "new_val": bool(new_val)}
    return {"success": False, "error": "Geçersiz izin alanı."}


@app.post("/api/staff/remove")
async def api_remove_staff(request: Request):
    data = await request.json()
    user_id = int(data.get("user_id", 0))
    target_id = int(data.get("target_id", 0))
    chat_id = int(data.get("chat_id", 0))
    if not _is_founder_id(user_id):
        return {"success": False, "error": "Yetkisiz!"}
    database.remove_staff_member(target_id, chat_id)
    database.log_action(chat_id, "STAFF_REMOVE", user_id, str(target_id), "Yetkili silindi.")
    return {"success": True}


@app.post("/api/member/action")
async def api_member_action(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        target_input = str(data.get("target", "")).strip()
        action = str(data.get("action", "")).strip()
        chat_id = int(data.get("chat_id", 0))

        founder = _is_founder_id(user_id)
        if not founder and not database.is_authorized_staff(user_id, chat_id):
            database.record_suspicious_ai_alert(user_id, chat_id, action, "Yetkisiz üye işlemi girişimi", 88)
            return {"success": False, "error": "⛔ Yetkisiz erişim!"}

        if not founder:
            perms = database.get_staff_permissions(user_id, chat_id)
            if action in ["ban", "unban"] and not perms.get("can_ban", True):
                database.record_suspicious_ai_alert(user_id, chat_id, action, "Kapalı olan BAN yetkisi ile işlem denemesi", 90)
                return {"success": False, "error": "❌ Ban yetkiniz kapalı!"}
            if action in ["mute", "unmute"] and not perms.get("can_mute", True):
                database.record_suspicious_ai_alert(user_id, chat_id, action, "Kapalı olan MUTE yetkisi ile işlem denemesi", 90)
                return {"success": False, "error": "❌ Mute yetkiniz kapalı!"}

        if not tg_application or not tg_application.bot:
            return {"success": False, "error": "Bot aktif değil!"}

        bot = tg_application.bot
        clean_target = target_input.lstrip("@").lower()
        target_id = (
            int(clean_target)
            if clean_target.isdigit() or (clean_target.startswith("-") and clean_target[1:].isdigit())
            else database.get_user_id_by_username(chat_id, clean_target)
        )
        if not target_id and chat_id == 0:
            for g in database.get_bot_groups():
                uid = database.get_user_id_by_username(g["chat_id"], clean_target)
                if uid:
                    target_id = uid
                    break

        if not target_id:
            return {"success": False, "error": "Kullanıcı bulunamadı!"}

        target_chats = [g["chat_id"] for g in database.get_bot_groups()] if chat_id == 0 else [chat_id]
        for cid in target_chats:
            try:
                if action == "ban":
                    await bot.ban_chat_member(chat_id=cid, user_id=target_id)
                    database.log_action(cid, "WEB_BAN", user_id, clean_target, f"Banlandı (ID: {target_id})")
                elif action == "unban":
                    await bot.unban_chat_member(chat_id=cid, user_id=target_id, only_if_banned=True)
                    database.log_action(cid, "WEB_UNBAN", user_id, clean_target, f"Banı kaldırıldı (ID: {target_id})")
                elif action == "mute":
                    await bot.restrict_chat_member(chat_id=cid, user_id=target_id, permissions=ChatPermissions(can_send_messages=False))
                    database.log_action(cid, "WEB_MUTE", user_id, clean_target, f"Susturuldu (ID: {target_id})")
                elif action == "unmute":
                    perms_chat = ChatPermissions(
                        can_send_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    )
                    await bot.restrict_chat_member(chat_id=cid, user_id=target_id, permissions=perms_chat)
                    database.log_action(cid, "WEB_UNMUTE", user_id, clean_target, f"Susturması kaldırıldı (ID: {target_id})")
            except Exception as sub_e:
                logger.error(f"Grup {cid} hata: {sub_e}")

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/settings/update")
async def api_update_settings(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        chat_id = int(data.get("chat_id", 0))
        if not database.is_authorized_staff(user_id) and not _is_founder_id(user_id):
            return {"success": False, "error": "Yetkisiz!"}
        if chat_id:
            for key in [
                "welcome_msg",
                "btn_text",
                "btn_url",
                "media_url",
                "announcement_btn_text",
                "announcement_btn_url",
                "scheduled_btn_text",
                "scheduled_btn_url",
            ]:
                if key in data:
                    database.update_group_setting(chat_id, key, data[key])
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


class TemplateData(BaseModel):
    user_id: int
    chat_id: int
    name: str
    msg: str
    media: Optional[str] = ""
    btn_text: Optional[str] = ""
    btn_url: Optional[str] = ""
    mins: Optional[int] = 0
    template_id: Optional[int] = None


@app.post("/api/templates/list")
async def api_list_templates(request: Request):
    try:
        data = await request.json()
        chat_id = int(data.get("chat_id", 0))
        return database.get_group_templates(chat_id)
    except Exception:
        return {"announcements": [], "schedules": []}


@app.post("/api/templates/save-announcement")
async def api_save_announcement(data: TemplateData):
    try:
        founder = _is_founder_id(data.user_id)
        if not database.is_authorized_staff(data.user_id) and not founder:
            return {"success": False, "error": "Yetkisiz!"}
        database.save_template_db(
            data.chat_id,
            "announcement",
            data.name,
            data.msg,
            data.media,
            data.btn_text,
            data.btn_url,
            0,
            data.template_id,
        )
        database.log_action(data.chat_id, "SAVE_ANN_TEMPLATE", data.user_id, "Admin", f"Duyuru şablonu: {data.name}")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/templates/save-schedule")
async def api_save_schedule(data: TemplateData):
    try:
        founder = _is_founder_id(data.user_id)
        if not database.is_authorized_staff(data.user_id) and not founder:
            return {"success": False, "error": "Yetkisiz!"}
        database.save_template_db(
            data.chat_id,
            "schedule",
            data.name,
            data.msg,
            data.media,
            data.btn_text,
            data.btn_url,
            data.mins,
            data.template_id,
        )
        database.log_action(data.chat_id, "SAVE_SCH_TEMPLATE", data.user_id, "Admin", f"Zamanlama şablonu: {data.name}")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/templates/delete")
async def api_delete_template(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        chat_id = int(data.get("chat_id", 0))
        template_id = data.get("template_id")
        founder = _is_founder_id(user_id)
        if not database.is_authorized_staff(user_id) and not founder:
            return {"success": False, "error": "Yetkisiz!"}
        database.database_delete_template_db(chat_id, template_id)
        database.log_action(data.chat_id, "DELETE_TEMPLATE", user_id, "Admin", f"Şablon silindi ID: {template_id}")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _serve_html(filename: str):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    paths_to_check = [os.path.join(base_dir, "public", filename), os.path.join(base_dir, filename)]
    for path in paths_to_check:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return HTMLResponse(
                        content=f.read(),
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                    )
            except Exception as e:
                logger.error(f"HTML hata ({filename}): {e}")
    return HTMLResponse(content=f"{filename} bulunamadı!", status_code=500)


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def handle_root():
    return _serve_html("index.html")


@app.api_route("/member", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def handle_member_portal():
    return _serve_html("member.html")


async def _delete_after_delay(context, chat_id, message_id, delay=5):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def send_auto_delete_message(context, chat_id, text, delay=5):
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        asyncio.create_task(_delete_after_delay(context, chat_id, msg.message_id, delay))
    except Exception as e:
        logger.error(f"Mesaj hata: {e}")


async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, action_type="general") -> bool:
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user:
        return False
    if _is_founder_id(user.id):
        return True

    if action_type == "ban" and database.is_perm_explicitly_disabled_in_staff(user.id, chat_id, "can_ban"):
        database.record_suspicious_ai_alert(user.id, chat_id, "BOT_BAN", "Kapalı olan BAN yetkisi ile komut denemesi", 90)
        await send_auto_delete_message(context, chat_id, "❌ Ban yetkiniz kapalı!", 4)
        return False

    if action_type == "mute" and database.is_perm_explicitly_disabled_in_staff(user.id, chat_id, "can_mute"):
        database.record_suspicious_ai_alert(user.id, chat_id, "BOT_MUTE", "Kapalı olan MUTE yetkisi ile komut denemesi", 90)
        await send_auto_delete_message(context, chat_id, "❌ Mute yetkiniz kapalı!", 4)
        return False

    if database.is_authorized_staff(user.id, chat_id):
        perms = database.get_staff_permissions(user.id, chat_id)
        if action_type == "ban" and not perms.get("can_ban", True):
            await send_auto_delete_message(context, chat_id, "❌ Ban yetkiniz kapalı!", 4)
            return False
        if action_type == "mute" and not perms.get("can_mute", True):
            await send_auto_delete_message(context, chat_id, "❌ Mute yetkiniz kapalı!", 4)
            return False
        return True

    if update.effective_chat.type in ["group", "supergroup"]:
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            if member.status in ["administrator", "creator"]:
                return True
        except Exception:
            pass
    else:
        return True

    await send_auto_delete_message(context, chat_id, "❌ Yetkiniz yok!", 4)
    return False


async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        if target and target.username:
            database.save_user(chat_id, target.id, target.username.lower())
        return target
    if context.args:
        clean_arg = context.args[0].strip().lstrip("@").lower()
        if clean_arg.isdigit() or (clean_arg.startswith("-") and clean_arg[1:].isdigit()):
            try:
                member = await context.bot.get_chat_member(chat_id, int(clean_arg))
                return member.user
            except Exception:
                class TempUser:
                    def __init__(self, uid):
                        self.id, self.full_name, self.username = uid, f"Kullanıcı_{uid}", f"id_{uid}"

                return TempUser(int(clean_arg))
        else:
            db_uid = database.get_user_id_by_username(chat_id, clean_arg) or database.get_user_id_by_username(0, clean_arg)
            if db_uid:
                try:
                    return (await context.bot.get_chat_member(chat_id, db_uid)).user
                except Exception:
                    pass

            class UsernameTarget:
                def __init__(self, uname):
                    self.id, self.full_name, self.username = f"@{uname}", f"@{uname}", uname

            return UsernameTarget(clean_arg)
    return None


async def send_periodic_announcement(bot, chat_id, text, btn_text, btn_url, media_url):
    try:
        current_bot = bot or (tg_application and tg_application.bot)
        if not current_bot:
            return
        reply_markup = None
        if not btn_text or not btn_url:
            sched_settings = database.get_announcement_settings(chat_id)
            if not btn_text:
                btn_text = sched_settings.get("scheduled_btn_text", "")
            if not btn_url:
                btn_url = sched_settings.get("scheduled_btn_url", "")

        if btn_text and btn_url:
            clean_url = str(btn_url).strip()
            if " " not in clean_url and "\n" not in clean_url:
                if not clean_url.startswith(("http://", "https://", "tg://")):
                    clean_url = "https://" + clean_url
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text=btn_text, url=clean_url)]])

        if media_url:
            await current_bot.send_photo(chat_id=chat_id, photo=media_url, caption=text, reply_markup=reply_markup)
        else:
            await current_bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Zamanlı duyuru hata: {e}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        await _refresh_group_info_if_needed(context.bot, chat.id, chat.title or "Grup", force=True)
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    welcome_text = (
        "🤖 Grup Yönetim & Golden Staff Güvenlik Botu\n\n"
        "📜 Yönetici Komutları:\n"
        "- /welcome <mesaj> - Hoş geldin mesajı\n"
        "- /butonmetni <yazı> - Hoş geldin butonu yazısı\n"
        "- /butonlinki <url> - Hoş geldin butonu linki\n"
        "- /duyuru <mesaj> - Duyuru gönder (Otomatik Pinlenir)\n"
        "- /duyuru <mesaj> | <buton metni> | <link> - Özel butonlu duyuru\n"
        "- /duyurubutonmetni <yazı> - Varsayılan duyuru butonu yazısı\n"
        "- /duyurubutonlinki <url> - Varsayılan duyuru butonu linki\n"
        "- /zamanla <dakika> <mesaj> - Periyodik duyuru başlat\n"
        "- /zamanla <dakika> | <mesaj> | <buton metni> | <link> - Butonlu periyodik duyuru\n"
        "- /zamanladur veya /zamanla dur - Zamanlanmış paylaşımı durdur\n"
        "- /defol <kullanıcı> - Banla\n"
        "- /undefol <kullanıcı> - Ban kaldır\n"
        "- /itaat <kullanıcı> - Sustur\n"
        "- /unitaat <kullanıcı> - Susturma kaldır\n"
        "- /uyari <kullanıcı> - Uyarı ver (3 uyarıda otomatik banlar)\n"
        "- /unuyari <kullanıcı> - Uyarı sıfırla\n\n"
        "⚜️ Powered by 𝘽𝙍𝙀𝙎𝙆𝘼𝙑𝙄𝘾𝘼 🇷🇸"
    )
    base_url = os.environ.get("WEBAPP_URL", "https://golden-guardian-bot.onrender.com").rstrip("/")
    keyboard = [
        [InlineKeyboardButton(text="🛡️ Paneli Aç", web_app=WebAppInfo(url=f"{base_url}/"))],
        [InlineKeyboardButton(text="✨ Golden Üye Girişi", web_app=WebAppInfo(url=f"{base_url}/member"))],
    ]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        return
    text = " ".join(context.args)
    if text:
        database.update_group_setting(update.effective_chat.id, "welcome_msg", text)
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Hoş geldin mesajı güncellendi!", 4)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_butonmetni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        return
    text = " ".join(context.args)
    if text:
        database.update_group_setting(update.effective_chat.id, "btn_text", text)
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Hoş geldin buton metni güncellendi!", 4)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_butonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        return
    if context.args:
        database.update_group_setting(update.effective_chat.id, "btn_url", context.args[0].strip())
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Hoş geldin buton linki güncellendi!", 4)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_duyurubutonmetni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        return
    text = " ".join(context.args)
    if text:
        database.update_group_setting(update.effective_chat.id, "announcement_btn_text", text)
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Duyuru buton metni güncellendi!", 4)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_duyurubutonlinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        return
    if context.args:
        database.update_group_setting(update.effective_chat.id, "announcement_btn_url", context.args[0].strip())
        await send_auto_delete_message(context, update.effective_chat.id, "✅ Duyuru buton linki güncellendi!", 4)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "general"):
        return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    raw_text = update.message.text.partition(" ")[2].strip()

    photo_file_id = None
    if reply:
        if reply.photo:
            photo_file_id = reply.photo[-1].file_id
        if not raw_text:
            raw_text = reply.caption or reply.text or ""

    if not raw_text and not photo_file_id:
        await send_auto_delete_message(context, chat_id, "❌ Duyuru metni veya görsel belirtmelisiniz!", 4)
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    def_settings = database.get_announcement_settings(chat_id)
    btn_text = def_settings.get("announcement_btn_text", def_settings.get("btn_text", ""))
    btn_url = def_settings.get("announcement_btn_url", def_settings.get("btn_url", ""))
    msg_text = raw_text

    if "|" in raw_text:
        parts = [p.strip() for p in raw_text.split("|")]
        msg_text = parts[0]
        if len(parts) > 1 and parts[1]:
            btn_text = parts[1]
        if len(parts) > 2 and parts[2]:
            btn_url = parts[2]

    reply_markup = None
    if btn_text and btn_url:
        clean_url = str(btn_url).strip()
        if not clean_url.startswith(("http://", "https://", "tg://")):
            clean_url = "https://" + clean_url
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text=btn_text, url=clean_url)]])

    try:
        if photo_file_id:
            sent_msg = await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=msg_text, reply_markup=reply_markup)
        else:
            sent_msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=reply_markup)

        await context.bot.pin_chat_message(chat_id=chat_id, message_id=sent_msg.message_id, disable_notification=False)
        admin_user = update.effective_user
        database.log_action(
            chat_id,
            "ANNOUNCEMENT",
            admin_user.id,
            f"@{admin_user.username or admin_user.first_name}",
            f"Duyuru: {msg_text[:30]}...",
        )

        auto_name = f"Chat Duyurusu: {msg_text[:20]}..." if msg_text else "Chat Duyurusu"
        database.save_template_db(chat_id, "announcement", auto_name, msg_text, photo_file_id or "", btn_text, btn_url, 0)
    except Exception as e:
        logger.error(f"Duyuru hata: {e}")
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_zamanladur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "general"):
        return
    chat_id = update.effective_chat.id
    job_to_remove = database.get_latest_scheduled_job(chat_id)
    if job_to_remove:
        job_id = job_to_remove["job_id"]
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        database.remove_scheduled_job_db(job_id)
        admin_user = update.effective_user
        database.log_action(
            chat_id,
            "SCHEDULE_STOP",
            admin_user.id,
            f"@{admin_user.username or admin_user.first_name}",
            "Periyodik duyuru durduruldu.",
        )
        await send_auto_delete_message(context, chat_id, "🛑 Periyodik duyuru durduruldu!", 5)
    else:
        await send_auto_delete_message(context, chat_id, "❌ Aktif periyodik duyuru bulunamadı!", 5)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_zamanla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "general"):
        return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    raw_args = update.message.text.partition(" ")[2].strip()

    if raw_args.lower().startswith("dur"):
        return await cmd_zamanladur(update, context)

    media_url = ""
    if reply and reply.photo:
        media_url = reply.photo[-1].file_id

    btn_text, btn_url, minutes, msg_text = "", "", 0, ""
    if "|" in raw_args:
        parts = [p.strip() for p in raw_args.split("|")]
        try:
            minutes = int(parts[0])
        except ValueError:
            await send_auto_delete_message(context, chat_id, "❌ Dakika sayısal olmalıdır!", 5)
            try:
                await update.message.delete()
            except Exception:
                pass
            return
        msg_text = parts[1] if len(parts) > 1 else ""
        btn_text = parts[2] if len(parts) > 2 else ""
        btn_url = parts[3] if len(parts) > 3 else ""
    else:
        parts = raw_args.split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            await send_auto_delete_message(context, chat_id, "❌ Format: /zamanla <dakika> <mesaj>", 6)
            try:
                await update.message.delete()
            except Exception:
                pass
            return
        minutes = int(parts[0])
        msg_text = parts[1] if len(parts) > 1 else ""

    if not msg_text and reply:
        msg_text = reply.caption or reply.text or ""

    if not btn_text or not btn_url:
        sched_settings = database.get_announcement_settings(chat_id)
        if not btn_text:
            btn_text = sched_settings.get("scheduled_btn_text", "")
        if not btn_url:
            btn_url = sched_settings.get("scheduled_btn_url", "")

    job_id = hashlib.md5(f"{chat_id}_{minutes}_{msg_text[:20]}".encode()).hexdigest()
    scheduler.add_job(
        send_periodic_announcement,
        "interval",
        minutes=minutes,
        args=[context.bot, chat_id, msg_text, btn_text, btn_url, media_url],
        id=job_id,
        replace_existing=True,
    )

    database.save_scheduled_job(job_id, chat_id, minutes, msg_text, btn_text, btn_url, media_url)
    auto_name = f"Chat Zamanlı ({minutes}dk): {msg_text[:15]}..." if msg_text else f"Periyodik {minutes}dk"
    database.save_template_db(chat_id, "schedule", auto_name, msg_text, media_url, btn_text, btn_url, minutes)

    admin_user = update.effective_user
    database.log_action(
        chat_id,
        "SCHEDULE_START",
        admin_user.id,
        f"@{admin_user.username or admin_user.first_name}",
        f"Her {minutes} dk periyodik başlatıldı.",
    )
    await send_auto_delete_message(context, chat_id, f"✅ Her {minutes} dakikada bir paylaşılacak görev başlatıldı!", 5)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_defol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "ban"):
        return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            uname = getattr(user, "username", str(user.id))
            admin_user = update.effective_user
            database.log_action(
                chat_id,
                "BAN",
                admin_user.id,
                f"@{admin_user.username or admin_user.first_name}",
                f"@{uname} banlandı.",
            )
            await send_auto_delete_message(context, chat_id, f"🚫 @{uname} gruptan banlandı!", 5)
        except Exception as e:
            logger.error(f"Ban hata: {e}")
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_undefol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "ban"):
        return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
            uname = getattr(user, "username", str(user.id))
            admin_user = update.effective_user
            database.log_action(
                chat_id,
                "UNBAN",
                admin_user.id,
                f"@{admin_user.username or admin_user.first_name}",
                f"@{uname} banı kaldırıldı.",
            )
            await send_auto_delete_message(context, chat_id, f"✅ @{uname} banı kaldırıldı!", 5)
        except Exception as e:
            logger.error(f"Unban hata: {e}")
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_itaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "mute"):
        return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False))
            uname = getattr(user, "username", str(user.id))
            admin_user = update.effective_user
            database.log_action(
                chat_id,
                "MUTE",
                admin_user.id,
                f"@{admin_user.username or admin_user.first_name}",
                f"@{uname} susturuldu.",
            )
            await send_auto_delete_message(context, chat_id, f"🔇 @{uname} susturuldu!", 5)
        except Exception as e:
            logger.error(f"Mute hata: {e}")
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_unitaat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "mute"):
        return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        try:
            perms = ChatPermissions(
                can_send_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
            await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user.id, permissions=perms)
            uname = getattr(user, "username", str(user.id))
            admin_user = update.effective_user
            database.log_action(
                chat_id,
                "UNMUTE",
                admin_user.id,
                f"@{admin_user.username or admin_user.first_name}",
                f"@{uname} susturması kaldırıldı.",
            )
            await send_auto_delete_message(context, chat_id, f"🔊 @{uname} susturması kaldırıldı!", 5)
        except Exception as e:
            logger.error(f"Unmute hata: {e}")
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_uyari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "ban"):
        return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        uname = getattr(user, "username", str(user.id))
        admin_user = update.effective_user
        current_warns = database.add_warning(user.id)
        if current_warns >= 3:
            try:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                database.reset_warnings(user.id)
                database.log_action(
                    chat_id,
                    "AUTO_BAN_WARN",
                    admin_user.id,
                    f"@{admin_user.username or admin_user.first_name}",
                    f"@{uname} 3 uyarı ile banlandı.",
                )
                await send_auto_delete_message(context, chat_id, f"🚫 @{uname} 3 uyarıyı aştığı için otomatik banlandı!", 6)
            except Exception as e:
                logger.error(f"Otomatik ban: {e}")
        else:
            database.log_action(
                chat_id,
                "WARN",
                admin_user.id,
                f"@{admin_user.username or admin_user.first_name}",
                f"@{uname} uyarıldı ({current_warns}/3).",
            )
            await send_auto_delete_message(context, chat_id, f"⚠️ @{uname} uyarıldı! ({current_warns}/3)", 5)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_unuyari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context, "ban"):
        return
    chat_id = update.effective_chat.id
    user = await get_target_user(update, context)
    if user:
        uname = getattr(user, "username", str(user.id))
        admin_user = update.effective_user
        database.reset_warnings(user.id)
        database.log_action(
            chat_id,
            "UNWARN",
            admin_user.id,
            f"@{admin_user.username or admin_user.first_name}",
            f"@{uname} uyarıları sıfırlandı.",
        )
        await send_auto_delete_message(context, chat_id, f"✅ @{uname} uyarıları sıfırlandı!", 5)
    try:
        await update.message.delete()
    except Exception:
        pass


COMMAND_MAP = {
    "start": cmd_start,
    "help": cmd_start,
    "welcome": cmd_welcome,
    "butonmetni": cmd_butonmetni,
    "butonlinki": cmd_butonlinki,
    "duyurubutonmetni": cmd_duyurubutonmetni,
    "duyurubutonlinki": cmd_duyurubutonlinki,
    "duyuru": cmd_duyuru,
    "zamanla": cmd_zamanla,
    "zamanladur": cmd_zamanladur,
    "defol": cmd_defol,
    "undefol": cmd_undefol,
    "itaat": cmd_itaat,
    "unitaat": cmd_unitaat,
    "uyari": cmd_uyari,
    "warn": cmd_uyari,
    "unuyari": cmd_unuyari,
    "unwarn": cmd_unuyari,
}


async def master_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user, chat, chat_id = update.effective_user, update.effective_chat, update.effective_chat.id
    if chat and chat.type in ["group", "supergroup"]:
        await _refresh_group_info_if_needed(context.bot, chat_id, chat.title or "Grup", force=False)

    if user and user.username:
        database.save_user(chat_id, user.id, user.username.lower())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

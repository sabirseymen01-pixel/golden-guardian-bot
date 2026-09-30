"""Giriş noktası: Telegram botu (polling) + Render için health check sunucusu."""
import asyncio
import logging
import signal

import aiohttp
from aiohttp import web
from telegram import Update
from telegram.ext import Application, ContextTypes

import config
import database as db
from plugins import admin, fed, start

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("fedbot")


# ---------- Health check ----------
async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def start_web() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("Health check sunucusu :%s portunda", config.PORT)
    return runner


async def keep_alive() -> None:
    """Render ücretsiz planda uyumayı önlemek için kendi /health adresine ping atar."""
    if not config.RENDER_EXTERNAL_URL:
        return
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            await asyncio.sleep(600)
            try:
                async with session.get(f"{config.RENDER_EXTERNAL_URL}/health") as r:
                    log.info("keep-alive: %s", r.status)
            except Exception as e:  # noqa: BLE001
                log.warning("keep-alive hatası: %s", e)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Beklenmeyen hata", exc_info=ctx.error)


# ---------- Ana döngü ----------
async def run() -> None:
    await db.init()
    runner = await start_web()

    application = Application.builder().token(config.BOT_TOKEN).build()
    for plugin in (start, admin, fed):
        plugin.register(application)
    application.add_error_handler(on_error)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES, drop_pending_updates=True
    )
    ping_task = asyncio.create_task(keep_alive())
    log.info("Bot çalışıyor.")

    await stop.wait()

    ping_task.cancel()
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass



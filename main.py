import asyncio
import logging
import uvicorn
from config import API_HOST, API_PORT
from database import init_db
from api_server import app as fastapi_app
from bot import create_bot_app, bakong_deposit_verifier_loop, supplier_balance_monitor_loop

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_server_and_bot():
    logger.info("Initializing database...")
    await init_db()
    
    # Create Telegram Bot application
    logger.info("Building Telegram Bot application...")
    bot_app = create_bot_app()
    
    # Retry loop for initializing Telegram Bot connection
    for attempt in range(1, 11):
        try:
            logger.info(f"Connecting to Telegram API (Attempt {attempt}/10)...")
            await bot_app.initialize()
            await bot_app.start()
            logger.info("Telegram Bot connected successfully!")
            break
        except Exception as e:
            logger.warning(f"Telegram connection attempt {attempt} failed ({e}). Retrying in 3 seconds...")
            if attempt == 10:
                raise e
            await asyncio.sleep(3)
    
    # Start Telegram Bot Polling
    logger.info("Starting Telegram Bot updater polling...")
    await bot_app.updater.start_polling(drop_pending_updates=True)
    
    # Start background tasks for Bakong deposit verifier and Supplier Low Balance Monitor
    logger.info("Starting Bakong KHQR deposit auto-verifier loop...")
    asyncio.create_task(bakong_deposit_verifier_loop(bot_app))
    
    logger.info("Starting Supplier Low-Balance Auto-Monitor loop (< $20 alert)...")
    asyncio.create_task(supplier_balance_monitor_loop(bot_app))
    
    # Configure and start FastAPI Uvicorn Server concurrently
    logger.info(f"Starting FastAPI REST server on http://{API_HOST}:{API_PORT}...")
    config = uvicorn.Config(app=fastapi_app, host=API_HOST, port=API_PORT, log_level="info")
    server = uvicorn.Server(config)
    
    try:
        await server.serve()
    finally:
        logger.info("Stopping Telegram Bot...")
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()

def main():
    try:
        asyncio.run(run_server_and_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application stopped gracefully.")

if __name__ == "__main__":
    main()

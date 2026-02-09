import os
import logging
import threading
import asyncio
import time
import sys
from flask import Flask
from telegram import Update
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
PORT = int(os.environ.get('PORT', 5000))
COOKIES_FILE = 'cookies.txt' 

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- FLASK KEEP-ALIVE SERVER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    try:
        # Use a simpler server for production stability
        app.run(host='0.0.0.0', port=PORT, use_reloader=False, threaded=True)
    except Exception as e:
        logger.error(f"Flask server error: {e}")

# --- YOUTUBE DOWNLOAD LOGIC ---
async def download_video(url: str, chat_id: int, status_msg):
    filename = f"video_{chat_id}_{int(time.time())}.mp4"
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': filename,
        'quiet': True,
        'noplaylist': True,
        'max_filesize': 50 * 1024 * 1024,
        'merge_output_format': 'mp4',
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        logger.info("Using cookies.txt for authentication")

    try:
        def run_ydl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                return ydl.extract_info(url, download=False)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_ydl)
        
        actual_file = None
        for f in os.listdir('.'):
            if f.startswith(f"video_{chat_id}"):
                actual_file = f
                break

        if not actual_file or not os.path.exists(actual_file):
             return None, "Download failed (File not found)."
             
        if os.path.getsize(actual_file) > 49 * 1024 * 1024:
            os.remove(actual_file)
            return None, "File too large for Telegram (Max 50MB)."
        
        return actual_file, None
    except Exception as e:
        for f in os.listdir('.'):
            if f.startswith(f"video_{chat_id}"):
                os.remove(f)
        return None, str(e)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send me a YouTube link!")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.warning("Conflict detected. Waiting for other instance to exit...")
        time.sleep(5)
    else:
        logger.error(f"Exception while handling an update: {context.error}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    chat_id = update.effective_chat.id

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Please send a valid YouTube link.")
        return

    status_msg = await update.message.reply_text("🔎 Processing YouTube link...")

    try:
        video_path, error = await download_video(url, chat_id, status_msg)

        if error:
            await status_msg.edit_text(f"❌ Error: {error}")
            return

        await status_msg.edit_text("⬆️ Uploading...")
        
        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(chat_id=chat_id, video=video_file, supports_streaming=True)
        
        await status_msg.delete()
        os.remove(video_path)

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text("❌ Download error.")
        if 'video_path' in locals() and os.path.exists(video_path):
            os.remove(video_path)

# --- MAIN EXECUTION ---
async def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is missing!")
        sys.exit(1)

    # 1. Start Flask in background thread
    threading.Thread(target=run_flask, daemon=True).start()

    # 2. Setup Application
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.add_error_handler(error_handler)

    # 3. Aggressive Webhook Cleanup
    logger.info("Resetting Telegram connection...")
    await application.bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(2) # Brief pause

    # 4. Start Polling
    logger.info("Bot starting...")
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        # Keep the loop running
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.fatal(f"Fatal error: {e}")

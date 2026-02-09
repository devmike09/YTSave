import os
import logging
import threading
import asyncio
import time
from flask import Flask
from telegram import Update
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
        app.run(host='0.0.0.0', port=PORT, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask server error: {e}")

# --- YOUTUBE DOWNLOAD LOGIC ---
async def download_video(url: str, chat_id: int, status_msg):
    # Using a unique filename per request to avoid collision
    filename = f"video_{chat_id}_{int(time.time())}.mp4"
    
    ydl_opts = {
        # 'best' is more reliable than 'best[ext=mp4]' for restricted videos
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
                # Use download=True directly
                ydl.download([url])
                return ydl.extract_info(url, download=False)

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, run_ydl)
        
        # Check for the filename (yt-dlp might have appended .mp4 or similar)
        # We look for the file that starts with our base filename
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
        # Cleanup any partial files
        for f in os.listdir('.'):
            if f.startswith(f"video_{chat_id}"):
                os.remove(f)
        return None, str(e)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send me a YouTube link and I will download it for you!")

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

        await status_msg.edit_text("⬆️ Uploading video to Telegram...")
        
        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=chat_id, 
                video=video_file,
                supports_streaming=True
            )
        
        await status_msg.delete()
        os.remove(video_path)

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text("❌ An error occurred during processing.")
        if 'video_path' in locals() and os.path.exists(video_path):
            os.remove(video_path)

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is missing!")
        exit(1)

    # 1. Start Flask (Keep-Alive) in background
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Startup Delay to let Render clear old instances
    time.sleep(10)

    # 3. Build Application
    application = ApplicationBuilder().token(TOKEN).build()
    
    # 4. Explicitly kick off other instances by deleting webhook
    async def clear_proxy():
        await application.bot.delete_webhook(drop_pending_updates=True)
        print("Webhook cleared, starting polling...")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(clear_proxy())

    # 5. Setup Handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # 6. Run Polling
    application.run_polling(drop_pending_updates=True)

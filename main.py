import os
import logging
import threading
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
PORT = int(os.environ.get('PORT', 5000))
COOKIES_FILE = 'cookies.txt' # The file you upload to GitHub

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
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)

# --- YOUTUBE DOWNLOAD LOGIC ---
async def download_video(url: str, chat_id: int, status_msg):
    filename = f"video_{chat_id}.mp4"
    
    ydl_opts = {
        'format': 'best[ext=mp4]/best', 
        'outtmpl': filename,
        'quiet': True,
        'noplaylist': True,
        'max_filesize': 50 * 1024 * 1024,
    }

    # Add cookies if the file exists
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        logger.info("Using cookies.txt for authentication")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get('duration', 0)
            
            if duration > 900: 
                return None, "Video is too long (limit 15 mins)."

            await status_msg.edit_text(f"⬇️ Downloading: {info.get('title', 'Video')}...")
            ydl.download([url])
            
            if not os.path.exists(filename):
                 return None, "Download failed (File not found)."
                 
            if os.path.getsize(filename) > 49 * 1024 * 1024:
                os.remove(filename)
                return None, "File too large for Telegram (Max 50MB)."
            
            return filename, None
    except Exception as e:
        if os.path.exists(filename):
            os.remove(filename)
        return None, str(e)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send me a YouTube link!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    chat_id = update.effective_chat.id

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Please send a valid YouTube link.")
        return

    status_msg = await update.message.reply_text("🔎 Processing...")

    try:
        video_path, error = await download_video(url, chat_id, status_msg)

        if error:
            await status_msg.edit_text(f"❌ Error: {error}")
            return

        await status_msg.edit_text("⬆️ Uploading...")
        
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
        await status_msg.edit_text("❌ An internal error occurred.")
        if 'video_path' in locals() and os.path.exists(video_path):
            os.remove(video_path)

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not TOKEN:
        exit(1)

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    application.run_polling()

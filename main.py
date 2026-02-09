import os
import logging
import asyncio
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
PORT = int(os.environ.get('PORT', 5000))

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- FLASK KEEP-ALIVE SERVER ---
# Render requires a web service to bind to a port. 
# We run a dummy Flask app in a separate thread to satisfy this.
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# --- YOUTUBE DOWNLOAD LOGIC ---
async def download_video(url: str, chat_id: int, status_msg):
    """
    Downloads the video using yt-dlp and returns the file path.
    """
    filename = f"video_{chat_id}.mp4"
    
    # Options for yt-dlp
    ydl_opts = {
        'format': 'best[ext=mp4]/best', # Best quality MP4
        'outtmpl': filename,            # Output filename
        'quiet': True,
        'max_filesize': 50 * 1024 * 1024, # 50MB limit (Telegram Bot API limit)
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Check duration (optional safeguard, e.g., skip videos > 10 mins)
            if info.get('duration') > 600: 
                return None, "Video is too long (limit 10 mins)."

            await status_msg.edit_text(f"⬇️ Downloading: {info.get('title', 'Video')}...")
            ydl.download([url])
            
            if os.path.getsize(filename) > 50 * 1024 * 1024:
                os.remove(filename)
                return None, "File too large for Telegram (Max 50MB)."
            
            return filename, None
    except Exception as e:
        return None, str(e)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! Send me a YouTube link, and I'll try to download it for you.\n\n"
        "⚠️ *Note:* Telegram bots have a 50MB upload limit. Longer videos may fail."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    chat_id = update.effective_chat.id

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Please send a valid YouTube link.")
        return

    # Send an initial status message
    status_msg = await update.message.reply_text("🔎 Processing link...")

    try:
        # Run the blocking download function
        video_path, error = await download_video(url, chat_id, status_msg)

        if error:
            await status_msg.edit_text(f"❌ Error: {error}")
            return

        await status_msg.edit_text("⬆️ Uploading to Telegram...")
        
        # Upload the video
        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(chat_id=chat_id, video=video_file)
        
        # Cleanup
        await status_msg.delete()
        os.remove(video_path)

    except Exception as e:
        await status_msg.edit_text(f"❌ An error occurred: {str(e)}")
        if video_path and os.path.exists(video_path):
            os.remove(video_path)

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN environment variable not set.")
        exit(1)

    # 1. Start the dummy Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Start the Telegram Bot
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Bot is polling...")
    application.run_polling()

import os
import logging
from flask import Flask
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask App for Render Health Check ---
app = Flask('')

@app.route('/')
def health_check():
    return "Bot is alive and polling!", 200

def run_flask():
    # Render provides the PORT environment variable.
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
# -----------------------------------------


# --- Telegram Bot Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Hi! I am your bot.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('This is the help command.')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"You said: {update.message.text}")
# ----------------------------


def main() -> None:
    """Start the bot."""
    # Start Flask server in a background thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Create the Telegram Application
    token = os.environ.get('BOT_TOKEN')
    if not token:
        logger.error("BOT_TOKEN environment variable not set!")
        return

    application = Application.builder().token(token).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Start the polling
    logger.info("Bot is now polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
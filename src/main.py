import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
import threading
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError, Forbidden, TimedOut

from database.database_manager import Database

# --- Flask App for Render Health Check ---
flask_app = Flask('')

@flask_app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)
# -----------------------------------------

# --- Bot Configuration ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(SELECTING_ACTION, ADD_CONTENT, ADD_CATEGORY, ADD_SUBJECT,
 AWAIT_NOTE, AWAIT_EDIT, AWAIT_REMINDER, AWAIT_SEARCH) = range(8)
# -------------------------

# ✨ Global Error Handler ✨
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors and handle specific network issues."""
    error = context.error
    logger.error("Exception while handling an update:", exc_info=error)

    if isinstance(error, Conflict):
        logger.warning("Conflict error detected, likely due to another instance running.")
    elif isinstance(error, TimedOut):
        logger.warning("Timeout error. The bot will automatically retry.")
    elif isinstance(error, NetworkError):
        logger.warning("Network error. Could be a temporary issue with connection to Telegram.")
    elif isinstance(error, Forbidden):
        if update and hasattr(update, 'effective_user') and update.effective_user:
            logger.warning(f"Forbidden: The bot was blocked by the user {update.effective_user.id}.")
        else:
            logger.warning("Forbidden: Bot may be kicked from a group or channel.")


class SaveMeBot:
    def __init__(self):
        db_path = os.environ.get('DATABASE_PATH', 'save_me_bot.db')
        self.db = Database(db_path=db_path)
        self.pending_items: Dict[int, Dict[str, Any]] = {}

    # --- Main Menu and Start ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Sends a welcome message and the main menu."""
        username = update.effective_user.first_name
        welcome_text = f"שלום {username}! 👋\nברוך הבא לבוט 'שמור לי'.\nבחר פעולה מהתפריט:"
        
        keyboard = [
            [KeyboardButton("➕ הוסף תוכן")],
            [KeyboardButton("🔍 חיפוש"), KeyboardButton("📚 הצג קטגוריות")],
            [KeyboardButton("⚙️ הגדרות")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
        return SELECTING_ACTION

    # --- Handlers for Main Menu Buttons ---
    async def ask_for_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text("שלח לי את התוכן לשמירה (טקסט, קובץ, תמונה וכו'):" )
        return ADD_CONTENT

    async def ask_for_search_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text("מה לחפש?")
        return AWAIT_SEARCH

    # --- Conversation Flow for Adding an Item ---
    async def receive_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user_id = update.effective_user.id
        message = update.message
        content_data = {'user_id': user_id}

        if message.text:
            content_data.update({'type': 'text', 'content': message.text})
        elif message.photo:
            content_data.update({'type': 'photo', 'file_id': message.photo[-1].file_id, 'caption': message.caption or ""})
        elif message.document:
            content_data.update({'type': 'document', 'file_id': message.document.file_id, 'file_name': message.document.file_name, 'caption': message.caption or ""})
        elif message.video:
            content_data.update({'type': 'video', 'file_id': message.video.file_id, 'caption': message.caption or ""})
        elif message.voice:
            content_data.update({'type': 'voice', 'file_id': message.voice.file_id, 'caption': message.caption or ""})
        else:
            await update.message.reply_text("סוג תוכן לא נתמך. נסה שוב.")
            return ADD_CONTENT
        
        context.user_data['new_item'] = content_data
        await self.show_category_selection(update, context)
        return ADD_CATEGORY

    async def show_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        categories = self.db.get_user_categories(user_id)
        keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in categories]
        keyboard.append([InlineKeyboardButton("🆕 קטגוריה חדשה", callback_data="cat_new")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("בחר קטגוריה:", reply_markup=reply_markup)

    async def receive_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user_id = update.effective_user.id
        category_name = ""
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            if query.data == 'cat_new':
                await query.edit_message_text("הקלד שם לקטגוריה החדשה:")
                return ADD_CATEGORY
            category_name = query.data.replace('cat_', '')
            await query.edit_message_text(f"קטגוריה: {category_name}\n\nהקלד נושא לפריט:")
        else:
            category_name = update.message.text.strip()
            await update.message.reply_text(f"קטגוריה: {category_name}\n\nהקלד נושא לפריט:")

        context.user_data['new_item']['category'] = category_name
        return ADD_SUBJECT

    async def receive_subject_and_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        subject = update.message.text.strip()
        context.user_data['new_item']['subject'] = subject
        
        item_data = context.user_data['new_item']
        item_id = self.db.save_item(
            user_id=item_data['user_id'],
            category=item_data['category'],
            subject=item_data['subject'],
            content_type=item_data['type'],
            content=item_data.get('content', ''),
            file_id=item_data.get('file_id', ''),
            file_name=item_data.get('file_name', ''),
            caption=item_data.get('caption', '')
        )
        
        await update.message.reply_text("✅ נשמר בהצלחה!")
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['new_item']
        return ConversationHandler.END

    # --- Displaying Items and Categories ---
    async def show_item_with_actions(self, update_or_query, context: ContextTypes.DEFAULT_TYPE, item_id: int):
        item = self.db.get_item(item_id)
        if not item:
            if hasattr(update_or_query, 'edit_message_text'):
                await update_or_query.edit_message_text("הפריט נמחק.")
            return

        metadata_text = f"📁 **קטגוריה:** {item['category']}\n📝 **נושא:** {item['subject']}"
        if item['note']:
            metadata_text += f"\n\n🗒️ **הערה:** {item['note']}"

        pin_text = "📌 בטל קיבוע" if item['is_pinned'] else "📌 קבע"
        note_text = "✏️ ערוך הערה" if item['note'] else "📝 הוסף הערה"
        
        keyboard = [
            [InlineKeyboardButton(pin_text, callback_data=f"pin_{item_id}")],
            [InlineKeyboardButton("✏️ ערוך תוכן", callback_data=f"edit_{item_id}")],
            [InlineKeyboardButton(note_text, callback_data=f"note_{item_id}")],
            [InlineKeyboardButton("🗑️ מחק", callback_data=f"delete_{item_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif hasattr(update_or_query, 'message'):
            await update_or_query.message.reply_text(metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else: # Fallback for other update types
            await context.bot.send_message(chat_id=update_or_query.effective_chat.id, text=metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

        chat_id = update_or_query.effective_chat.id
        content_type = item.get('content_type', 'N/A')
        
        if content_type == 'text':
            await context.bot.send_message(chat_id=chat_id, text=item['content'])
        elif content_type == 'photo':
            await context.bot.send_photo(chat_id=chat_id, photo=item['file_id'], caption=item.get('caption', ''))
        elif content_type == 'document':
            await context.bot.send_document(chat_id=chat_id, document=item['file_id'], caption=item.get('caption', ''))
        elif content_type == 'video':
            await context.bot.send_video(chat_id=chat_id, video=item['file_id'], caption=item.get('caption', ''))
        elif content_type == 'voice':
            await context.bot.send_voice(chat_id=chat_id, voice=item['file_id'], caption=item.get('caption', ''))

    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        categories = self.db.get_user_categories(update.effective_user.id)
        if not categories:
            await update.message.reply_text("אין קטגוריות עדיין.")
            return
        
        keyboard = [[InlineKeyboardButton(f"{cat} ({self.db.get_category_count(update.effective_user.id, cat)})", callback_data=f"showcat_{cat}")] for cat in categories]
        await update.message.reply_text("בחר קטגוריה:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_category_items(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        category = query.data.replace('showcat_', '')
        items = self.db.get_category_items(update.effective_user.id, category)
        if not items:
            await query.edit_message_text("אין פריטים בקטגוריה זו.")
            return
        
        keyboard = [[InlineKeyboardButton(f"{'📌 ' if item['is_pinned'] else ''}{item['subject']}", callback_data=f"showitem_{item['id']}")] for item in items]
        await query.edit_message_text(f"📁 {category}:", reply_markup=InlineKeyboardMarkup(keyboard))

    # --- Search ---
    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.message.text.strip()
        results = self.db.search_items(update.effective_user.id, query)
        if not results:
            await update.message.reply_text("לא נמצאו תוצאות.")
            return ConversationHandler.END
        
        keyboard = [[InlineKeyboardButton(f"{item['category']} | {item['subject']}", callback_data=f"showitem_{item['id']}")] for item in results[:10]]
        await update.message.reply_text(f"נמצאו {len(results)} תוצאות:", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    # --- Callback Handlers for Item Actions ---
    async def show_item_from_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        item_id = int(query.data.replace('showitem_', ''))
        await self.show_item_with_actions(query, context, item_id)

    async def toggle_pin_item(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        item_id = int(query.data.replace('pin_', ''))
        self.db.toggle_pin(item_id)
        await self.show_item_with_actions(query, context, item_id)

    async def delete_item_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        item_id = int(query.data.replace('delete_', ''))
        keyboard = [
            [InlineKeyboardButton("✅ כן, מחק", callback_data=f"delconfirm_{item_id}")],
            [InlineKeyboardButton("❌ לא", callback_data=f"showitem_{item_id}")]
        ]
        await query.edit_message_text("האם אתה בטוח שברצונך למחוק את הפריט?", reply_markup=InlineKeyboardMarkup(keyboard))

    async def delete_item_confirmed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        item_id = int(query.data.replace('delconfirm_', ''))
        self.db.delete_item(item_id)
        await query.edit_message_text("✅ הפריט נמחק.")

    # --- Placeholder for settings ---
    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("אזור הגדרות (בבנייה).")

    # --- Handlers for editing note and content ---
    async def ask_for_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        item_id = int(query.data.replace('note_', ''))
        context.user_data['item_to_edit'] = item_id
        await query.edit_message_text("הקלד את ההערה החדשה:")
        return AWAIT_NOTE

    async def save_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        item_id = context.user_data['item_to_edit']
        note = update.message.text
        self.db.update_note(item_id, note)
        await update.message.reply_text("✅ ההערה עודכנה.")
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['item_to_edit']
        return ConversationHandler.END

    async def ask_for_edit_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        item_id = int(query.data.replace('edit_', ''))
        context.user_data['item_to_edit'] = item_id
        await query.edit_message_text("שלח את התוכן החדש:")
        return AWAIT_EDIT

    async def save_edited_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        item_id = context.user_data['item_to_edit']
        message = update.message
        # This is a simplified version; you can expand it like in receive_content
        if message.text:
            self.db.update_content(item_id, 'text', content=message.text)
            await update.message.reply_text("✅ התוכן עודכן.")
            await self.show_item_with_actions(update, context, item_id)
            del context.user_data['item_to_edit']
            return ConversationHandler.END
        else:
            await update.message.reply_text("סוג תוכן לא נתמך לעריכה.")
            return AWAIT_EDIT


def main() -> None:
    """Start the bot and the keep-alive server."""
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    token = os.environ.get('BOT_TOKEN')
    if not token:
        logger.error("FATAL: BOT_TOKEN environment variable is not set.")
        return

    bot = SaveMeBot()
    application = Application.builder().token(token).build()
    application.add_error_handler(error_handler)

    # --- Main Conversation Handler ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', bot.start)],
        states={
            SELECTING_ACTION: [
                MessageHandler(filters.TEXT & filters.Regex('^➕ הוסף תוכן$'), bot.ask_for_content),
                MessageHandler(filters.TEXT & filters.Regex('^🔍 חיפוש$'), bot.ask_for_search_query),
                MessageHandler(filters.TEXT & filters.Regex('^📚 הצג קטגוריות$'), bot.show_categories),
                MessageHandler(filters.TEXT & filters.Regex('^⚙️ הגדרות$'), bot.show_settings),
            ],
            ADD_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.receive_content)],
            ADD_CATEGORY: [
                CallbackQueryHandler(bot.receive_category, pattern="^cat_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_category)
            ],
            ADD_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_subject_and_save)],
            AWAIT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_search)],
            AWAIT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.save_note)],
            AWAIT_EDIT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.save_edited_content)],
        },
        fallbacks=[CommandHandler('start', bot.start)],
        per_message=False,
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    # --- Callback Handlers (outside conversation) ---
    application.add_handler(CallbackQueryHandler(bot.show_category_items, pattern="^showcat_"))
    application.add_handler(CallbackQueryHandler(bot.show_item_from_button, pattern="^showitem_"))
    application.add_handler(CallbackQueryHandler(bot.toggle_pin_item, pattern="^pin_"))
    application.add_handler(CallbackQueryHandler(bot.delete_item_confirmation, pattern="^delete_"))
    application.add_handler(CallbackQueryHandler(bot.delete_item_confirmed, pattern="^delconfirm_"))
    application.add_handler(CallbackQueryHandler(bot.ask_for_note, pattern="^note_"))
    application.add_handler(CallbackQueryHandler(bot.ask_for_edit_content, pattern="^edit_"))

    logger.info("Bot is starting to poll...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

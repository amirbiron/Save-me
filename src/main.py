import os
import logging
import threading
from datetime import datetime
from flask import Flask
from typing import Dict, Any
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError, Forbidden, TimedOut

from database.database_manager import Database
from activity_reporter import create_reporter

# Activity Reporter setup (keep after variable loading)
reporter = create_reporter(
    mongodb_uri="mongodb+srv://mumin:M43M2TFgLfGvhBwY@muminai.tm6x81b.mongodb.net/?retryWrites=true&w=majority&appName=muminAI",
    service_id="srv-d1t3lijuibrs738s0af0",
    service_name="SaveMe"
)

# Helper function to escape markdown
def escape_markdown(text: str) -> str:
    """Helper function to escape telegram markdown characters."""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- Flask App for Render Health Check ---
flask_app = Flask('')
@flask_app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)

# --- Bot Configuration ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation States
SELECTING_ACTION, AWAIT_CONTENT, AWAIT_CATEGORY, AWAIT_SUBJECT, AWAIT_SUBJECT_EDIT, AWAIT_NOTE, AWAIT_EDIT, AWAIT_SEARCH = range(8)

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error("Exception while handling an update:", exc_info=error)
    # Add specific error handling if needed

class SaveMeBot:
    def __init__(self):
        db_path = os.environ.get('DATABASE_PATH', 'save_me_bot.db')
        self.db = Database(db_path=db_path)

    # --- Activity Reporting Helper ---
    def _report(self, update: Update):
        reporter.report_activity(update.effective_user.id)

    # --- Main Menu and State Entrypoints ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
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

    async def ask_for_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        await update.message.reply_text("שלח לי את התוכן לשמירה:")
        return AWAIT_CONTENT

    async def ask_for_search_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        await update.message.reply_text("מה לחפש?")
        return AWAIT_SEARCH

    # --- Display Logic ---
    async def show_item_with_actions(self, update_or_query, context: ContextTypes.DEFAULT_TYPE, item_id: int):
        item = self.db.get_item(item_id)
        if not item:
            if hasattr(update_or_query, 'edit_message_text'):
                await update_or_query.edit_message_text("הפריט נמחק.")
            return

        # --- הודעת ניהול (מטא-דאטה וכפתורים) ---
        category = escape_markdown(item['category'])
        subject = escape_markdown(item['subject'])
        note = escape_markdown(item.get('note', ''))

        metadata_text = f"📁 **קטגוריה:** {category}\n📝 **נושא:** {subject}"
        if note:
            metadata_text += f"\n\n🗒️ **הערה:** {note}"

        pin_text = "📌 בטל קיבוע" if item.get('is_pinned') else "📌 קבע"
        note_text = "✏️ ערוך הערה" if item.get('note') else "📝 הוסף הערה"
        keyboard = [
            [InlineKeyboardButton(pin_text, callback_data=f"pin_{item_id}")],
            [InlineKeyboardButton("✏️ ערוך נושא", callback_data=f"editsubject_{item_id}")],
            [InlineKeyboardButton("✏️ ערוך תוכן", callback_data=f"edit_{item_id}")],
            [InlineKeyboardButton(note_text, callback_data=f"note_{item_id}")],
            [InlineKeyboardButton("🗑️ מחק", callback_data=f"delete_{item_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        chat_id = update_or_query.message.chat.id
        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(chat_id=chat_id, text=metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

        content_type = item.get('content_type')
        if content_type == 'text':
            await context.bot.send_message(chat_id=chat_id, text=item['content'])
        elif content_type and item.get('file_id'):
            send_map = {'photo': context.bot.send_photo, 'document': context.bot.send_document, 'video': context.bot.send_video, 'voice': context.bot.send_voice}
            if content_type in send_map:
                await send_map[content_type](chat_id=chat_id, **{content_type: item['file_id'], 'caption': item.get('caption', '')})

    # --- All other class methods from your bot logic go here ---
    # (show_categories, handle_search, receive_content, receive_category, save_note, etc.)
    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._report(update)
        categories = self.db.get_user_categories(update.effective_user.id)
        if not categories:
            await update.message.reply_text("אין קטגוריות עדיין.")
            return
        keyboard = [[InlineKeyboardButton(f"{cat} ({self.db.get_category_count(update.effective_user.id, cat)})", callback_data=f"showcat_{cat}")] for cat in categories]
        await update.message.reply_text("בחר קטגוריה להצגה:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def handle_search_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        query = update.message.text.strip()
        results = self.db.search_items(update.effective_user.id, query)
        if not results:
            await update.message.reply_text("לא נמצאו תוצאות.")
        else:
            keyboard = [[InlineKeyboardButton(f"{item['category']} | {item['subject']}", callback_data=f"showitem_{item['id']}")] for item in results[:10]]
            await update.message.reply_text(f"נמצאו {len(results)} תוצאות:", reply_markup=InlineKeyboardMarkup(keyboard))
        return await self.start(update, context) # Return to main menu

    async def receive_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        message = update.message
        content_data = {}
        if message.text: content_data.update({'type': 'text', 'content': message.text})
        elif message.photo: content_data.update({'type': 'photo', 'file_id': message.photo[-1].file_id, 'caption': message.caption or ""})
        elif message.document: content_data.update({'type': 'document', 'file_id': message.document.file_id, 'file_name': message.document.file_name, 'caption': message.caption or ""})
        else:
            await update.message.reply_text("סוג תוכן לא נתמך.")
            return await self.start(update, context)

        context.user_data['new_item'] = content_data

        categories = self.db.get_user_categories(update.effective_user.id)
        keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in categories]
        keyboard.append([InlineKeyboardButton("🆕 קטגוריה חדשה", callback_data="cat_new")])
        await update.message.reply_text("בחר קטגוריה:", reply_markup=InlineKeyboardMarkup(keyboard))
        return AWAIT_CATEGORY

    async def receive_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        query = update.callback_query
        category_name = ""
        if query:
            await query.answer()
            if query.data == 'cat_new':
                await query.edit_message_text("הקלד שם לקטגוריה החדשה:")
                return AWAIT_CATEGORY
            category_name = query.data.replace('cat_', '')
            await query.edit_message_text(f"קטגוריה: {category_name}\n\nכעת, הקלד נושא:")
        else:
            category_name = update.message.text.strip()
            await update.message.reply_text(f"קטגוריה: {category_name}\n\nכעת, הקלד נושא:")

        context.user_data['new_item']['category'] = category_name
        return AWAIT_SUBJECT

    async def receive_subject_and_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        context.user_data['new_item']['subject'] = update.message.text.strip()
        item_data = context.user_data['new_item']
        
        # Call the save_item function with all required parameters
        item_id = self.db.save_item(
            user_id=update.effective_user.id,
            category=item_data.get('category'),
            subject=item_data.get('subject'),
            content_type=item_data.get('type'),
            content=item_data.get('content', ''),
            file_id=item_data.get('file_id', ''),
            file_name=item_data.get('file_name', ''),
            caption=item_data.get('caption', '')
        )
        
        await update.message.reply_text("✅ נשמר בהצלחה!")
        await self.show_item_with_actions(update, context, item_id)
        
        # Clean up user_data and return to the main menu
        del context.user_data['new_item']
        return await self.start(update, context)

    async def save_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        item_id = context.user_data.get('action_item_id')
        if not item_id: return await self.start(update, context)
        self.db.update_note(item_id, update.message.text)
        await update.message.reply_text("✅ ההערה עודכנה.")
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['action_item_id']
        return await self.start(update, context)

    async def item_action_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        query = update.callback_query; await query.answer()
        action, item_id_str = query.data.split('_', 1)
        item_id = int(item_id_str)

        if action in ['showitem', 'pin', 'delete']:
            if action == 'pin': self.db.toggle_pin(item_id)
            if action == 'delete': self.db.delete_item(item_id); await query.edit_message_text("✅ הפריט נמחק."); return SELECTING_ACTION
            await self.show_item_with_actions(query, context, item_id)
            return SELECTING_ACTION

        context.user_data['action_item_id'] = item_id
        if action == 'note': await query.edit_message_text("הקלד את ההערה:"); return AWAIT_NOTE
        elif action == 'edit': await query.edit_message_text("שלח את התוכן החדש:"); return AWAIT_EDIT
        elif action == 'editsubject': await query.edit_message_text("הקלד את הנושא החדש:"); return AWAIT_SUBJECT_EDIT

        return SELECTING_ACTION

    async def save_edited_subject(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        item_id = context.user_data.get('action_item_id')
        if not item_id:
            return await self.start(update, context)
        new_subject = update.message.text.strip()
        self.db.update_subject(item_id, new_subject)
        await update.message.reply_text("✅ הנושא עודכן.")
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['action_item_id']
        return await self.start(update, context)

    async def show_category_items(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._report(update)
        query = update.callback_query; await query.answer()
        category = query.data.replace('showcat_', '')
        items = self.db.get_category_items(update.effective_user.id, category)
        if not items:
            await query.edit_message_text("אין פריטים בקטגוריה זו.")
            return
        keyboard = [[InlineKeyboardButton(f"{'📌 ' if item['is_pinned'] else ''}{item['subject']}", callback_data=f"showitem_{item['id']}")] for item in items]
        await query.edit_message_text(f"📁 פריטים בקטגוריית {category}:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._report(update)
        await update.message.reply_text("אזור הגדרות (בבנייה).")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        await update.message.reply_text("הפעולה בוטלה.")
        return await self.start(update, context)

def main() -> None:
    token = os.environ.get('BOT_TOKEN')
    if not token: logger.error("FATAL: BOT_TOKEN is not set."); return

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    bot = SaveMeBot()
    application = Application.builder().token(token).build()
    application.add_error_handler(error_handler)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', bot.start)],
        states={
            SELECTING_ACTION: [
                MessageHandler(filters.TEXT & filters.Regex('^➕ הוסף תוכן$'), bot.ask_for_content),
                MessageHandler(filters.TEXT & filters.Regex('^🔍 חיפוש$'), bot.ask_for_search_query),
                MessageHandler(filters.TEXT & filters.Regex('^📚 הצג קטגוריות$'), bot.show_categories),
                MessageHandler(filters.TEXT & filters.Regex('^⚙️ הגדרות$'), bot.show_settings),
                CallbackQueryHandler(bot.show_category_items, pattern="^showcat_"),
                CallbackQueryHandler(bot.item_action_router, pattern="^(showitem_|pin_|delete_|note_|edit_|editsubject_)")
            ],
            AWAIT_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.receive_content)],
            AWAIT_CATEGORY: [CallbackQueryHandler(bot.receive_category, pattern="^cat_"), MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_category)],
            AWAIT_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_subject_and_save)],
            AWAIT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_search_query)],
            AWAIT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.save_note)],
            AWAIT_EDIT: [MessageHandler(filters.ALL & ~filters.COMMAND, lambda u,c: c.bot.send_message(u.effective_chat.id, "Edit not implemented yet"))], # Placeholder
            AWAIT_SUBJECT_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.save_edited_subject)]
        },
        fallbacks=[CommandHandler('cancel', bot.cancel)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)

    logger.info("Bot is starting to poll...")
    application.run_polling()

if __name__ == '__main__':
    main()

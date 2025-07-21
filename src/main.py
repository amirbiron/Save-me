import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
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
 AWAIT_NOTE, AWAIT_EDIT) = range(6)
# -------------------------

# ✨ Global Error Handler ✨
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error("Exception while handling an update:", exc_info=error)
    if isinstance(error, (Conflict, TimedOut, NetworkError)):
        logger.warning(f"Network/Conflict error detected: {error}")
    elif isinstance(error, Forbidden):
        if update and hasattr(update, 'effective_user') and update.effective_user:
            logger.warning(f"Forbidden: Bot blocked by user {update.effective_user.id}.")
        else:
            logger.warning("Forbidden: Bot may be kicked from a group.")

class SaveMeBot:
    def __init__(self):
        db_path = os.environ.get('DATABASE_PATH', 'save_me_bot.db')
        self.db = Database(db_path=db_path)

    # --- Main Menu and General Commands ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        username = update.effective_user.first_name
        welcome_text = f"שלום {username}! 👋\nבחר פעולה מהתפריט:"
        keyboard = [[KeyboardButton("➕ הוסף תוכן חדש")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
        return ADD_CONTENT

    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        categories = self.db.get_user_categories(update.effective_user.id)
        if not categories:
            await update.message.reply_text("אין קטגוריות עדיין.")
            return
        keyboard = [[InlineKeyboardButton(f"{cat} ({self.db.get_category_count(update.effective_user.id, cat)})", callback_data=f"showcat_{cat}")] for cat in categories]
        await update.message.reply_text("הצג פריטים מקטגוריה:", reply_markup=InlineKeyboardMarkup(keyboard))

    # --- Display Logic ---
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

        chat_id = update_or_query.message.chat.id
        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else: # Fallback for Update object
            await context.bot.send_message(chat_id=chat_id, text=metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

        content_type = item.get('content_type')
        if content_type == 'text':
            await context.bot.send_message(chat_id=chat_id, text=item['content'])
        elif content_type and item.get('file_id'):
            send_map = {
                'photo': context.bot.send_photo, 'document': context.bot.send_document,
                'video': context.bot.send_video, 'voice': context.bot.send_voice
            }
            if content_type in send_map:
                # The argument name must match the type (e.g., 'photo' for send_photo)
                await send_map[content_type](chat_id=chat_id, **{content_type: item['file_id'], 'caption': item.get('caption', '')})

    # --- Item Action Handlers (Callbacks) ---
    async def item_action_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        action, item_id_str = query.data.split('_', 1)
        item_id = int(item_id_str)

        if action == 'showitem':
            await self.show_item_with_actions(query, context, item_id)
            return ConversationHandler.END
        if action == 'pin':
            self.db.toggle_pin(item_id)
            await self.show_item_with_actions(query, context, item_id)
            return ConversationHandler.END
        if action == 'delete':
            self.db.delete_item(item_id)
            await query.edit_message_text("✅ הפריט נמחק.")
            return ConversationHandler.END
        if action == 'note':
            context.user_data['action_item_id'] = item_id
            await query.edit_message_text("הקלד את ההערה:")
            return WAITING_NOTE
        if action == 'edit':
            context.user_data['action_item_id'] = item_id
            await query.edit_message_text("שלח את התוכן החדש:")
            return WAITING_EDIT
        
        return ConversationHandler.END
        
    async def show_category_items(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        category = query.data.replace('showcat_', '')
        items = self.db.get_category_items(update.effective_user.id, category)
        if not items:
            await query.edit_message_text("אין פריטים בקטגוריה זו.")
            return
        keyboard = [[InlineKeyboardButton(f"{'📌 ' if item['is_pinned'] else ''}{item['subject']}", callback_data=f"showitem_{item['id']}")] for item in items]
        await query.edit_message_text(f"📁 פריטים בקטגוריית {category}:", reply_markup=InlineKeyboardMarkup(keyboard))

    # --- Conversation Handlers ---
    async def ask_for_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        message = update.message
        content_data = {}
        if message.text: content_data.update({'type': 'text', 'content': message.text})
        elif message.photo: content_data.update({'type': 'photo', 'file_id': message.photo[-1].file_id, 'caption': message.caption or ""})
        elif message.document: content_data.update({'type': 'document', 'file_id': message.document.file_id, 'file_name': message.document.file_name, 'caption': message.caption or ""})
        elif message.video: content_data.update({'type': 'video', 'file_id': message.video.file_id, 'caption': message.caption or ""})
        elif message.voice: content_data.update({'type': 'voice', 'file_id': message.voice.file_id, 'caption': message.caption or ""})
        else:
            await update.message.reply_text("סוג תוכן לא נתמך.")
            return ConversationHandler.END

        context.user_data['new_item'] = content_data
        await self.show_category_selection(update, context)
        return WAITING_CATEGORY

    async def show_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        categories = self.db.get_user_categories(user_id)
        keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in categories]
        keyboard.append([InlineKeyboardButton("🆕 קטגוריה חדשה", callback_data="cat_new")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("בחר קטגוריה:", reply_markup=reply_markup)
        
    async def receive_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        category_name = ""
        if query:
            await query.answer()
            if query.data == 'cat_new':
                await query.edit_message_text("הקלד שם לקטגוריה החדשה:")
                return WAITING_CATEGORY
            category_name = query.data.replace('cat_', '')
            await query.edit_message_text(f"קטגוריה: {category_name}\n\nכעת, הקלד נושא לפריט:")
        else:
            category_name = update.message.text.strip()
            await update.message.reply_text(f"קטגוריה: {category_name}\n\nכעת, הקלד נושא לפריט:")
        
        context.user_data['new_item']['category'] = category_name
        return WAITING_SUBJECT

    async def receive_subject_and_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data['new_item']['subject'] = update.message.text.strip()
        item_data = context.user_data['new_item']
        item_id = self.db.save_item(
            user_id=update.effective_user.id, **item_data
        )
        await update.message.reply_text("✅ נשמר בהצלחה!", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("➕ הוסף תוכן חדש")]], resize_keyboard=True))
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['new_item']
        return ConversationHandler.END

    async def save_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        item_id = context.user_data.get('action_item_id')
        if not item_id: return ConversationHandler.END
        self.db.update_note(item_id, update.message.text)
        await update.message.reply_text("✅ ההערה עודכנה.")
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['action_item_id']
        return ConversationHandler.END
    
    async def save_edited_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        item_id = context.user_data.get('action_item_id')
        if not item_id: return ConversationHandler.END
        # Simplified version
        if update.message.text:
            self.db.update_content(item_id, 'text', content=update.message.text)
        # Add other types if needed
        await update.message.reply_text("✅ התוכן עודכן.")
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['action_item_id']
        return ConversationHandler.END

    async def cancel_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text("הפעולה בוטלה.")
        return ConversationHandler.END

def main() -> None:
    token = os.environ.get('BOT_TOKEN')
    if not token:
        logger.error("FATAL: BOT_TOKEN environment variable is not set.")
        return

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    bot = SaveMeBot()
    application = Application.builder().token(token).build()
    application.add_error_handler(error_handler)

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT('➕ הוסף תוכן חדש'), bot.ask_for_category),
            CallbackQueryHandler(bot.item_action_router, pattern="^(note_|edit_)")
        ],
        states={
            WAITING_CATEGORY: [CallbackQueryHandler(bot.receive_category, pattern="^cat_"), MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_category)],
            WAITING_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_subject_and_save)],
            WAITING_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.save_note)],
            WAITING_EDIT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.save_edited_content)]
        },
        fallbacks=[CommandHandler('start', bot.start), CommandHandler('cancel', bot.cancel_conversation)],
        per_user=True,
        per_chat=True
    )
    
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("categories", bot.show_categories))
    application.add_handler(conv_handler)
    
    # Callback handlers that are NOT part of a conversation
    application.add_handler(CallbackQueryHandler(bot.show_category_items, pattern="^showcat_"))
    application.add_handler(CallbackQueryHandler(bot.item_action_router, pattern="^(showitem_|pin_|delete_)"))
    
    # Add a general text handler for search, but with lower priority
    # This is a placeholder; a more robust search would be a command
    # application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.start), group=1)

    logger.info("Bot is starting to poll...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

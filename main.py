import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import asyncio
import sqlite3
from contextlib import asynccontextmanager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler, filters
)
from telegram.constants import ParseMode

from database_manager import Database
from config import Config

# הגדרת לוגים
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# מצבי שיחה
WAITING_CONTENT, WAITING_CATEGORY, WAITING_SUBJECT, WAITING_REMINDER, WAITING_EDIT, WAITING_NOTE = range(6)

class SaveMeBot:
    def __init__(self):
        self.db = Database()
        self.pending_items: Dict[int, Dict[str, Any]] = {}
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """הודעת פתיחה ותפריט ראשי"""
        user_id = update.effective_user.id
        username = update.effective_user.first_name or "משתמש"
        
        welcome_text = f"""
שלום {username}! 👋
ברוך הבא לבוט "שמור לי" 📝

🎯 **מטרה:** לעזור לך לשמור במהירות הודעות, רעיונות וקבצים, למיין אותם ולחזור אליהם בקלות.

בחר פעולה:
"""
        
        keyboard = [
            [KeyboardButton("➕ הוסף תוכן")],
            [KeyboardButton("🔍 חיפוש"), KeyboardButton("📚 הצג לפי קטגוריה")],
            [KeyboardButton("⚙️ הגדרות")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def handle_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בתפריט הראשי"""
        text = update.message.text
        user_id = update.effective_user.id
        
        if text == "➕ הוסף תוכן":
            await update.message.reply_text("שלח לי את התוכן לשמירה (טקסט, קובץ, תמונה או Reply על הודעה):")
            return WAITING_CONTENT
            
        elif text == "🔍 חיפוש":
            await self.search_prompt(update, context)
            
        elif text == "📚 הצג לפי קטגוריה":
            await self.show_categories(update, context)
            
        elif text == "⚙️ הגדרות":
            await self.show_settings(update, context)
            
        return ConversationHandler.END

    async def receive_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """קבלת תוכן לשמירה"""
        user_id = update.effective_user.id
        message = update.message
        
        # שמירת התוכן זמנית
        content_data = {
            'user_id': user_id,
            'timestamp': datetime.now()
        }
        
        if message.text:
            content_data['type'] = 'text'
            content_data['content'] = message.text
        elif message.photo:
            content_data['type'] = 'photo'
            content_data['file_id'] = message.photo[-1].file_id
            content_data['caption'] = message.caption or ""
        elif message.document:
            content_data['type'] = 'document'
            content_data['file_id'] = message.document.file_id
            content_data['file_name'] = message.document.file_name
            content_data['caption'] = message.caption or ""
        elif message.voice:
            content_data['type'] = 'voice'
            content_data['file_id'] = message.voice.file_id
            content_data['caption'] = message.caption or ""
        elif message.video:
            content_data['type'] = 'video'
            content_data['file_id'] = message.video.file_id
            content_data['caption'] = message.caption or ""
        else:
            await update.message.reply_text("סוג קובץ לא נתמך. אנא שלח טקסט, תמונה, מסמך או הודעה קולית.")
            return WAITING_CONTENT
        
        self.pending_items[user_id] = content_data
        
        # הצגת קטגוריות
        await self.show_category_selection(update, context)
        return WAITING_CATEGORY

    async def show_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """הצגת בחירת קטגוריה"""
        user_id = update.effective_user.id
        categories = self.db.get_user_categories(user_id)
        
        keyboard = []
        for category in categories:
            keyboard.append([InlineKeyboardButton(category, callback_data=f"cat_{category}")])
        
        keyboard.append([InlineKeyboardButton("🆕 קטגוריה חדשה", callback_data="new_category")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("בחר קטגוריה:", reply_markup=reply_markup)

    async def handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בבחירת קטגוריה"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        if data == "new_category":
            await query.edit_message_text("הקלד שם לקטגוריה החדשה:")
            return WAITING_CATEGORY
        elif data.startswith("cat_"):
            category = data[4:]  # הסרת "cat_"
            self.pending_items[user_id]['category'] = category
            await query.edit_message_text(f"נבחרה קטגוריה: {category}\n\nהקלד נושא לפריט:")
            return WAITING_SUBJECT
        
        return ConversationHandler.END

    async def receive_new_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """קבלת שם קטגוריה חדשה"""
        user_id = update.effective_user.id
        category = update.message.text.strip()
        
        if not category:
            await update.message.reply_text("שם הקטגוריה לא יכול להיות ריק. נסה שוב:")
            return WAITING_CATEGORY
        
        self.pending_items[user_id]['category'] = category
        await update.message.reply_text(f"נוצרה קטגוריה: {category}\n\nהקלד נושא לפריט:")
        return WAITING_SUBJECT

    async def receive_subject(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """קבלת נושא הפריט"""
        user_id = update.effective_user.id
        subject = update.message.text.strip()
        
        if not subject:
            await update.message.reply_text("הנושא לא יכול להיות ריק. נסה שוב:")
            return WAITING_SUBJECT
        
        self.pending_items[user_id]['subject'] = subject
        
        # הצגת אישור שמירה
        keyboard = [[InlineKeyboardButton("✅ שמור", callback_data="confirm_save")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"**פרטי הפריט:**\n"
            f"📁 קטגוריה: {self.pending_items[user_id]['category']}\n"
            f"📝 נושא: {subject}\n\n"
            f"לחץ לשמירה:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    async def confirm_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """אישור שמירת הפריט"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if user_id not in self.pending_items:
            await query.edit_message_text("שגיאה: לא נמצא פריט לשמירה.")
            return
        
        item_data = self.pending_items[user_id]
        
        # שמירה במסד הנתונים
        item_id = self.db.save_item(
            user_id=user_id,
            category=item_data['category'],
            subject=item_data['subject'],
            content_type=item_data['type'],
            content=item_data.get('content', ''),
            file_id=item_data.get('file_id', ''),
            file_name=item_data.get('file_name', ''),
            caption=item_data.get('caption', '')
        )
        
        # ניקוי הפריט הזמני
        del self.pending_items[user_id]
        
        await query.edit_message_text("✅ נשמר בהצלחה!")
        
        # הצגת הפריט עם כפתורי פעולה
        await self.show_item_with_actions(query, item_id)

    async def show_item_with_actions(self, query_or_update, item_id: int) -> None:
        """הצגת פריט עם כפתורי פעולה"""
        item = self.db.get_item(item_id)
        if not item:
            return
        
        # הכנת התוכן להצגה
        display_text = f"📁 {item['category']} | 📝 {item['subject']}\n"
        if item['note']:
            display_text += f"🗒️ {item['note']}\n"
        display_text += f"⏰ {item['created_at']}\n\n"
        
        # הוספת תוכן הפריט
        if item['content_type'] == 'text':
            display_text += item['content']
        elif item['caption']:
            display_text += f"📎 {item['caption']}"
        
        # כפתורי פעולה
        pin_text = "📌 בטל קיבוע" if item['is_pinned'] else "📌 קבע"
        note_text = "✏️ ערוך הערה" if item['note'] else "📝 הוסף הערה"
        
        keyboard = [
            [InlineKeyboardButton(pin_text, callback_data=f"pin_{item_id}")],
            [InlineKeyboardButton("🕰️ תזכורת", callback_data=f"remind_{item_id}")],
            [InlineKeyboardButton("✏️ ערוך תוכן", callback_data=f"edit_{item_id}")],
            [InlineKeyboardButton(note_text, callback_data=f"note_{item_id}")],
            [InlineKeyboardButton("🗑️ מחק", callback_data=f"delete_{item_id}")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            if hasattr(query_or_update, 'edit_message_text'):
                await query_or_update.edit_message_text(display_text, reply_markup=reply_markup)
            else:
                await query_or_update.message.reply_text(display_text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error showing item: {e}")

    async def handle_item_actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בפעולות על פריטים"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        action, item_id = data.split('_', 1)
        item_id = int(item_id)
        
        if action == "pin":
            self.db.toggle_pin(item_id)
            await self.show_item_with_actions(query, item_id)
            
        elif action == "remind":
            keyboard = [
                [InlineKeyboardButton("1 שעה", callback_data=f"setremind_{item_id}_1")],
                [InlineKeyboardButton("3 שעות", callback_data=f"setremind_{item_id}_3")],
                [InlineKeyboardButton("24 שעות", callback_data=f"setremind_{item_id}_24")],
                [InlineKeyboardButton("שעות מותאמות", callback_data=f"customremind_{item_id}")],
                [InlineKeyboardButton("❌ בטל", callback_data=f"back_{item_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("בעוד כמה שעות להזכיר?", reply_markup=reply_markup)
            
        elif action == "edit":
            await query.edit_message_text("שלח את התוכן החדש:")
            context.user_data['editing_item'] = item_id
            return WAITING_EDIT
            
        elif action == "note":
            item = self.db.get_item(item_id)
            if item['note']:
                await query.edit_message_text(f"ההערה הנוכחית: {item['note']}\n\nהקלד הערה חדשה:")
            else:
                await query.edit_message_text("הקלד הערה לפריט:")
            context.user_data['editing_note'] = item_id
            return WAITING_NOTE
            
        elif action == "delete":
            keyboard = [
                [InlineKeyboardButton("🗑️ מחק תוכן", callback_data=f"delcontent_{item_id}")],
                [InlineKeyboardButton("🗑️ מחק הערה", callback_data=f"delnote_{item_id}")],
                [InlineKeyboardButton("❌ בטל", callback_data=f"back_{item_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("מה למחוק?", reply_markup=reply_markup)
            
        elif action == "setremind":
            _, hours = data.split('_', 2)[1:]
            hours = int(hours)
            reminder_time = datetime.now() + timedelta(hours=hours)
            self.db.set_reminder(item_id, reminder_time)
            
            # הוספת משימה לתזכורת
            context.job_queue.run_once(
                self.send_reminder, 
                when=reminder_time,
                data={'item_id': item_id, 'user_id': query.from_user.id}
            )
            
            await query.edit_message_text(f"✅ תזכורת נקבעה לעוד {hours} שעות")
            await self.show_item_with_actions(query, item_id)
            
        elif action == "customremind":
            await query.edit_message_text("הקלד מספר שעות (1-168):")
            context.user_data['custom_reminder'] = item_id
            return WAITING_REMINDER
            
        elif action == "back":
            await self.show_item_with_actions(query, item_id)
            
        elif action == "delcontent":
            self.db.delete_item(item_id)
            await query.edit_message_text("✅ הפריט נמחק")
            
        elif action == "delnote":
            self.db.delete_note(item_id)
            await query.edit_message_text("✅ ההערה נמחקה")
            await self.show_item_with_actions(query, item_id)
        
        return ConversationHandler.END

    async def send_reminder(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """שליחת תזכורת"""
        job_data = context.job.data
        item_id = job_data['item_id']
        user_id = job_data['user_id']
        
        item = self.db.get_item(item_id)
        if not item:
            return
        
        # שליחת הפריט מחדש
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🔔 **תזכורת!**\n\n{item['category']} | {item['subject']}\n\n{item['content'] or item['caption']}"
        )
        
        # הצגת הפריט עם כפתורי פעולה
        await self.show_item_with_actions_by_user(context, user_id, item_id)

    async def show_item_with_actions_by_user(self, context: ContextTypes.DEFAULT_TYPE, user_id: int, item_id: int) -> None:
        """הצגת פריט עם כפתורי פעולה למשתמש ספציפי"""
        item = self.db.get_item(item_id)
        if not item:
            return
        
        # הכנת התוכן להצגה (כמו בפונקציה הקודמת)
        display_text = f"📁 {item['category']} | 📝 {item['subject']}\n"
        if item['note']:
            display_text += f"🗒️ {item['note']}\n"
        display_text += f"⏰ {item['created_at']}\n\n"
        
        if item['content_type'] == 'text':
            display_text += item['content']
        elif item['caption']:
            display_text += f"📎 {item['caption']}"
        
        # כפתורי פעולה
        pin_text = "📌 בטל קיבוע" if item['is_pinned'] else "📌 קבע"
        note_text = "✏️ ערוך הערה" if item['note'] else "📝 הוסף הערה"
        
        keyboard = [
            [InlineKeyboardButton(pin_text, callback_data=f"pin_{item_id}")],
            [InlineKeyboardButton("🕰️ תזכורת", callback_data=f"remind_{item_id}")],
            [InlineKeyboardButton("✏️ ערוך תוכן", callback_data=f"edit_{item_id}")],
            [InlineKeyboardButton(note_text, callback_data=f"note_{item_id}")],
            [InlineKeyboardButton("🗑️ מחק", callback_data=f"delete_{item_id}")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=user_id,
            text=display_text,
            reply_markup=reply_markup
        )

    async def handle_edit_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בעריכת תוכן פריט"""
        user_id = update.effective_user.id
        message = update.message
        item_id = context.user_data.get('editing_item')
        
        if not item_id:
            await update.message.reply_text("שגיאה: לא נמצא פריט לעריכה")
            return ConversationHandler.END
        
        # עדכון התוכן בהתאם לסוג ההודעה
        if message.text:
            self.db.update_content(item_id, 'text', content=message.text)
        elif message.photo:
            self.db.update_content(
                item_id, 'photo', 
                file_id=message.photo[-1].file_id, 
                caption=message.caption or ""
            )
        elif message.document:
            self.db.update_content(
                item_id, 'document',
                file_id=message.document.file_id,
                file_name=message.document.file_name,
                caption=message.caption or ""
            )
        elif message.voice:
            self.db.update_content(
                item_id, 'voice',
                file_id=message.voice.file_id,
                caption=message.caption or ""
            )
        elif message.video:
            self.db.update_content(
                item_id, 'video',
                file_id=message.video.file_id,
                caption=message.caption or ""
            )
        else:
            await update.message.reply_text("סוג קובץ לא נתמך")
            return ConversationHandler.END
        
        # ניקוי נתוני העריכה
        del context.user_data['editing_item']
        
        await update.message.reply_text("✅ התוכן עודכן בהצלחה!")
        
        # הצגת הפריט המעודכן
        await self.show_item_with_actions(update, item_id)
        
        return ConversationHandler.END

    async def handle_edit_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בעריכת הערה"""
        user_id = update.effective_user.id
        note = update.message.text.strip()
        item_id = context.user_data.get('editing_note')
        
        if not item_id:
            await update.message.reply_text("שגיאה: לא נמצא פריט לעריכה")
            return ConversationHandler.END
        
        if not note:
            await update.message.reply_text("ההערה לא יכולה להיות ריקה. נסה שוב:")
            return WAITING_NOTE
        
        # עדכון ההערה
        self.db.update_note(item_id, note)
        
        # ניקוי נתוני העריכה
        del context.user_data['editing_note']
        
        await update.message.reply_text("✅ ההערה עודכנה בהצלחה!")
        
        # הצגת הפריט המעודכן
        await self.show_item_with_actions(update, item_id)
        
        return ConversationHandler.END

    async def handle_custom_reminder(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בתזכורת מותאמת"""
        user_id = update.effective_user.id
        text = update.message.text.strip()
        item_id = context.user_data.get('custom_reminder')
        
        if not item_id:
            await update.message.reply_text("שגיאה: לא נמצא פריט להגדרת תזכורת")
            return ConversationHandler.END
        
        try:
            hours = int(text)
            if hours < Config.MIN_REMINDER_HOURS or hours > Config.MAX_REMINDER_HOURS:
                await update.message.reply_text(
                    f"מספר השעות צריך להיות בין {Config.MIN_REMINDER_HOURS} ל-{Config.MAX_REMINDER_HOURS}. נסה שוב:"
                )
                return WAITING_REMINDER
        except ValueError:
            await update.message.reply_text("אנא הקלד מספר תקין של שעות:")
            return WAITING_REMINDER
        
        # קביעת התזכורת
        reminder_time = datetime.now() + timedelta(hours=hours)
        self.db.set_reminder(item_id, reminder_time)
        
        # הוספת משימה לתזכורת
        context.job_queue.run_once(
            self.send_reminder,
            when=reminder_time,
            data={'item_id': item_id, 'user_id': user_id}
        )
        
        # ניקוי נתוני התזכורת
        del context.user_data['custom_reminder']
        
        await update.message.reply_text(f"✅ תזכורת נקבעה לעוד {hours} שעות")
        
        # הצגת הפריט
        await self.show_item_with_actions(update, item_id)
        
        return ConversationHandler.END

    async def search_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """הצגת הנחיה לחיפוש"""
        await update.message.reply_text("מה לחפש? (טקסט חופשי, קטגוריה או נושא)")

    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """טיפול בחיפוש"""
        user_id = update.effective_user.id
        query = update.message.text.strip()
        
        if not query:
            await update.message.reply_text("הקלד מילת חיפוש")
            return
        
        results = self.db.search_items(user_id, query)
        
        if not results:
            await update.message.reply_text("לא נמצאו תוצאות")
            return
        
        # הצגת תוצאות
        keyboard = []
        for i, item in enumerate(results[:10], 1):
            button_text = f"{i}. {item['category']} | {item['subject']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"show_{item['id']}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"נמצאו {len(results)} תוצאות (מציג 10 ראשונות):",
            reply_markup=reply_markup
        )

    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """הצגת קטגוריות"""
        user_id = update.effective_user.id
        categories = self.db.get_user_categories(user_id)
        
        if not categories:
            await update.message.reply_text("אין קטגוריות עדיין")
            return
        
        keyboard = []
        for category in categories:
            count = self.db.get_category_count(user_id, category)
            button_text = f"{category} ({count})"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"showcat_{category}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("בחר קטגוריה:", reply_markup=reply_markup)

    async def show_category_items(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """הצגת פריטים בקטגוריה"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        category = query.data[8:]  # הסרת "showcat_"
        
        items = self.db.get_category_items(user_id, category)
        
        if not items:
            await query.edit_message_text("אין פריטים בקטגוריה זו")
            return
        
        keyboard = []
        for item in items:
            pin_icon = "📌 " if item['is_pinned'] else ""
            button_text = f"{pin_icon}{item['subject']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"show_{item['id']}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"📁 {category} ({len(items)} פריטים):",
            reply_markup=reply_markup
        )

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """הצגת הגדרות"""
        keyboard = [
            [InlineKeyboardButton("🗂️ ניהול קטגוריות", callback_data="manage_categories")],
            [InlineKeyboardButton("📊 סטטיסטיקות", callback_data="stats")],
            [InlineKeyboardButton("🔄 ייצוא נתונים", callback_data="export")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("הגדרות:", reply_markup=reply_markup)

    async def show_item_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """הצגת פריט מכפתור"""
        query = update.callback_query
        await query.answer()
        
        item_id = int(query.data[5:])  # הסרת "show_"
        await self.show_item_with_actions(query, item_id)

def main() -> None:
    """פונקציה ראשית"""
    bot = SaveMeBot()
    
    # יצירת האפליקציה
    application = Application.builder().token(Config.BOT_TOKEN).build()
    
    # הוספת handlers
    application.add_handler(CommandHandler("start", bot.start))
    
    # שיחת הוספת תוכן
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_main_menu)],
        states={
            WAITING_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.receive_content)],
            WAITING_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_new_category)],
            WAITING_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_subject)],
            WAITING_EDIT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.handle_edit_content)],
            WAITING_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_edit_note)],
            WAITING_REMINDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_custom_reminder)]
        },
        fallbacks=[CommandHandler("start", bot.start)]
    )
    
    application.add_handler(conv_handler)
    
    # כפתורי callback
    application.add_handler(CallbackQueryHandler(bot.handle_category_selection, pattern="^(cat_|new_category)"))
    application.add_handler(CallbackQueryHandler(bot.confirm_save, pattern="^confirm_save"))
    application.add_handler(CallbackQueryHandler(bot.handle_item_actions, pattern="^(pin_|remind_|edit_|note_|delete_|setremind_|customremind_|back_|delcontent_|delnote_)"))
    application.add_handler(CallbackQueryHandler(bot.show_category_items, pattern="^showcat_"))
    application.add_handler(CallbackQueryHandler(bot.show_item_callback, pattern="^show_"))
    
    # חיפוש
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_search))
    
    # הפעלת הבוט
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

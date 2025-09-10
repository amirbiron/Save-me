import os
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import calendar
from flask import Flask
from typing import Dict, Any
import re
from io import BytesIO
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError, Forbidden, TimedOut, BadRequest

from database.database_manager import Database
from activity_reporter import create_reporter
from github_gist_handler import GithubGistHandler
from internal_share_handler import InternalShareHandler

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

# Heuristic language detection for code snippets
def detect_code_language(text: str) -> str | None:
    try:
        sample = text.strip()
        first_line = sample.splitlines()[0] if sample else ""
        if re.match(r'^#!/bin/(bash|sh)', sample):
            return 'bash'
        if '<?php' in sample:
            return 'php'
        if re.search(r'</?[a-zA-Z][\w:-]*\b', sample) and '<' in sample and '>' in sample:
            return 'html'
        if re.search(r'^\s*\{', sample) and re.search(r'"[^"]+"\s*:', sample):
            return 'json'
        if re.search(r'^(FROM|RUN|CMD|COPY|ENTRYPOINT|ENV|ARG|WORKDIR|EXPOSE)\b', sample, re.IGNORECASE | re.MULTILINE):
            return 'dockerfile'
        if re.search(r'^\s*\[.+\]\s*$', sample, re.MULTILINE) and re.search(r'=', sample):
            return 'ini'
        if re.search(r'^[\s\-\w]+:\s+.+$', sample, re.MULTILINE) and not re.search(r';\s*$', sample, re.MULTILINE):
            return 'yaml'
        if re.search(r'\bpackage\s+main\b', sample) or re.search(r'\bfunc\s+\w+\s*\(', sample):
            return 'go'
        if re.search(r'\bfn\s+\w+\s*\(|println!\s*\(', sample):
            return 'rust'
        if re.search(r'\busing\s+System\b|\bnamespace\b|public\s+class\b', sample):
            return 'csharp'
        if re.search(r'\bpublic\s+class\b|System\.out\.println', sample):
            return 'java'
        if re.search(r'\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|\bCREATE\b|\bTABLE\b', sample, re.IGNORECASE):
            return 'sql'
        if re.search(r'\b(def|class)\s+\w+|from\s+\w+\s+import|import\s+\w+', sample) and not re.search(r';\s*$', first_line):
            return 'python'
        if re.search(r'\b(const|let|var|function)\b|=>|console\.log|import\s+.*\s+from\s+', sample):
            return 'javascript'
        return None
    except Exception:
        return None

# Detect if full text is a fenced code block
def is_fenced_code_block(text: str) -> bool:
    try:
        sample = text.strip()
        if not (sample.startswith('```') and sample.endswith('```')):
            return False
        # ensure at least opening and closing fence present
        return sample.count('```') >= 2
    except Exception:
        return False

# Extract code and optional language from a fenced code block
def extract_fenced_code(text: str) -> tuple[str, str | None]:
    try:
        sample = text.strip()
        m = re.match(r'^```([a-zA-Z0-9_+-]*)\n([\s\S]*?)\n```\s*$', sample)
        if m:
            lang = m.group(1) or None
            code = m.group(2)
            return code, lang
        # Fallback: remove first and last fence line
        if sample.startswith('```') and sample.endswith('```'):
            lines = sample.splitlines()
            if len(lines) >= 2:
                first = lines[0]
                last = lines[-1]
                if first.startswith('```') and last.strip() == '```':
                    code = '\n'.join(lines[1:-1])
                    return code, None
        return text, None
    except Exception:
        return text, None

# Helper to format text content to preserve code/Markdown rendering in Telegram
def format_text_content_for_telegram(text: str):
    """Return (formatted_text, parse_mode) for Telegram so code/Markdown is preserved.

    - If content already includes triple backticks, send as-is with Markdown parse mode.
    - If content looks like Markdown (headings, lists, emphasis, links), send as-is with Markdown parse mode.
    - If content looks like code, wrap in triple backticks (with language when detected) and send with Markdown parse mode.
    - Otherwise, send as plain text (no parse mode).
    """
    try:
        # Global override to force code block rendering for all text
        if 'FORCE_CODE_BLOCKS' in globals() and FORCE_CODE_BLOCKS:
            if is_fenced_code_block(text):
                return text, ParseMode.MARKDOWN_V2
            lang = detect_code_language(text) or ''
            fence = f"```{lang}\n{text}\n```"
            return fence, ParseMode.MARKDOWN_V2

        # PRIORITY 1: If content contains triple backticks ANYWHERE, always use MarkdownV2
        # This ensures code blocks are preserved and displayed properly
        if '```' in text:
            # Use MARKDOWN_V2 for better code block support
            return text, ParseMode.MARKDOWN_V2

        # If it's already a properly fenced code block
        if is_fenced_code_block(text):
            return text, ParseMode.MARKDOWN_V2

        # Detect Markdown-like content (should be rendered, not shown as code)
        markdown_patterns = [
            r'(^|\n)#{1,6}\s',                 # headings
            r'(^|\n)(?:\- |\* |\d+\. |> )', # lists / blockquote
            r'\*\*[^\n]+\*\*',              # bold
            r'__[^\n]+__',                      # bold (alt)
            r'(?<!\*)\*[^\n]+\*(?!\*)',     # italic
            r'_(?:[^\n_]|_[^\n])_',          # italic (alt)
            r'\[[^\]]+\]\([^\)]+\)',       # links [text](url)
        ]
        if any(re.search(p, text, re.MULTILINE) for p in markdown_patterns):
            # If it has markdown patterns, keep it as markdown
            return text, ParseMode.MARKDOWN_V2

        # Detect code-like content (should be fenced)
        code_patterns = [
            r'(^|\n)\s{4,}',  # indented code blocks
            r'\b(def|class|import|from|const|let|var|function|public|private|return|if|else|for|while|try|catch)\b',
            r'[{};=<>\[\]]',   # common code punctuation
        ]
        if any(re.search(pattern, text) for pattern in code_patterns):
            lang = detect_code_language(text) or ''
            fence = f"```{lang}\n{text}\n```"
            return fence, ParseMode.MARKDOWN_V2

        # Default: If no special formatting detected, wrap in code block for consistent display
        # This ensures saved content is always displayed as code blocks
        lang = detect_code_language(text) or ''
        fence = f"```{lang}\n{text}\n```"
        return fence, ParseMode.MARKDOWN_V2
        
    except Exception:
        return text, None

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
SELECTING_ACTION, AWAIT_CONTENT, AWAIT_CATEGORY, AWAIT_SUBJECT, AWAIT_SUBJECT_EDIT, AWAIT_NOTE, AWAIT_EDIT, AWAIT_SEARCH, AWAIT_MD_TEXT, AWAIT_MULTIPART, AWAIT_REMINDER_HOURS, AWAIT_REMINDER_TIME, AWAIT_GITHUB_TOKEN, AWAIT_GIST_CONFIRM = range(14)

# --- Display/length thresholds and helpers ---
TELEGRAM_MAX_MESSAGE_CHARS = 4000
PREVIEW_THRESHOLD_CHARS = 3500
VERY_LONG_THRESHOLD_CHARS = 12000

# Reminder bounds (fallback to env if provided)
MIN_REMINDER_HOURS = int(os.environ.get('MIN_REMINDER_HOURS', '1'))
MAX_REMINDER_HOURS = int(os.environ.get('MAX_REMINDER_HOURS', '168'))
LOCAL_TZ = ZoneInfo(os.environ.get('TZ', 'Asia/Jerusalem'))

# Global flag: force wrapping all textual content in fenced code blocks
# Set to 'true' to always display saved content as code blocks
FORCE_CODE_BLOCKS = os.environ.get('FORCE_CODE_BLOCKS', 'true').lower() == 'true'

def split_text_for_telegram(text: str, max_chars: int = TELEGRAM_MAX_MESSAGE_CHARS) -> list[str]:
    """Split text into chunks under Telegram message limit, preferring line boundaries."""
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            nl = text.rfind('\n', start, end)
            if nl != -1 and nl > start + 100:
                end = nl + 1
        chunks.append(text[start:end])
        start = end
    return chunks

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error("Exception while handling an update:", exc_info=error)
    # Add specific error handling if needed

class SaveMeBot:
    def __init__(self):
        db_path = os.environ.get('DATABASE_PATH', 'save_me_bot.db')
        self.db = Database(db_path=db_path)
        self.gist_handler = GithubGistHandler(self.db)
        self.share_handler = InternalShareHandler(self.db)
        self._start_reminder_job = False

    async def reminder_hours_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle custom reminder hours entry."""
        self._report(update)
        text = (update.message.text or '').strip()
        if not text.isdigit():
            await update.message.reply_text(f"יש להזין מספר שעות תקין ({MIN_REMINDER_HOURS}-{MAX_REMINDER_HOURS}).")
            return AWAIT_REMINDER_HOURS
        hours = int(text)
        if hours < MIN_REMINDER_HOURS or hours > MAX_REMINDER_HOURS:
            await update.message.reply_text(f"המספר מחוץ לטווח. בחר בין {MIN_REMINDER_HOURS}-{MAX_REMINDER_HOURS}.")
            return AWAIT_REMINDER_HOURS
        item_id = context.user_data.get('pending_reminder_item_id')
        if not item_id:
            await update.message.reply_text("אין פריט ממתין.")
            return SELECTING_ACTION
        remind_at = datetime.now(tz=LOCAL_TZ).replace(tzinfo=None) + timedelta(hours=hours)
        ok = self.db.set_reminder(int(item_id), remind_at)
        if ok:
            await update.message.reply_text(f"⏰ נקבעה תזכורת בעוד {hours} שעות.")
        else:
            await update.message.reply_text("שגיאה בקביעת תזכורת.")
        context.user_data.pop('pending_reminder_item_id', None)
        return SELECTING_ACTION

    async def reminder_tick(self, context: ContextTypes.DEFAULT_TYPE):
        """Periodic job: check pending reminders and deliver."""
        try:
            due_items = self.db.get_pending_reminders()
            for item in due_items:
                chat_id = item['user_id']
                subject = item.get('subject', '')
                category = item.get('category', '')
                text = f"⏰ תזכורת לפריט:\n📁 {category}\n📝 {subject}"
                try:
                    await context.bot.send_message(chat_id=chat_id, text=text)
                except Exception:
                    pass
                self.db.clear_reminder(item['id'])
        except Exception:
            # do not raise in job
            pass

    # --- Calendar UI helpers ---
    def _build_calendar_markup(self, item_id: int, year: int, month: int) -> InlineKeyboardMarkup:
        cal = calendar.Calendar(firstweekday=6)  # Start on Sunday
        month_days = cal.monthdayscalendar(year, month)
        rows = []
        # Header with month/year and nav
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        month_name = f"{month:02d}/{year}"
        rows.append([
            InlineKeyboardButton("‹", callback_data=f"cal_{item_id}_{prev_year}_{prev_month}"),
            InlineKeyboardButton(month_name, callback_data="noop"),
            InlineKeyboardButton("›", callback_data=f"cal_{item_id}_{next_year}_{next_month}")
        ])
        # Weekday headers
        rows.append([
            InlineKeyboardButton("א", callback_data="noop"),
            InlineKeyboardButton("ב", callback_data="noop"),
            InlineKeyboardButton("ג", callback_data="noop"),
            InlineKeyboardButton("ד", callback_data="noop"),
            InlineKeyboardButton("ה", callback_data="noop"),
            InlineKeyboardButton("ו", callback_data="noop"),
            InlineKeyboardButton("ש", callback_data="noop"),
        ])
        # Days
        for week in month_days:
            row = []
            for day in week:
                if day == 0:
                    row.append(InlineKeyboardButton(" ", callback_data="noop"))
                else:
                    row.append(InlineKeyboardButton(str(day), callback_data=f"calpick_{item_id}_{year}_{month}_{day}"))
            rows.append(row)
        # Cancel
        rows.append([InlineKeyboardButton("ביטול", callback_data=f"remcancel_{item_id}")])
        return InlineKeyboardMarkup(rows)

    async def open_calendar(self, query, context: ContextTypes.DEFAULT_TYPE, item_id: int):
        now = datetime.now(tz=LOCAL_TZ)
        markup = self._build_calendar_markup(item_id, now.year, now.month)
        await query.edit_message_reply_markup(reply_markup=markup)

    async def calendar_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        data = query.data
        if data.startswith('cal_'):
            _, item_id, year, month = data.split('_')
            markup = self._build_calendar_markup(int(item_id), int(year), int(month))
            await query.edit_message_reply_markup(reply_markup=markup)
            return SELECTING_ACTION
        if data.startswith('calpick_'):
            _, item_id, year, month, day = data.split('_')
            # store the chosen date and ask for time
            context.user_data['pending_reminder_item_id'] = int(item_id)
            context.user_data['pending_reminder_date'] = (int(year), int(month), int(day))
            times = [
                [InlineKeyboardButton("09:00", callback_data="time_09_00"), InlineKeyboardButton("12:00", callback_data="time_12_00"), InlineKeyboardButton("18:00", callback_data="time_18_00")],
                [InlineKeyboardButton("בחר שעה…", callback_data="time_custom"), InlineKeyboardButton("ביטול", callback_data=f"remcancel_{item_id}")]
            ]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(times))
            return SELECTING_ACTION
        if data.startswith('time_'):
            _, hh, mm = data.split('_')
            y, m, d = context.user_data.get('pending_reminder_date', (None, None, None))
            item_id = context.user_data.get('pending_reminder_item_id')
            if not all([y, m, d, item_id]):
                return SELECTING_ACTION
            remind_at = datetime(year=y, month=m, day=d, hour=int(hh), minute=int(mm))
            ok = self.db.set_reminder(int(item_id), remind_at)
            chat_id = query.message.chat.id
            if ok:
                await context.bot.send_message(chat_id=chat_id, text=f"⏰ נקבעה תזכורת ל-{d:02d}/{m:02d}/{y} בשעה {hh}:{mm}.")
            else:
                await context.bot.send_message(chat_id=chat_id, text="שגיאה בקביעת תזכורת.")
            context.user_data.pop('pending_reminder_date', None)
            context.user_data.pop('pending_reminder_item_id', None)
            return SELECTING_ACTION
        if data == 'time_custom':
            await query.edit_message_text("הקלד שעה בפורמט HH:MM (24h):")
            return AWAIT_REMINDER_TIME
        if data.startswith('remcancel_'):
            await query.edit_message_text("בוטל.")
            context.user_data.pop('pending_reminder_date', None)
            context.user_data.pop('pending_reminder_item_id', None)
            return SELECTING_ACTION
        return SELECTING_ACTION

    async def reminder_time_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        text = (update.message.text or '').strip()
        if not re.match(r'^\d{2}:\d{2}$', text):
            await update.message.reply_text("פורמט לא תקין. הקלד שעה כמו 09:30")
            return AWAIT_REMINDER_TIME
        hh, mm = text.split(':')
        try:
            h, m = int(hh), int(mm)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except Exception:
            await update.message.reply_text("שעה לא תקינה.")
            return AWAIT_REMINDER_TIME
        y, mo, d = context.user_data.get('pending_reminder_date', (None, None, None))
        item_id = context.user_data.get('pending_reminder_item_id')
        if not all([y, mo, d, item_id]):
            await update.message.reply_text("אין פריט/תאריך ממתינים.")
            return SELECTING_ACTION
        remind_at = datetime(year=y, month=mo, day=d, hour=h, minute=m)
        ok = self.db.set_reminder(int(item_id), remind_at)
        if ok:
            await update.message.reply_text(f"⏰ נקבעה תזכורת ל-{d:02d}/{mo:02d}/{y} בשעה {h:02d}:{m:02d}.")
        else:
            await update.message.reply_text("שגיאה בקביעת תזכורת.")
        context.user_data.pop('pending_reminder_date', None)
        context.user_data.pop('pending_reminder_item_id', None)
        return SELECTING_ACTION

    # --- Activity Reporting Helper ---
    def _report(self, update: Update):
        reporter.report_activity(update.effective_user.id)

    # --- Main Menu and State Entrypoints ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        
        # Check for deep link parameters (share links)
        if context.args and len(context.args) > 0:
            param = context.args[0]
            if param.startswith('share_'):
                # Handle shared item
                token = param.replace('share_', '')
                return await self.handle_shared_item(update, context, token)
        
        username = update.effective_user.first_name
        welcome_text = f"שלום {username}! 👋\nברוך הבא לבוט 'שמור לי'.\nבחר פעולה מהתפריט:"
        keyboard = [
            [KeyboardButton("➕ הוסף תוכן"), KeyboardButton("🧩 איסוף טקסט רב-הודעות")],
            [KeyboardButton("🔍 חיפוש"), KeyboardButton("📚 הצג קטגוריות")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id=chat_id, text=welcome_text, reply_markup=reply_markup)
        return SELECTING_ACTION
    
    async def handle_shared_item(self, update: Update, context: ContextTypes.DEFAULT_TYPE, token: str) -> int:
        """Handle viewing a shared item via deep link"""
        chat_id = update.effective_chat.id
        
        # Get item by token
        item_data = self.share_handler.get_item_by_token(token)
        
        if not item_data:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ הקישור אינו תקף או שהפריט כבר לא משותף.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return await self.start(update, context)
        
        # Format and display the shared item
        message = self.share_handler.format_shared_item(item_data)
        
        # Create buttons for actions
        keyboard = []
        
        # If it's a text item, add copy button
        if item_data.get('content_type') == 'text' and item_data.get('content'):
            keyboard.append([InlineKeyboardButton("📋 העתק תוכן", callback_data=f"copy_shared_{token}")])
        
        # If it's a document, add download button
        if item_data.get('content_type') == 'document' and item_data.get('file_id'):
            keyboard.append([InlineKeyboardButton("📥 הורד קובץ", callback_data=f"download_shared_{token}")])
        
        keyboard.append([InlineKeyboardButton("🔙 תפריט ראשי", callback_data="main_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Send the shared item
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup
        )
        
        return SELECTING_ACTION

    async def ask_for_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        await update.message.reply_text("שלח לי את התוכן לשמירה:")
        return AWAIT_CONTENT

    async def start_multipart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        context.user_data['multipart_buffer'] = []
        keyboard = [[InlineKeyboardButton("✔️ סיום", callback_data="multipart_end")],
                    [InlineKeyboardButton("✖️ ביטול", callback_data="multipart_cancel")]]
        await update.message.reply_text("מצב איסוף הופעל. שלח כמה הודעות טקסט שתרצה, ואז לחץ '✔️ סיום'", reply_markup=InlineKeyboardMarkup(keyboard))
        return AWAIT_MULTIPART

    async def multipart_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        query = update.callback_query
        if query:
            await query.answer()
            if query.data == 'multipart_end':
                parts = context.user_data.get('multipart_buffer', [])
                text = "\n".join(parts).strip()
                context.user_data.pop('multipart_buffer', None)
                await query.edit_message_text("קיבלתי. שמור כעת כפריט רגיל.")
                if not text:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text="לא התקבל טקסט.")
                    return await self.start(update, context)
                # Continue to category selection as text item
                context.user_data['new_item'] = {'type': 'text', 'content': text}
                categories = self.db.get_user_categories(update.effective_user.id)
                keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in categories]
                keyboard.append([InlineKeyboardButton("🆕 קטגוריה חדשה", callback_data="cat_new")])
                await context.bot.send_message(chat_id=update.effective_chat.id, text="בחר קטגוריה:", reply_markup=InlineKeyboardMarkup(keyboard))
                return AWAIT_CATEGORY
            if query.data == 'multipart_cancel':
                context.user_data.pop('multipart_buffer', None)
                await query.edit_message_text("בוטל.")
                return await self.start(update, context)
        else:
            # receive a part
            if update.message and update.message.text:
                buf = context.user_data.get('multipart_buffer', [])
                buf.append(update.message.text)
                context.user_data['multipart_buffer'] = buf
                await update.message.reply_text(f"נוסף קטע. כרגע {len(buf)} קטעים. לחץ '✔️ סיום' כשאתה מוכן.")
                return AWAIT_MULTIPART
 
    async def upload_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        text = (
            "איך להעלות תוכן:\n\n"
            "1) קובץ מצורף: שלח כ-document/photo/video לפי הצורך.\n"
            "2) טקסט ארוך: לחץ על 'התחל איסוף טקסט' ושלח כמה הודעות, ואז סיים.\n"
        )
        keyboard = [[InlineKeyboardButton("התחל איסוף טקסט", callback_data="upload_start_multipart")],
                    [InlineKeyboardButton("סגור", callback_data="upload_close")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECTING_ACTION

    async def upload_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        query = update.callback_query
        if not query:
            return SELECTING_ACTION
        await query.answer()
        if query.data == 'upload_start_multipart':
            # Start multipart collection via callback
            context.user_data['multipart_buffer'] = []
            kb = [[InlineKeyboardButton("✔️ סיום", callback_data="multipart_end")],
                  [InlineKeyboardButton("✖️ ביטול", callback_data="multipart_cancel")]]
            await context.bot.send_message(chat_id=update.effective_chat.id, text="מצב איסוף הופעל. שלח הודעות טקסט ואז לחץ '✔️ סיום'", reply_markup=InlineKeyboardMarkup(kb))
            return AWAIT_MULTIPART
        if query.data == 'upload_close':
            try:
                await query.edit_message_text("נסגר.")
            except Exception:
                pass
            return SELECTING_ACTION
        return SELECTING_ACTION

    async def ask_for_search_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        await update.message.reply_text("מה לחפש?")
        return AWAIT_SEARCH

    # New: Ask for text to convert to Markdown
    async def ask_for_md_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        await update.message.reply_text("שלח את הטקסט להמרה לקובץ Markdown (.md):")
        return AWAIT_MD_TEXT

    # New: Convert received text to .md and send back
    async def convert_text_to_md_and_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        text = (update.message.text or '').strip()
        if not text:
            await update.message.reply_text("לא התקבל טקסט. שלח טקסט רגיל להמרה.")
            return AWAIT_MD_TEXT

        # Smart display based on length
        if len(text) <= PREVIEW_THRESHOLD_CHARS:
            preview_text, parse_mode = format_text_content_for_telegram(text)
            if parse_mode:
                await update.message.reply_text(preview_text, parse_mode=parse_mode)
            else:
                await update.message.reply_text(preview_text)
        elif len(text) <= VERY_LONG_THRESHOLD_CHARS:
            safe_limit = TELEGRAM_MAX_MESSAGE_CHARS if not ("FORCE_CODE_BLOCKS" in globals() and FORCE_CODE_BLOCKS) else max(1000, TELEGRAM_MAX_MESSAGE_CHARS - 100)
            for chunk in split_text_for_telegram(text, max_chars=safe_limit):
                chunk_text, chunk_parse_mode = format_text_content_for_telegram(chunk)
                if chunk_parse_mode:
                    await update.message.reply_text(chunk_text, parse_mode=chunk_parse_mode)
                else:
                    await update.message.reply_text(chunk_text)
        else:
            # Very long: show partial preview with safe Markdown attempt and fallback
            preview = text[:PREVIEW_THRESHOLD_CHARS]
            preview_text, parse_mode = format_text_content_for_telegram(preview)
            try:
                if parse_mode:
                    await update.message.reply_text(preview_text, parse_mode=parse_mode)
                else:
                    await update.message.reply_text(preview_text)
            except BadRequest:
                # Fallback to plain text if markdown entities are broken due to truncation
                await update.message.reply_text(preview)
            await update.message.reply_text("הטקסט ארוך מאוד, נשלח גם כקובץ להורדה.")

        # Additionally send as a .md file so the user can open with a Markdown viewer
        try:
            md_bytes = BytesIO(text.encode('utf-8'))
            md_bytes.name = f"note-{datetime.now(tz=LOCAL_TZ).strftime('%Y%m%d-%H%M%S')}.md"
            await update.message.reply_document(document=md_bytes, filename=md_bytes.name, caption="קובץ Markdown")
        except Exception:
            # Ignore file send failures; preview already sent
            pass

        # Prepare save flow like a regular save (user chooses category and subject)
        context.user_data['new_item'] = {'type': 'text', 'content': text}

        categories = self.db.get_user_categories(update.effective_user.id)
        keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in categories]
        keyboard.append([InlineKeyboardButton("🆕 קטגוריה חדשה", callback_data="cat_new")])
        await update.message.reply_text("בחר קטגוריה:", reply_markup=InlineKeyboardMarkup(keyboard))
        return AWAIT_CATEGORY

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
        note_text = "📝 ערוך הערה" if item.get('note') else "📝 הוסף הערה"
        keyboard = [
            [InlineKeyboardButton(pin_text, callback_data=f"pin_{item_id}")],
            [InlineKeyboardButton("🖊 ערוך נושא", callback_data=f"editsubject_{item_id}"), InlineKeyboardButton("✏️ ערוך תוכן", callback_data=f"edit_{item_id}")],
            [InlineKeyboardButton(note_text, callback_data=f"note_{item_id}"), InlineKeyboardButton("🕰️ תזכורת", callback_data=f"reminder_{item_id}")],
        ]

        # Content-aware action row
        content_type = item.get('content_type')
        file_id = item.get('file_id') or ''
        file_name = item.get('file_name') or ''
        text_content = item.get('content') or ''
        text_len = len(text_content)

        content_buttons_row_gist_share = []
        content_buttons_row_copy_download = []
        is_textual = content_type == 'text' or (content_type == 'document' and file_name.endswith('.md') and text_len > 0)
        if is_textual:
            # Preview button (separate row) for very long text
            if text_len > VERY_LONG_THRESHOLD_CHARS:
                keyboard.append([InlineKeyboardButton("👁️ תצוגה מקדימה", callback_data=f"preview_{item_id}")])

            # Internal share link then Gist (always consistent labels and order)
            content_buttons_row_gist_share.append(InlineKeyboardButton("צור קישור פנימי 🔗", callback_data=f"share_{item_id}"))
            content_buttons_row_gist_share.append(InlineKeyboardButton("Gist 🐙", callback_data=f"gist_{item_id}"))

            # Download row (copy all removed per request)
            content_buttons_row_copy_download.append(InlineKeyboardButton("📥 הורדה", callback_data=f"download_{item_id}"))
        elif content_type == 'document' and file_id:
            content_buttons_row_copy_download.append(InlineKeyboardButton("📥 הורדה", callback_data=f"download_{item_id}"))

        if content_buttons_row_gist_share:
            keyboard.append(content_buttons_row_gist_share)
        if content_buttons_row_copy_download:
            keyboard.append(content_buttons_row_copy_download)

        keyboard.append([InlineKeyboardButton("🗑️ מחק", callback_data=f"delete_{item_id}")])
        keyboard.append([InlineKeyboardButton("🔙 חזרה", callback_data="back_categories")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        chat_id = update_or_query.message.chat.id
        # Reset tracked content messages for current view
        context.user_data['open_item_content_message_ids'] = []
        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            context.user_data['open_item_meta_message_id'] = update_or_query.message.message_id
        else:
            meta_msg = await context.bot.send_message(chat_id=chat_id, text=metadata_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            context.user_data['open_item_meta_message_id'] = meta_msg.message_id

        content_type = item.get('content_type')
        if content_type == 'text' or (content_type == 'document' and (item.get('file_name', '').endswith('.md')) and (item.get('content') is not None and item.get('content') != '')):
            if text_len == 0:
                pass
            elif text_len <= PREVIEW_THRESHOLD_CHARS:
                text_to_send, parse_mode = format_text_content_for_telegram(text_content)
                if parse_mode:
                    sent = await context.bot.send_message(chat_id=chat_id, text=text_to_send, parse_mode=parse_mode)
                else:
                    sent = await context.bot.send_message(chat_id=chat_id, text=text_to_send)
                context.user_data['open_item_content_message_ids'].append(sent.message_id)
            elif text_len <= VERY_LONG_THRESHOLD_CHARS:
                if is_fenced_code_block(text_content):
                    code, lang = extract_fenced_code(text_content)
                    # Leave headroom for fences and language prefix
                    safe_limit = max(1000, TELEGRAM_MAX_MESSAGE_CHARS - 100)
                    for chunk in split_text_for_telegram(code, max_chars=safe_limit):
                        fenced = f"```{lang or ''}\n{chunk}\n```"
                        try:
                            sent = await context.bot.send_message(chat_id=chat_id, text=fenced, parse_mode=ParseMode.MARKDOWN_V2)
                        except BadRequest:
                            escaped = html.escape(chunk)
                            html_block = f"<pre><code>{escaped}</code></pre>"
                            sent = await context.bot.send_message(chat_id=chat_id, text=html_block, parse_mode=ParseMode.HTML)
                        context.user_data['open_item_content_message_ids'].append(sent.message_id)
                else:
                    safe_limit = TELEGRAM_MAX_MESSAGE_CHARS if not ("FORCE_CODE_BLOCKS" in globals() and FORCE_CODE_BLOCKS) else max(1000, TELEGRAM_MAX_MESSAGE_CHARS - 100)
                    for chunk in split_text_for_telegram(text_content, max_chars=safe_limit):
                        chunk_text, chunk_parse_mode = format_text_content_for_telegram(chunk)
                        if chunk_parse_mode:
                            sent = await context.bot.send_message(chat_id=chat_id, text=chunk_text, parse_mode=chunk_parse_mode)
                        else:
                            sent = await context.bot.send_message(chat_id=chat_id, text=chunk_text)
                        context.user_data['open_item_content_message_ids'].append(sent.message_id)
            else:
                sent = await context.bot.send_message(chat_id=chat_id, text="התוכן ארוך מאוד. השתמש בכפתור 'הורדה'.")
                context.user_data['open_item_content_message_ids'].append(sent.message_id)
        elif content_type and item.get('file_id'):
            send_map = {'photo': context.bot.send_photo, 'document': context.bot.send_document, 'video': context.bot.send_video, 'voice': context.bot.send_voice}
            if content_type in send_map:
                await send_map[content_type](chat_id=chat_id, **{content_type: item['file_id'], 'caption': item.get('caption', '')})

    # --- All other class methods from your bot logic go here ---
    # (show_categories, handle_search, receive_content, receive_category, save_note, etc.)
    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._report(update)
        categories = self.db.get_user_categories(update.effective_user.id)
        chat_id = update.effective_chat.id
        if not categories:
            await context.bot.send_message(chat_id=chat_id, text="אין קטגוריות עדיין.")
            return
        keyboard = [[InlineKeyboardButton(f"{cat} ({self.db.get_category_count(update.effective_user.id, cat)})", callback_data=f"showcat_{cat}")] for cat in categories]
        await context.bot.send_message(chat_id=chat_id, text="בחר קטגוריה להצגה:", reply_markup=InlineKeyboardMarkup(keyboard))

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
        # Offer to set a reminder now
        kb = [
            [
                InlineKeyboardButton("1ש׳", callback_data=f"remset_{item_id}_1"),
                InlineKeyboardButton("3ש׳", callback_data=f"remset_{item_id}_3"),
                InlineKeyboardButton("24ש׳", callback_data=f"remset_{item_id}_24"),
            ],
            [
                InlineKeyboardButton("בחר תאריך…", callback_data=f"remdate_{item_id}"),
                InlineKeyboardButton("מותאם שעות…", callback_data=f"remcustom_{item_id}"),
                InlineKeyboardButton("דלג", callback_data=f"remignore_{item_id}")
            ]
        ]
        await update.message.reply_text("להוסיף תזכורת לפריט הזה?", reply_markup=InlineKeyboardMarkup(kb))

        # Keep item_id for potential custom input
        context.user_data['pending_reminder_item_id'] = item_id

        # Also show item actions
        await self.show_item_with_actions(update, context, item_id)

        # Clean up content buffer only (keep pending_reminder_item_id)
        del context.user_data['new_item']
        return SELECTING_ACTION

    async def save_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        item_id = context.user_data.get('action_item_id')
        if not item_id: return await self.start(update, context)
        self.db.update_note(item_id, update.message.text)
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['action_item_id']
        return SELECTING_ACTION

    async def item_action_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        query = update.callback_query; await query.answer()
        # Support actions without underscore (e.g., back_categories)
        if '_' in query.data:
            action, item_id_str = query.data.split('_', 1)
        else:
            action, item_id_str = query.data, ''
        # Some actions encode more data after the id (e.g., remset_{id}_{hours})
        # Defer strict parsing until needed
        try:
            item_id = int(item_id_str)
        except ValueError:
            item_id = None

        if action in ['showitem', 'pin', 'delete']:
            if item_id is None:
                try:
                    item_id = int(item_id_str)
                except Exception:
                    return SELECTING_ACTION
            if action == 'pin': self.db.toggle_pin(item_id)
            if action == 'delete': self.db.delete_item(item_id); await query.edit_message_text("✅ הפריט נמחק."); return SELECTING_ACTION
            await self.show_item_with_actions(query, context, item_id)
            return SELECTING_ACTION

        # New: content operations
        if action in ['preview', 'download']:
            if item_id is None:
                try:
                    item_id = int(item_id_str)
                except Exception:
                    return SELECTING_ACTION
            item = self.db.get_item(item_id)
            if not item:
                await query.edit_message_text("הפריט לא קיים עוד.")
                return SELECTING_ACTION
            chat_id = query.message.chat.id

            if action == 'preview':
                text = item.get('content') or ''
                if not text:
                    await context.bot.send_message(chat_id=chat_id, text="אין תוכן להצגה.")
                    return SELECTING_ACTION
                # Try formatted preview with safe fallback to plain text
                preview = text[:PREVIEW_THRESHOLD_CHARS]
                preview_text, parse_mode = format_text_content_for_telegram(preview)
                try:
                    if parse_mode:
                        await context.bot.send_message(chat_id=chat_id, text=preview_text, parse_mode=parse_mode)
                    else:
                        await context.bot.send_message(chat_id=chat_id, text=preview_text)
                except BadRequest:
                    await context.bot.send_message(chat_id=chat_id, text=preview)
                if len(text) > PREVIEW_THRESHOLD_CHARS:
                    await context.bot.send_message(chat_id=chat_id, text="... המשך הושמט בתצוגה מקדימה. השתמש ב'הורדה'.")
                return SELECTING_ACTION

            # copyall removed per request

            # copycode no longer needed; using native code rendering in show/copyall flows

            if action == 'download':
                if item.get('file_id') and item.get('content_type') == 'document':
                    try:
                        await context.bot.send_document(chat_id=chat_id, document=item['file_id'], caption=item.get('caption', ''))
                    except Exception:
                        await context.bot.send_message(chat_id=chat_id, text="שגיאה בשליחת הקובץ המקורי. נשלח כטקסט.")
                        text = item.get('content') or ''
                        if text:
                            md_bytes = BytesIO(text.encode('utf-8'))
                            md_bytes.name = item.get('file_name') or f"note-{datetime.now(tz=LOCAL_TZ).strftime('%Y%m%d-%H%M%S')}.txt"
                            await context.bot.send_document(chat_id=chat_id, document=md_bytes, filename=md_bytes.name)
                else:
                    text = item.get('content') or ''
                    if not text:
                        await context.bot.send_message(chat_id=chat_id, text="אין תוכן להורדה.")
                        return SELECTING_ACTION
                    md_bytes = BytesIO(text.encode('utf-8'))
                    looks_md = '```' in text or re.search(r'(^|\n)#{1,6}\s', text)
                    ext = 'md' if looks_md else 'txt'
                    md_bytes.name = item.get('file_name') or f"note-{datetime.now(tz=LOCAL_TZ).strftime('%Y%m%d-%H%M%S')}.{ext}"
                    await context.bot.send_document(chat_id=chat_id, document=md_bytes, filename=md_bytes.name)
                return SELECTING_ACTION

        # Back button from item view to categories list
        if query.data == 'back_categories' or action == 'back_categories':
            # Best effort: delete content messages to clean view
            try:
                ids = context.user_data.get('open_item_content_message_ids', [])
                for mid in ids:
                    try:
                        await context.bot.delete_message(chat_id=query.message.chat.id, message_id=mid)
                    except Exception:
                        pass
                context.user_data['open_item_content_message_ids'] = []
            except Exception:
                pass
            # Replace the metadata message with categories list
            await self.show_categories(update, context)
            try:
                await query.delete_message()
            except Exception:
                pass
            return SELECTING_ACTION

        # Reminder menu entry from item view
        if action == 'reminder':
            if item_id is None:
                try:
                    item_id = int(item_id_str)
                except Exception:
                    return SELECTING_ACTION
            kb = [
                [
                    InlineKeyboardButton("1ש׳", callback_data=f"remset_{item_id}_1"),
                    InlineKeyboardButton("3ש׳", callback_data=f"remset_{item_id}_3"),
                    InlineKeyboardButton("24ש׳", callback_data=f"remset_{item_id}_24"),
                ],
                [
                    InlineKeyboardButton("בחר תאריך…", callback_data=f"remdate_{item_id}"),
                    InlineKeyboardButton("מותאם שעות…", callback_data=f"remcustom_{item_id}"),
                    InlineKeyboardButton("בטל תזכורת", callback_data=f"remclear_{item_id}"),
                ]
            ]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
            return SELECTING_ACTION

        # Quick-set reminder buttons
        if action.startswith('remset'):
            # pattern: remset_{itemId}_{hours}
            try:
                parts = item_id_str.split('_')
                item_id = int(parts[0])
                hours = int(parts[1])
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat.id, text="שגיאת תזכורת.")
                return SELECTING_ACTION
            hours = max(MIN_REMINDER_HOURS, min(MAX_REMINDER_HOURS, hours))
            remind_at = datetime.now(tz=LOCAL_TZ).replace(tzinfo=None) + timedelta(hours=hours)
            ok = self.db.set_reminder(item_id, remind_at)
            if ok:
                await context.bot.send_message(chat_id=query.message.chat.id, text=f"⏰ נקבעה תזכורת בעוד {hours} שעות.")
            else:
                await context.bot.send_message(chat_id=query.message.chat.id, text="שגיאה בקביעת תזכורת.")
            return SELECTING_ACTION

        # Open date calendar
        if action.startswith('remdate'):
            try:
                item_id = int(item_id_str)
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat.id, text="שגיאת תזכורת.")
                return SELECTING_ACTION
            await self.open_calendar(query, context, item_id)
            return SELECTING_ACTION

        # Open custom reminder input
        if action.startswith('remcustom'):
            try:
                item_id = int(item_id_str)
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat.id, text="שגיאת תזכורת.")
                return SELECTING_ACTION
            context.user_data['pending_reminder_item_id'] = item_id
            await context.bot.send_message(chat_id=query.message.chat.id, text=f"הקלד מספר שעות (בין {MIN_REMINDER_HOURS}-{MAX_REMINDER_HOURS}):")
            return AWAIT_REMINDER_HOURS

        # Clear reminder for item
        if action.startswith('remclear'):
            try:
                item_id = int(item_id_str)
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat.id, text="שגיאת תזכורת.")
                return SELECTING_ACTION
            self.db.clear_reminder(item_id)
            await context.bot.send_message(chat_id=query.message.chat.id, text="התזכורת בוטלה.")
            return SELECTING_ACTION

        # Ignore post-save prompt
        if action.startswith('remignore'):
            await context.bot.send_message(chat_id=query.message.chat.id, text="לא נקבעה תזכורת.")
            return SELECTING_ACTION
        
        # Handle GitHub Gist creation
        if action.startswith('gist'):
            return await self.handle_gist_creation(update, context)
        
        # Handle internal share link creation
        if action.startswith('share'):
            return await self.handle_share_creation(update, context)
        
        # Handle unshare
        if action.startswith('unshare'):
            return await self.handle_unshare(update, context)

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
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['action_item_id']
        return SELECTING_ACTION

    async def setup_github(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """הגדרת GitHub token"""
        self._report(update)
        user_id = update.effective_user.id
        
        # Check if token already exists
        existing_token = self.gist_handler.get_user_token(user_id)
        if existing_token:
            keyboard = [
                [InlineKeyboardButton("🔄 החלף Token", callback_data="github_replace")],
                [InlineKeyboardButton("❌ הסר Token", callback_data="github_remove")],
                [InlineKeyboardButton("❌ ביטול", callback_data="cancel")]
            ]
            await update.message.reply_text(
                "🐙 כבר הגדרת GitHub token.\n"
                "מה תרצה לעשות?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                "🐙 **הגדרת GitHub Token**\n\n"
                "כדי ליצור Gists, אני צריך GitHub Personal Access Token.\n\n"
                "**איך יוצרים Token:**\n"
                "1. היכנס ל-GitHub.com\n"
                "2. לך ל-Settings → Developer settings → Personal access tokens → Tokens (classic)\n"
                "3. לחץ על 'Generate new token (classic)'\n"
                "4. תן לו שם (למשל: SaveMe Bot)\n"
                "5. סמן את ההרשאה: `gist` (Create gists)\n"
                "6. לחץ 'Generate token'\n"
                "7. העתק את ה-token ושלח לי אותו\n\n"
                "⚠️ **חשוב:** שמור את ה-token במקום בטוח, הוא יוצג רק פעם אחת!\n"
                "🔒 ה-token יישמר מוצפן ולא ישותף עם אף אחד.\n\n"
                "שלח את ה-token או /cancel לביטול:",
                parse_mode=ParseMode.MARKDOWN
            )
            return AWAIT_GITHUB_TOKEN
        
        return SELECTING_ACTION
    
    async def handle_github_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בקבלת GitHub token"""
        self._report(update)
        user_id = update.effective_user.id
        token = update.message.text.strip()
        
        # Delete the message with the token for security
        try:
            await update.message.delete()
        except:
            pass
        
        # Validate and store token
        if self.gist_handler.set_user_token(user_id, token):
            username = self.db.get_user_settings(user_id).get('github_username', 'Unknown')
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ **Token נשמר בהצלחה!**\n"
                f"👤 משתמש GitHub: @{username}\n\n"
                f"עכשיו תוכל ליצור Gists מהקוד ששמרת.\n"
                f"פשוט לחץ על 'Create Gist' בכל פריט טקסט או קוד!",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ **Token לא תקין**\n\n"
                "בדוק שהעתקת את ה-token במלואו ונסה שוב.\n"
                "אם הבעיה נמשכת, ייתכן שה-token פג תוקף או חסרות הרשאות.",
                parse_mode=ParseMode.MARKDOWN
            )
        
        return await self.start(update, context)
    
    async def handle_github_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בפעולות GitHub מהתפריט"""
        self._report(update)
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        action = query.data
        
        if action == "github_replace":
            await query.edit_message_text(
                "🔄 **החלפת GitHub Token**\n\n"
                "שלח את ה-token החדש או /cancel לביטול:",
                parse_mode=ParseMode.MARKDOWN
            )
            return AWAIT_GITHUB_TOKEN
        
        elif action == "github_remove":
            if self.gist_handler.remove_user_token(user_id):
                await query.edit_message_text("✅ ה-token הוסר בהצלחה.")
            else:
                await query.edit_message_text("❌ שגיאה בהסרת ה-token.")
            return await self.start(update, context)
        
        elif action == "cancel":
            await query.edit_message_text("❌ הפעולה בוטלה.")
            return await self.start(update, context)
        
        elif action == "setup_github_now":
            await query.edit_message_text(
                "🐙 **הגדרת GitHub Token**\n\n"
                "כדי ליצור Gists, אני צריך GitHub Personal Access Token.\n\n"
                "**איך יוצרים Token:**\n"
                "1. היכנס ל-GitHub.com\n"
                "2. לך ל-Settings → Developer settings → Personal access tokens → Tokens (classic)\n"
                "3. לחץ על 'Generate new token (classic)'\n"
                "4. תן לו שם (למשל: SaveMe Bot)\n"
                "5. סמן את ההרשאה: `gist` (Create gists)\n"
                "6. לחץ 'Generate token'\n"
                "7. העתק את ה-token ושלח לי אותו\n\n"
                "⚠️ **חשוב:** שמור את ה-token במקום בטוח, הוא יוצג רק פעם אחת!\n"
                "🔒 ה-token יישמר מוצפן ולא ישותף עם אף אחד.\n\n"
                "שלח את ה-token או /cancel לביטול:",
                parse_mode=ParseMode.MARKDOWN
            )
            return AWAIT_GITHUB_TOKEN
        
        return SELECTING_ACTION
    
    async def handle_gist_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול ביצירת Gist"""
        self._report(update)
        query = update.callback_query
        await query.answer()
        
        # Extract item_id from callback data
        item_id = int(query.data.replace('gist_', ''))
        user_id = update.effective_user.id
        
        # Check if user has GitHub token
        if not self.gist_handler.get_user_token(user_id):
            keyboard = [[InlineKeyboardButton("🔧 הגדר GitHub Token", callback_data="setup_github_now")]]
            await query.edit_message_text(
                "❌ **לא הגדרת GitHub Token**\n\n"
                "כדי ליצור Gists, תצטרך להגדיר token.\n"
                "השתמש בפקודה /setup_github",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return SELECTING_ACTION
        
        # Store item_id for later use
        context.user_data['gist_item_id'] = item_id
        
        # Ask if public or private
        keyboard = [
            [InlineKeyboardButton("🌍 ציבורי", callback_data="gist_public")],
            [InlineKeyboardButton("🔒 פרטי", callback_data="gist_private")],
            [InlineKeyboardButton("❌ ביטול", callback_data="gist_cancel")]
        ]
        
        await query.edit_message_text(
            "🐙 **יצירת GitHub Gist**\n\n"
            "האם ברצונך ליצור Gist ציבורי או פרטי?\n\n"
            "• **ציבורי** \- כל אחד יכול לראות \(מופיע בחיפוש\)\n"
            "• **פרטי** \- רק מי שיש לו את הקישור",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        return AWAIT_GIST_CONFIRM
    
    async def handle_gist_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """אישור ויצירת Gist"""
        self._report(update)
        query = update.callback_query
        await query.answer()
        
        action = query.data
        item_id = context.user_data.get('gist_item_id')
        user_id = update.effective_user.id
        
        if action == "gist_cancel":
            await query.edit_message_text("❌ יצירת Gist בוטלה.")
            del context.user_data['gist_item_id']
            return await self.start(update, context)
        
        public = (action == "gist_public")
        
        # Create the gist
        await query.edit_message_text("⏳ יוצר Gist...")
        
        result = self.gist_handler.create_gist_from_item(user_id, item_id, public=public)
        
        if result and result.get('success'):
            gist_url = result['url']
            visibility = "ציבורי 🌍" if public else "פרטי 🔒"
            
            keyboard = [[InlineKeyboardButton("🔗 פתח ב-GitHub", url=gist_url)]]
            
            # Escape dynamic values for safe MarkdownV2
            safe_filename = escape_markdown(result.get('filename', 'file'))
            safe_url = escape_markdown(gist_url)
            safe_visibility = escape_markdown(visibility)
            
            try:
                await query.edit_message_text(
                    "✅ **Gist נוצר בהצלחה\!**\n\n"
                    f"📝 קובץ: {safe_filename}\n"
                    f"🔐 סוג: {safe_visibility}\n"
                    f"🔗 קישור: {safe_url}\n\n"
                    "הקישור נשמר עם הפריט.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except BadRequest:
                html_filename = html.escape(result.get('filename', 'file'))
                html_url = html.escape(gist_url)
                html_visibility = html.escape(visibility)
                await query.edit_message_text(
                    "✅ <b>Gist נוצר בהצלחה!</b>\n\n"
                    f"📝 קובץ: {html_filename}\n"
                    f"🔐 סוג: {html_visibility}\n"
                    f"🔗 קישור: {html_url}\n\n"
                    "הקישור נשמר עם הפריט.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
        else:
            error_msg = result.get('error', 'שגיאה לא ידועה') if result else 'שגיאה לא ידועה'
            safe_error = escape_markdown(str(error_msg))
            try:
                await query.edit_message_text(
                    "❌ **שגיאה ביצירת Gist:**\n" + safe_error,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except BadRequest:
                await query.edit_message_text(
                    "❌ <b>שגיאה ביצירת Gist:</b>\n" + html.escape(str(error_msg)),
                    parse_mode=ParseMode.HTML
                )
        
        del context.user_data['gist_item_id']
        return await self.start(update, context)
    
    async def handle_share_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול ביצירת קישור פנימי לשיתוף"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        # Extract item ID from callback data
        item_id = int(query.data.replace('share_', ''))
        
        # Check if item already has a share link
        share_info = self.db.get_item_share_info(item_id)
        
        if share_info and share_info.get('token'):
            # Item already has a share link
            share_url = self.share_handler.get_share_link(item_id)
            
            keyboard = [
                [InlineKeyboardButton("📋 העתק קישור", url=share_url)],
                [InlineKeyboardButton("🗑️ הסר קישור", callback_data=f"unshare_{item_id}")],
                [InlineKeyboardButton("🔙 חזור", callback_data=f"showitem_{item_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            safe_url = escape_markdown(share_url)
            
            await query.edit_message_text(
                f"📤 **קישור שיתוף פנימי קיים\!**\n\n"
                f"הפריט כבר משותף בקישור:\n"
                f"```\n{safe_url}\n```\n"
                f"ניתן לפתוח את הקישור או להסיר את השיתוף.",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup
            )
        else:
            # Create new share link
            result = self.share_handler.create_share_link(item_id)
            
            if result:
                share_url = result['url']
                
                keyboard = [
                    [InlineKeyboardButton("🔗 פתח קישור", url=share_url)],
                    [InlineKeyboardButton("🗑️ הסר קישור", callback_data=f"unshare_{item_id}")],
                    [InlineKeyboardButton("🔙 חזור", callback_data=f"showitem_{item_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                safe_url = escape_markdown(share_url)
                
                await query.edit_message_text(
                    f"✅ **קישור שיתוף פנימי נוצר בהצלחה\!**\n\n"
                    f"📤 הקישור לשיתוף:\n"
                    f"```\n{safe_url}\n```\n"
                    f"שתף את הקישור עם כל מי שתרצה\!\n"
                    f"הם יוכלו לצפות בתוכן ולהעתיק אותו\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    "❌ שגיאה ביצירת קישור השיתוף.\nנסה שוב מאוחר יותר.",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        
        return SELECTING_ACTION
    
    async def handle_unshare(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """טיפול בהסרת קישור שיתוף"""
        query = update.callback_query
        await query.answer()
        
        # Extract item ID from callback data
        item_id = int(query.data.replace('unshare_', ''))
        
        if self.share_handler.remove_share_link(item_id):
            await query.edit_message_text(
                "✅ קישור השיתוף הוסר בהצלחה.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await query.edit_message_text(
                "❌ שגיאה בהסרת קישור השיתוף.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        
        return SELECTING_ACTION
    
    async def handle_shared_item_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle actions on shared items"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "main_menu":
            # Return to main menu
            await query.delete_message()
            return await self.start(update, context)
        
        if data.startswith("copy_shared_"):
            token = data.replace("copy_shared_", "")
            item_data = self.share_handler.get_item_by_token(token)
            
            if item_data and item_data.get('content'):
                content = item_data['content']
                # Send content as a copyable message
                if len(content) <= TELEGRAM_MAX_MESSAGE_CHARS:
                    # Escape backticks in content for code block
                    escaped_content = content.replace('`', '\\`')
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text=f"```\n{escaped_content}\n```",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    await query.answer("התוכן נשלח - ניתן להעתיק אותו")
                else:
                    # Send as file if too long
                    import io
                    file_name = f"shared_content_{token[:8]}.txt"
                    file_bytes = content.encode('utf-8')
                    await context.bot.send_document(
                        chat_id=query.message.chat.id,
                        document=io.BytesIO(file_bytes),
                        filename=file_name,
                        caption="התוכן ארוך מדי - מצורף כקובץ"
                    )
                    await query.answer("התוכן נשלח כקובץ")
            else:
                await query.answer("לא ניתן להעתיק את התוכן", show_alert=True)
        
        elif data.startswith("download_shared_"):
            token = data.replace("download_shared_", "")
            item_data = self.share_handler.get_item_by_token(token)
            
            if item_data and item_data.get('file_id'):
                try:
                    await context.bot.send_document(
                        chat_id=query.message.chat.id,
                        document=item_data['file_id'],
                        caption=f"📥 קובץ משותף: {item_data.get('file_name', 'document')}"
                    )
                    await query.answer("הקובץ נשלח")
                except Exception as e:
                    logger.error(f"Error sending shared file: {e}")
                    # Try sending content as file if file_id failed
                    if item_data.get('content'):
                        import io
                        file_name = item_data.get('file_name', f"shared_{token[:8]}.txt")
                        file_bytes = item_data['content'].encode('utf-8')
                        await context.bot.send_document(
                            chat_id=query.message.chat.id,
                            document=io.BytesIO(file_bytes),
                            filename=file_name,
                            caption=f"📥 תוכן הקובץ המשותף: {file_name}"
                        )
                        await query.answer("התוכן נשלח כקובץ")
                    else:
                        await query.answer("לא ניתן להוריד את הקובץ", show_alert=True)
            else:
                await query.answer("לא נמצא קובץ להורדה", show_alert=True)
        
        return SELECTING_ACTION

    async def save_edited_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._report(update)
        item_id = context.user_data.get('action_item_id')
        if not item_id:
            return await self.start(update, context)
        
        # קבלת סוג התוכן החדש והכנת נתונים לעדכון בבסיס הנתונים
        content = ''
        file_id = ''
        file_name = ''
        caption = update.message.caption or ''

        if update.message.text:
            content_type = 'text'
            content = update.message.text.strip()
        elif update.message.document:
            content_type = 'document'
            doc = update.message.document
            file_id = doc.file_id
            file_name = doc.file_name or ''
        elif update.message.photo:
            content_type = 'photo'
            photo = update.message.photo[-1]  # הגודל הגדול ביותר
            file_id = photo.file_id
        elif update.message.video:
            content_type = 'video'
            vid = update.message.video
            file_id = vid.file_id
        elif update.message.voice:
            content_type = 'voice'
            voc = update.message.voice
            file_id = voc.file_id
        elif update.message.audio:
            # אין טיפול ייעודי ב-audio; נשמור כ-document כדי לאפשר שליחה כהעלאת מסמך
            content_type = 'document'
            aud = update.message.audio
            file_id = aud.file_id
            try:
                file_name = getattr(aud, 'file_name', '') or ''
            except Exception:
                file_name = ''
        else:
            await update.message.reply_text("❌ סוג תוכן לא נתמך.")
            return await self.start(update, context)
        
        # עדכון התוכן בבסיס הנתונים
        self.db.update_content(item_id, content_type, content, file_id, file_name, caption)
        
        await self.show_item_with_actions(update, context, item_id)
        del context.user_data['action_item_id']
        # הישאר בתצוגה הנוכחית ללא הודעת ברוך הבא מיותרת
        return SELECTING_ACTION

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

    # Bot commands setup skipped to keep main() synchronous

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', bot.start), CommandHandler('tomd', bot.ask_for_md_text), CommandHandler('upload', bot.upload_help), CommandHandler('setup_github', bot.setup_github)],
        states={
                         SELECTING_ACTION: [
                MessageHandler(filters.TEXT & filters.Regex('^➕ הוסף תוכן$'), bot.ask_for_content),
                # Removed Markdown conversion command from main menu
                MessageHandler(filters.TEXT & filters.Regex('^🧩 איסוף טקסט רב-הודעות$'), bot.start_multipart),
                MessageHandler(filters.TEXT & filters.Regex('^🔍 חיפוש$'), bot.ask_for_search_query),
                MessageHandler(filters.TEXT & filters.Regex('^📚 הצג קטגוריות$'), bot.show_categories),
                # Removed settings from main menu
                CallbackQueryHandler(bot.show_category_items, pattern="^showcat_"),
                CallbackQueryHandler(bot.upload_router, pattern="^(upload_start_multipart|upload_close)$"),
                CallbackQueryHandler(bot.item_action_router, pattern="^(showitem_|pin_|delete_|note_|edit_|editsubject_|preview_|download_|reminder_|remset_|remdate_|remcustom_|remclear_|remignore_|gist_|share_|unshare_|back_categories)" ),
                CallbackQueryHandler(bot.handle_shared_item_action, pattern="^(copy_shared_|download_shared_|main_menu)"),
                CallbackQueryHandler(bot.handle_github_action, pattern="^(github_replace|github_remove|cancel|setup_github_now)$"),
                CallbackQueryHandler(bot.calendar_router, pattern="^(cal_|calpick_|time_|time_custom|remcancel_)"),
            ],
            AWAIT_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.receive_content)],
            AWAIT_CATEGORY: [CallbackQueryHandler(bot.receive_category, pattern="^cat_"), MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_category)],
            AWAIT_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_subject_and_save)],
            AWAIT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_search_query)],
            AWAIT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.save_note)],
            AWAIT_EDIT: [MessageHandler(filters.ALL & ~filters.COMMAND, bot.save_edited_content)],
            AWAIT_SUBJECT_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.save_edited_subject)],
            AWAIT_MD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.convert_text_to_md_and_send)],
            AWAIT_MULTIPART: [
                CallbackQueryHandler(bot.multipart_router, pattern='^(multipart_end|multipart_cancel)$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.multipart_router)
            ],
            AWAIT_REMINDER_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.reminder_hours_input)],
            AWAIT_REMINDER_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.reminder_time_input)],
            AWAIT_GITHUB_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_github_token)],
            AWAIT_GIST_CONFIRM: [CallbackQueryHandler(bot.handle_gist_confirm, pattern="^gist_(public|private|cancel)$")]
        },
        fallbacks=[CommandHandler('cancel', bot.cancel)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)

    # Start periodic reminder job (every minute)
    try:
        application.job_queue.run_repeating(bot.reminder_tick, interval=60, first=10)
    except Exception:
        pass

    logger.info("Bot is starting to poll...")
    application.run_polling()

if __name__ == '__main__':
    main()
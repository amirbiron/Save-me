"""
Internal Share Link Handler for SaveMe Bot
מטפל ביצירת קישורים פנימיים לשיתוף פריטים
"""

import logging
from typing import Dict, Optional
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

logger = logging.getLogger(__name__)

class InternalShareHandler:
    """Handler for creating and managing internal share links"""
    
    def __init__(self, db):
        """Initialize the share handler with database connection"""
        self.db = db
        self.bot_username = Config.BOT_USERNAME
    
    def create_share_link(self, item_id: int) -> Optional[Dict[str, str]]:
        """
        Create an internal share link for an item
        
        Args:
            item_id: ID of the item to share
            
        Returns:
            Dict with share info or None if failed
        """
        try:
            # Get or create share token
            token = self.db.create_share_token(item_id)
            
            if token:
                # Create Telegram deep link
                share_url = f"https://t.me/{self.bot_username}?start=share_{token}"
                
                return {
                    "url": share_url,
                    "token": token,
                    "message": "קישור שיתוף נוצר בהצלחה!"
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to create share link: {e}")
            return None
    
    def get_share_link(self, item_id: int) -> Optional[str]:
        """
        Get existing share link for an item
        
        Args:
            item_id: ID of the item
            
        Returns:
            Share URL or None if not shared
        """
        try:
            share_info = self.db.get_item_share_info(item_id)
            
            if share_info and share_info.get('token'):
                return f"https://t.me/{self.bot_username}?start=share_{share_info['token']}"
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get share link: {e}")
            return None
    
    def remove_share_link(self, item_id: int) -> bool:
        """
        Remove share link from an item
        
        Args:
            item_id: ID of the item
            
        Returns:
            True if successful, False otherwise
        """
        try:
            return self.db.remove_share_token(item_id)
            
        except Exception as e:
            logger.error(f"Failed to remove share link: {e}")
            return False
    
    def get_item_by_token(self, token: str) -> Optional[Dict]:
        """
        Get item data by share token
        
        Args:
            token: Share token
            
        Returns:
            Item data or None if not found
        """
        try:
            return self.db.get_item_by_token(token)
            
        except Exception as e:
            logger.error(f"Failed to get item by token: {e}")
            return None
    
    def format_shared_item(self, item_data: Dict) -> str:
        """
        Format shared item for display
        
        Args:
            item_data: Item data from database
            
        Returns:
            Formatted message string
        """
        try:
            # Helper function to escape markdown v2
            def escape_md(text):
                """Escape special characters for Telegram MarkdownV2"""
                if not text:
                    return ""
                special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
                for char in special_chars:
                    text = text.replace(char, f'\\{char}')
                return text
            
            category = escape_md(item_data.get('category', 'Unknown'))
            subject = escape_md(item_data.get('subject', 'No subject'))
            content_type = item_data.get('content_type', 'text')
            content = item_data.get('content', '')
            note = escape_md(item_data.get('note', ''))
            created_at = escape_md(item_data.get('created_at', ''))
            
            message = f"📤 **פריט משותף**\n\n"
            message += f"📁 קטגוריה: {category}\n"
            message += f"📌 נושא: {subject}\n"
            message += f"📅 נוצר: {created_at}\n"
            
            if note:
                message += f"📝 הערה: {note}\n"
            
            message += "\n" + "\\-" * 20 + "\n\n"
            
            if content_type == 'text' and content:
                # For text content, show it as a code block for easy copy/view
                # Preserve existing fenced blocks if present
                stripped = content.strip()
                if stripped.startswith('```') and stripped.endswith('```'):
                    # Already fenced - include as is
                    block = stripped
                else:
                    # Escape backticks to avoid breaking the fence in MarkdownV2
                    safe_content = content.replace('`', '\\`')
                    block = f"```\n{safe_content}\n```"

                # Telegram has message length limits; keep a safe preview size
                if len(block) > 3500:
                    message += block[:3500] + "\n\\.\\.\\.\n"
                    message += "\\[תוכן חתוך \\- השתמש בכפתור ההורדה או ההעתקה\\]"
                else:
                    message += block
            elif content_type == 'document':
                file_name = escape_md(item_data.get('file_name', 'document'))
                message += f"📎 קובץ מצורף: {file_name}"
                if content:
                    escaped_content = escape_md(content)
                    if len(escaped_content) > 1000:
                        message += f"\n\nתוכן הקובץ:\n{escaped_content[:1000]}\\.\\.\\."
                    else:
                        message += f"\n\nתוכן הקובץ:\n{escaped_content}"
            elif content_type in ['photo', 'video', 'audio']:
                message += f"🎯 סוג תוכן: {escape_md(content_type)}"
                caption = escape_md(item_data.get('caption', ''))
                if caption:
                    message += f"\n📝 כיתוב: {caption}"
            
            return message
            
        except Exception as e:
            logger.error(f"Failed to format shared item: {e}")
            return "שגיאה בטעינת הפריט המשותף"
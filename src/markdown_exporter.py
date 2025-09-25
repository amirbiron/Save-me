"""
מודול לייצוא פריטים שמורים לפורמט Markdown
כולל עיצוב מתקדם, סטטיסטיקות, ותוכן עניינים
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from io import BytesIO
import logging

logger = logging.getLogger(__name__)

class MarkdownExporter:
    """מחלקה לייצוא פריטים לפורמט Markdown"""
    
    def __init__(self, timezone: str = 'Asia/Jerusalem'):
        self.timezone = ZoneInfo(timezone)
        self.language_icons = {
            'python': '🐍',
            'javascript': '🟨', 
            'java': '☕',
            'cpp': '⚡',
            'c': '🔧',
            'html': '🌐',
            'css': '🎨',
            'sql': '🗃️',
            'bash': '💻',
            'json': '📋',
            'yaml': '📄',
            'markdown': '📝',
            'go': '🐹',
            'rust': '🦀',
            'php': '🐘',
            'ruby': '💎',
            'typescript': '🔷',
            'swift': '🦉',
            'kotlin': '🟣',
            'dockerfile': '🐳',
            'xml': '📰',
            'ini': '⚙️',
            'toml': '🔧',
            'csharp': '🔵'
        }
    
    def get_language_icon(self, language: str) -> str:
        """מחזיר אייקון לשפת תכנות"""
        if not language:
            return '📄'
        return self.language_icons.get(language.lower(), '📄')
    
    def detect_code_language(self, text: str) -> Optional[str]:
        """זיהוי אוטומטי של שפת התכנות בטקסט"""
        try:
            sample = text.strip()
            if not sample:
                return None
                
            first_line = sample.splitlines()[0] if sample else ""
            
            # Shebang detection
            if re.match(r'^#!/bin/(bash|sh)', sample):
                return 'bash'
            if re.match(r'^#!/usr/bin/env python', sample):
                return 'python'
            if re.match(r'^#!/usr/bin/env node', sample):
                return 'javascript'
                
            # Language-specific patterns
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
            if re.search(r'\b(interface|type|namespace|declare)\b|:\s*(string|number|boolean)', sample):
                return 'typescript'
                
            return None
        except Exception:
            return None
    
    def generate_header(self, user_info: Dict[str, Any], items_count: int) -> str:
        """יוצר כותרת מעוצבת למסמך"""
        date = datetime.now(tz=self.timezone).strftime('%d/%m/%Y')
        time = datetime.now(tz=self.timezone).strftime('%H:%M')
        username = user_info.get('username', 'משתמש')
        
        header = f"""# 📚 אוסף הקוד והתוכן השמור

> **נוצר בתאריך:** {date} בשעה {time}  
> **משתמש:** {username}  
> **סך הכל פריטים:** {items_count}

---

"""
        return header
    
    def generate_table_of_contents(self, items: List[Dict[str, Any]]) -> str:
        """יוצר תוכן עניינים אוטומטי"""
        toc = "## 📑 תוכן עניינים\n\n"
        
        # קיבוץ לפי קטגוריה
        categories = {}
        for idx, item in enumerate(items, 1):
            category = item.get('category', 'כללי')
            if category not in categories:
                categories[category] = []
            categories[category].append((idx, item))
        
        # יצירת תוכן עניינים לפי קטגוריות
        for category, category_items in categories.items():
            toc += f"### 📁 {category}\n"
            for idx, item in category_items:
                subject = item.get('subject', f'פריט #{idx}')
                # יצירת anchor link
                anchor = f"פריט-{idx}---{self.sanitize_anchor(subject)}"
                toc += f"- [{subject}](#{anchor})\n"
            toc += "\n"
        
        toc += "---\n\n"
        return toc
    
    def sanitize_anchor(self, text: str) -> str:
        """מנקה טקסט לשימוש כ-anchor בMarkdown"""
        # החלפת תווים בעייתיים
        text = re.sub(r'[^\w\s\u0590-\u05FF-]', '', text)  # שומר על עברית
        text = text.replace(' ', '-')
        text = text.lower()
        return text
    
    def generate_statistics(self, items: List[Dict[str, Any]]) -> str:
        """יוצר סטטיסטיקות על האוסף"""
        stats = "## 📊 סטטיסטיקות\n\n"
        
        # ספירת פריטים לפי קטגוריה
        category_counts = {}
        language_counts = {}
        total_chars = 0
        
        for item in items:
            # קטגוריות
            category = item.get('category', 'כללי')
            category_counts[category] = category_counts.get(category, 0) + 1
            
            # שפות תכנות (אם זה קוד)
            content = item.get('content', '')
            if content:
                total_chars += len(content)
                lang = self.detect_code_language(content)
                if lang:
                    language_counts[lang] = language_counts.get(lang, 0) + 1
        
        # טבלת קטגוריות
        stats += "### 📂 התפלגות לפי קטגוריות\n\n"
        stats += "| קטגוריה | מספר פריטים | אחוז |\n"
        stats += "|---------|-------------|------|\n"
        
        total = len(items)
        for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total * 100) if total > 0 else 0
            stats += f"| {category} | {count} | {percentage:.1f}% |\n"
        
        # טבלת שפות תכנות (אם יש)
        if language_counts:
            stats += "\n### 💻 שפות תכנות שזוהו\n\n"
            stats += "| שפה | אייקון | מספר קטעים |\n"
            stats += "|-----|--------|------------|\n"
            
            for lang, count in sorted(language_counts.items(), key=lambda x: x[1], reverse=True):
                icon = self.get_language_icon(lang)
                stats += f"| {lang.title()} | {icon} | {count} |\n"
        
        # סטטיסטיקות כלליות
        stats += f"\n### 📈 נתונים כלליים\n\n"
        stats += f"- **סך הכל תווים:** {total_chars:,}\n"
        stats += f"- **ממוצע תווים לפריט:** {total_chars // total if total > 0 else 0:,}\n"
        stats += f"- **פריטים מוצמדים:** {sum(1 for item in items if item.get('is_pinned', False))}\n"
        
        # תגיות פופולריות (אם יש)
        all_tags = []
        for item in items:
            if item.get('tags'):
                all_tags.extend(item['tags'])
        
        if all_tags:
            from collections import Counter
            tag_counts = Counter(all_tags)
            top_tags = tag_counts.most_common(10)
            
            stats += "\n### 🏷️ תגיות פופולריות\n\n"
            stats += " • ".join([f"`{tag}` ({count})" for tag, count in top_tags])
            stats += "\n"
        
        stats += "\n---\n\n"
        return stats
    
    def format_single_item(self, item: Dict[str, Any], index: int) -> str:
        """מעצב פריט בודד לMarkdown"""
        subject = item.get('subject', f'פריט #{index}')
        category = item.get('category', 'כללי')
        content = item.get('content', '')
        note = item.get('note', '')
        created_at = item.get('created_at', '')
        
        # כותרת הפריט
        section = f"## פריט {index} - {subject}\n\n"
        
        # מטא-דאטה
        section += f"📁 **קטגוריה:** {category}  \n"
        
        if created_at:
            try:
                date = datetime.fromisoformat(created_at)
                formatted_date = date.strftime('%d/%m/%Y %H:%M')
                section += f"📅 **נוצר:** {formatted_date}  \n"
            except:
                pass
        
        # תגיות (אם יש)
        if item.get('tags'):
            tags_str = ', '.join([f"`{tag}`" for tag in item['tags']])
            section += f"🏷️ **תגיות:** {tags_str}  \n"
        
        # סימון פריט מוצמד
        if item.get('is_pinned'):
            section += "📌 **פריט מוצמד**  \n"
        
        # הערה (אם יש)
        if note:
            section += f"\n💡 **הערה:**\n> {note}\n"
        
        section += "\n"
        
        # התוכן עצמו
        if content:
            content_type = item.get('content_type', 'text')
            
            if content_type == 'text' or content_type == 'document':
                # זיהוי שפת תכנות
                language = self.detect_code_language(content) or ''
                lang_icon = self.get_language_icon(language) if language else ''
                
                if language:
                    section += f"### {lang_icon} קוד ({language.title()})\n\n"
                else:
                    section += "### 📝 תוכן\n\n"
                
                # הוספת התוכן בבלוק קוד
                section += f"```{language}\n{content}\n```\n"
            else:
                # לסוגי תוכן אחרים (תמונה, וידאו וכו')
                file_name = item.get('file_name', '')
                if file_name:
                    section += f"### 📎 קובץ מצורף: {file_name}\n\n"
                    if item.get('caption'):
                        section += f"> {item['caption']}\n\n"
        
        # קישורים (אם יש)
        if item.get('gist_url'):
            section += f"\n🔗 **GitHub Gist:** [{item['gist_url']}]({item['gist_url']})\n"
        
        section += "\n---\n\n"
        return section
    
    def export_items_to_markdown(self, items: List[Dict[str, Any]], user_info: Dict[str, Any]) -> str:
        """מייצא רשימת פריטים לפורמט Markdown מלא"""
        if not items:
            return "# אין פריטים לייצוא\n\nהאוסף ריק."
        
        markdown = ""
        
        # כותרת
        markdown += self.generate_header(user_info, len(items))
        
        # תוכן עניינים
        markdown += self.generate_table_of_contents(items)
        
        # סטטיסטיקות
        markdown += self.generate_statistics(items)
        
        # הפריטים עצמם
        markdown += "## 📚 הפריטים השמורים\n\n"
        for idx, item in enumerate(items, 1):
            markdown += self.format_single_item(item, idx)
        
        # סיום
        markdown += self.generate_footer()
        
        return markdown
    
    def generate_footer(self) -> str:
        """יוצר סיום למסמך"""
        footer = """---

<div align="center">

📚 **נוצר על ידי SaveMe Bot**  
🤖 בוט חכם לשמירה וארגון של קוד ותוכן

</div>"""
        return footer
    
    def export_single_item_to_markdown(self, item: Dict[str, Any]) -> str:
        """מייצא פריט בודד לMarkdown"""
        markdown = f"# {item.get('subject', 'פריט שמור')}\n\n"
        markdown += self.format_single_item(item, 1)
        return markdown
    
    def create_markdown_file(self, content: str, filename: Optional[str] = None) -> BytesIO:
        """יוצר קובץ Markdown מהתוכן"""
        if not filename:
            timestamp = datetime.now(tz=self.timezone).strftime('%Y%m%d-%H%M%S')
            filename = f"export-{timestamp}.md"
        
        # יצירת BytesIO object
        md_bytes = BytesIO(content.encode('utf-8'))
        md_bytes.name = filename
        
        return md_bytes
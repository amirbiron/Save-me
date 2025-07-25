# 🤖 בוט "שמור לי" - Save Me Bot

בוט טלגרם לשמירה ואירגון של הודעות, רעיונות וקבצים עם אפשרויות חיפוש ותזכורות.

## ✨ תכונות

- 📝 שמירת טקסטים, קבצים ותמונות
- 🗂️ ארגון בקטגוריות
- 🔍 חיפוש מהיר ויעיל
- 📌 קיבוע פריטים חשובים
- 🕰️ תזכורות גמישות (1-168 שעות)
- 🗒️ הוספת הערות לפריטים
- ✏️ עריכה ומחיקה של תוכן
- 📊 סטטיסטיקות ויצוא נתונים

## 🚀 התקנה מהירה

### 1. יצירת בוט בטלגרם

1. פתח שיחה עם [@BotFather](https://t.me/BotFather)
2. שלח `/newbot`
3. בחר שם לבוט (לדוגמה: SaveMePersonalBot)
4. בחר username לבוט (חייב להסתיים ב-bot)
5. שמור את הטוקן שתקבל

### 2. פריסה על Render

#### שלב א': העלאת הקוד לגיטהאב

1. צור repository חדש בגיטהאב
2. העלה את כל הקבצים לrepository
3. וודא שכל הקבצים נמצאים בתיקיית השורש

#### שלב ב': הגדרת Render

1. כנס ל-[Render.com](https://render.com)
2. התחבר עם חשבון גיטהאב
3. לחץ על "New +" ובחר "Web Service"
4. בחר את הrepository שיצרת
5. מלא את הפרטים:
   - **Name**: save-me-bot (או שם אחר)
   - **Environment**: Docker
   - **Branch**: main
   - **Root Directory**: השאר ריק
   - **Build Command**: השאר ריק
   - **Start Command**: השאר ריק

#### שלב ג': הגדרת משתני סביבה

בעמוד ההגדרות של השירות ב-Render, הוסף את משתני הסביבה הבאים:

```
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
WEBHOOK_URL=https://your-app-name.onrender.com
PORT=8443
DATABASE_PATH=/app/data/save_me_bot.db
LOG_LEVEL=INFO
```

החלף:
- `YOUR_BOT_TOKEN_HERE` עם הטוקן שקיבלת מ-BotFather
- `your-app-name` עם השם שבחרת לשירות

### 3. הפעלה והגדרה

1. לחץ על "Create Web Service"
2. המתן לסיום הבנייה והפריסה (כמה דקות)
3. כשהסטטוס יהיה "Live", הבוט יהיה פעיל
4. שלח `/start` לבוט בטלגרם

## 🎯 איך להשתמש

### תפריט ראשי
```
➕ הוסף תוכן - שמירת הודעות וקבצים
🔍 חיפוש - חיפוש בפריטים שמורים
📚 הצג לפי קטגוריה - עיון לפי קטגוריות
⚙️ הגדרות - ניהול והגדרות
```

### זרימת שמירה
1. לחץ "➕ הוסף תוכן"
2. שלח טקסט, תמונה או קובץ
3. בחר קטגוריה (או צור חדשה)
4. הקלד נושא
5. לחץ "✅ שמור"

### כפתורי פעולה לכל פריט
- **📌 קבע/בטל** - קיבוע פריט בראש הרשימה
- **🕰️ תזכורת** - הגדרת תזכורת (1-168 שעות)
- **✏️ ערוך תוכן** - עריכת התוכן
- **📝 הוסף הערה** - הוספת או עריכת הערה
- **🗑️ מחק** - מחיקת פריט או הערה

## 🛠️ פיתוח מקומי

### דרישות מערכת
- Python 3.11+
- pip

### התקנה מקומית
```bash
git clone https://github.com/your-username/save-me-bot.git
cd save-me-bot
pip install -r requirements.txt
```

### הגדרת משתני סביבה
צור קובץ `.env`:
```
BOT_TOKEN=your_bot_token_here
DATABASE_PATH=save_me_bot.db
LOG_LEVEL=DEBUG
DEBUG=true
```

### הפעלה
```bash
python main.py
```

## 📁 מבנה הפרויקט

```
save-me-bot/
├── main.py              # קובץ הבוט הראשי
├── database.py          # מודל מסד הנתונים
├── config.py           # הגדרות הבוט
├── requirements.txt    # תלויות Python
├── Dockerfile         # קונטיינר לרנדר
├── .gitignore        # קבצים להתעלמות
└── README.md         # מדריך זה
```

## 🔧 משתני סביבה

| משתנה | נדרש | ברירת מחדל | תיאור |
|-------|------|------------|-------|
| `BOT_TOKEN` | ✅ | - | טוקן הבוט מ-BotFather |
| `WEBHOOK_URL` | לרנדר | - | URL של השירות |
| `PORT` | לרנדר | 8443 | פורט השירות |
| `DATABASE_PATH` | ❌ | save_me_bot.db | נתיב מסד הנתונים |
| `LOG_LEVEL` | ❌ | INFO | רמת לוגים |
| `MAX_REMINDER_HOURS` | ❌ | 168 | מקסימום שעות לתזכורת |
| `DEBUG` | ❌ | False | מצב פיתוח |

## 🔒 אבטחה

- הבוט פועל בסביבה מבודדת
- מסד הנתונים מקומי לכל פריסה
- כל משתמש רואה רק את הפריטים שלו
- ללא שמירת מידע אישי מיותר

## 🆘 פתרון בעיות

### הבוט לא עונה
1. בדוק שהטוקן נכון במשתני הסביבה
2. וודא שהשירות ב-Render פועל (סטטוס "Live")
3. בדוק את הלוגים ב-Render

### שגיאות מסד נתונים
1. הבוט יוצר את מסד הנתונים אוטומטית
2. אם יש בעיות, הסר את הקובץ ותן לבוט ליצור חדש

### בעיות ביצוא
1. וודא שיש מספיק מקום באחסון
2. בדוק הרשאות קבצים

## 📞 תמיכה

- דווח על באגים: פתח Issue בגיטהאב
- בקשות לתכונות: פתח Feature Request
- שאלות: שלח הודעה למפתח

## 📄 רישיון

MIT License - ראה קובץ LICENSE לפרטים

## 🙏 תודות

נבנה עם:
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [SQLite](https://sqlite.org/)
- [Render](https://render.com/)

---

**💡 עצה:** הבוט עובד הכי טוב כשמשתמשים בו בקביעות. התחל עם קטגוריות פשוטות והוסף עוד בהדרגה.

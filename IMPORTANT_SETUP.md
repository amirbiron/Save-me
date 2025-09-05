# הגדרות חשובות לבוט SaveMe 🚨

## הגדרת שם הבוט (חובה!)

כדי שהקישורים הפנימיים יעבדו נכון, חובה להגדיר את שם הבוט שלך:

### 1. אם אתה מפעיל ב-Render:
עדכן את הקובץ `render.yaml`:
```yaml
- key: BOT_USERNAME
  value: YOUR_BOT_USERNAME_HERE  # החלף בשם הבוט שלך (ללא @)
```

**דוגמה:** אם הבוט שלך נקרא `@MyAwesomeSaveBot`, הכנס: `MyAwesomeSaveBot`

### 2. אם אתה מפעיל באופן מקומי:
הגדר משתנה סביבה:
```bash
export BOT_USERNAME=YOUR_BOT_USERNAME_HERE
```

או הוסף לקובץ `.env`:
```
BOT_USERNAME=YOUR_BOT_USERNAME_HERE
```

## למה זה חשוב?
ללא הגדרה זו, הקישורים הפנימיים לשיתוף פריטים יפנו לבוט אחר (`SaveMeBot`) במקום לבוט שלך!

## איך לקבל את שם הבוט?
1. פתח צ'אט עם @BotFather בטלגרם
2. שלח `/mybots`
3. בחר את הבוט שלך
4. השם מופיע בתור `@YourBotName` - השתמש בחלק אחרי ה-@

## בדיקה
אחרי ההגדרה, כשתיצור קישור שיתוף פנימי, הוא צריך להיראות כך:
```
https://t.me/YOUR_BOT_USERNAME?start=share_xxxxx
```

ולא:
```
https://t.me/SaveMeBot?start=share_xxxxx
```
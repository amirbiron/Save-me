# שימוש בתמונת Python רשמית
FROM python:3.11-slim

# הגדרת תיקיית עבודה
WORKDIR /app

# העתקת קבצי requirements
COPY requirements.txt .

# התקנת תלויות
RUN pip install --no-cache-dir -r requirements.txt

# יצירת תיקייה למסד הנתונים
RUN mkdir -p /app/data

# העתקת קבצי הפרויקט
COPY . .

# הגדרת הרשאות
RUN chmod +x main.py

# יצירת משתמש לא-root לאבטחה
RUN useradd -m -u 1000 botuser && \
    chown -R botuser:botuser /app
USER botuser

# הגדרת משתני סביבה
ENV PYTHONPATH=/app
ENV DATABASE_PATH=/app/data/save_me_bot.db

# פתיחת פורט
EXPOSE 8443

# הרצת הבוט
CMD ["python", "main.py"]

# TRAIL RIDERS

אפליקציית Web לניווט ותקשורת בזמן אמת לקבוצות רכיבת שטח.

## מה כלול

- Flask + Flask-SocketIO
- עד 20 רוכבים בקבוצה
- מספר ברזל 1–20
- רכב מוביל
- שיתוף מיקום GPS בזמן אמת
- מפה עם Israel Hiking Map
- טעינת GPX
- Push-to-Talk באמצעות MediaRecorder
- אינדיקציה של הרוכב שמדבר
- ממשק מותאם לנייד
- קובץ `app.py` יחיד שמכיל את השרת ואת ה-HTML/JS/CSS

## הרצה מקומית

```bash
pip install -r requirements.txt
python app.py
```

פתח:
`http://localhost:5000`

## פריסה ב-Render דרך GitHub

1. העלה את תוכן התיקייה ל-GitHub Repository.
2. ב-Render בחר New + Web Service.
3. חבר את ה-Repository.
4. Render יכול להשתמש ב-`render.yaml`, או להגדיר:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn --worker-class eventlet -w 1 app:app`
5. לאחר הפריסה תקבל כתובת HTTPS.

## חשוב לגבי GPS ומיקרופון

יש להשתמש בכתובת HTTPS בפריסה ציבורית. דפדפנים מודרניים דורשים Secure Context עבור הרשאות מיקום ומיקרופון ברוב התרחישים.

## הערת Socket.IO

המערכת משתמשת ב-Eventlet וב-worker יחיד כדי לשמור על חיבורי WebSocket/Socket.IO בצורה פשוטה בפריסה הראשונית.

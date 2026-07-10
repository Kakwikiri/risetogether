# RiseTogether

RiseTogether is a Flask-based social media app for emotional support, motivational stories, and safe community families.

## Features

- Signup/Login/Logout with password hashing
- User profiles with avatar upload and bio
- Feed posts with image/audio/video support
- Reaction system with support, understand, keep going, inspire
- Family communities with join/invite and family feed
- Real-time 1-1 and family chat using WebSockets
- Notification system for comments, messages, and invites
- Reporting, blocking, and admin moderation
- PWA support with service worker and manifest
- WebRTC basic voice/video calling support

## Setup Instructions

1. Install dependencies

```bash
cd /home/allan/Desktop/socialapp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Configure PostgreSQL

Ensure PostgreSQL is running locally and create a database:

```bash
psql -U postgres -c "CREATE DATABASE rise_together;"
```

If your user or password differ, update `.env`:

```env
SECRET_KEY=supersecretkey123
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/rise_together
ADMIN_EMAIL=admin@risetogether.local
```

3. Initialize the database

```bash
python setup_db.py
```

4. Run the application

```bash
source venv/bin/activate
python app.py
```

5. Open in browser

Visit `http://localhost:5000`

## Local Deployment

The app runs locally with Flask and Socket.IO. For production, use a WSGI server like Gunicorn and set `DATABASE_URL` to your PostgreSQL endpoint.

---

If you need the exact database schema or further deployment steps, let me know.

# CashewTrack — Setup Instructions

## What's Inside
```
cashewtrack/
├── app.py              ← Flask backend + all API routes
├── requirements.txt    ← Python dependencies
├── cashewtrack.db      ← SQLite database (auto-created on first run)
└── templates/
    └── index.html      ← Full frontend UI
```

## Database Schema
| Table       | Columns                                              |
|-------------|------------------------------------------------------|
| users       | id, username, password                               |
| grades      | id, name                                             |
| sellers     | id, name                                             |
| buyers      | id, name                                             |
| inventory   | id, date, seller_id, grade_id, qty, notes, created_at|
| sales       | id, date, buyer_id, grade_id, qty, notes, created_at |
| settings    | key, value                                           |

## Step-by-Step Setup

### 1. Install Python
Make sure Python 3.8 or higher is installed.
Check: `python --version`

### 2. Install Dependencies
Open a terminal inside the `cashewtrack/` folder and run:
```bash
pip install flask
```

### 3. Run the App
```bash
python app.py
```

You will see:
```
✅ CashewTrack is running!
📂 Database: cashewtrack.db
🌐 Open your browser at: http://localhost:5000
```

### 4. Open in Browser
Go to: http://localhost:5000

### 5. Login
- Username: `admin`
- Password: `admin123`
(You can change the password from Settings inside the app)

## Notes
- The database file `cashewtrack.db` is created automatically on first run
- All data is stored permanently in this file — do NOT delete it
- To back up your data: just copy `cashewtrack.db` to a safe location
- The app runs locally on your computer — no internet required after setup

## Access from Other Devices on Same Network
Change the last line of `app.py` from:
```python
app.run(debug=True, port=5000)
```
to:
```python
app.run(host='0.0.0.0', port=5000)
```
Then other devices on your WiFi can access it at `http://YOUR_PC_IP:5000`

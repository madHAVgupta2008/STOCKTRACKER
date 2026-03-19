from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from functools import wraps
import sqlite3
import os
import csv
import io
from datetime import datetime, date

app = Flask(__name__)
app.secret_key = 'cashewtrack-secret-2024'
DB_PATH = os.path.join(os.path.dirname(__file__), 'cashewtrack.db')

# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS grades (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sellers (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS buyers (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                date      TEXT NOT NULL,
                seller_id INTEGER NOT NULL REFERENCES sellers(id),
                grade_id  INTEGER NOT NULL REFERENCES grades(id),
                qty       INTEGER NOT NULL CHECK(qty > 0),
                notes     TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sales (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                date      TEXT NOT NULL,
                buyer_id  INTEGER NOT NULL REFERENCES buyers(id),
                grade_id  INTEGER NOT NULL REFERENCES grades(id),
                qty       INTEGER NOT NULL CHECK(qty > 0),
                notes     TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Default admin user
            INSERT OR IGNORE INTO users (username, password) VALUES ('admin', 'admin123');

            -- Default grades
            INSERT OR IGNORE INTO grades (name) VALUES
                ('W180'),('W210'),('W240'),('W320'),('W450'),('Splits'),('Pieces');

            -- Default settings
            INSERT OR IGNORE INTO settings (key, value) VALUES ('low_threshold', '10');
        """)

# ─────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────
# PAGES
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

# ─────────────────────────────────────────────
# AUTH API
# ─────────────────────────────────────────────

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (data.get('username',''), data.get('password',''))
        ).fetchone()
    if user:
        session['user'] = user['username']
        return jsonify({'ok': True, 'username': user['username']})
    return jsonify({'ok': False, 'error': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
def me():
    if 'user' in session:
        return jsonify({'ok': True, 'username': session['user']})
    return jsonify({'ok': False}), 401

@app.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    data = request.json
    new_pass = data.get('password', '').strip()
    if not new_pass:
        return jsonify({'error': 'Password cannot be empty'}), 400
    with get_db() as conn:
        conn.execute("UPDATE users SET password=? WHERE username=?", (new_pass, session['user']))
    return jsonify({'ok': True})

# ─────────────────────────────────────────────
# GRADES API
# ─────────────────────────────────────────────

@app.route('/api/grades')
@login_required
def get_grades():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM grades ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/grades', methods=['POST'])
@login_required
def add_grade():
    name = request.json.get('name', '').strip().upper()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO grades (name) VALUES (?)", (name,))
        return jsonify({'ok': True})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Grade already exists'}), 409

@app.route('/api/grades/<int:gid>', methods=['DELETE'])
@login_required
def delete_grade(gid):
    with get_db() as conn:
        in_use = conn.execute(
            "SELECT 1 FROM inventory WHERE grade_id=? UNION SELECT 1 FROM sales WHERE grade_id=?",
            (gid, gid)
        ).fetchone()
        if in_use:
            return jsonify({'error': 'Grade is in use, cannot delete'}), 409
        conn.execute("DELETE FROM grades WHERE id=?", (gid,))
    return jsonify({'ok': True})

# ─────────────────────────────────────────────
# SELLERS / BUYERS API
# ─────────────────────────────────────────────

@app.route('/api/sellers')
@login_required
def get_sellers():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM sellers ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/buyers')
@login_required
def get_buyers():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM buyers ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

def get_or_create_seller(conn, name):
    row = conn.execute("SELECT id FROM sellers WHERE name=?", (name,)).fetchone()
    if row:
        return row['id']
    cur = conn.execute("INSERT INTO sellers (name) VALUES (?)", (name,))
    return cur.lastrowid

def get_or_create_buyer(conn, name):
    row = conn.execute("SELECT id FROM buyers WHERE name=?", (name,)).fetchone()
    if row:
        return row['id']
    cur = conn.execute("INSERT INTO buyers (name) VALUES (?)", (name,))
    return cur.lastrowid

# ─────────────────────────────────────────────
# STOCK / INVENTORY API
# ─────────────────────────────────────────────

def calc_stock_all(conn):
    rows = conn.execute("""
        SELECT g.name as grade,
               COALESCE(SUM(CASE WHEN i.qty IS NOT NULL THEN i.qty ELSE 0 END),0) -
               COALESCE(SUM(CASE WHEN s.qty IS NOT NULL THEN s.qty ELSE 0 END),0) as stock
        FROM grades g
        LEFT JOIN inventory i ON i.grade_id = g.id
        LEFT JOIN sales s ON s.grade_id = g.id
        GROUP BY g.id, g.name
    """).fetchall()
    return {r['grade']: r['stock'] for r in rows}

def calc_grade_stock(conn, grade_id, exclude_sale_id=None):
    added = conn.execute("SELECT COALESCE(SUM(qty),0) as t FROM inventory WHERE grade_id=?", (grade_id,)).fetchone()['t']
    q = "SELECT COALESCE(SUM(qty),0) as t FROM sales WHERE grade_id=?"
    params = [grade_id]
    if exclude_sale_id:
        q += " AND id != ?"
        params.append(exclude_sale_id)
    sold = conn.execute(q, params).fetchone()['t']
    return added - sold

@app.route('/api/stock')
@login_required
def get_stock():
    with get_db() as conn:
        stock = calc_stock_all(conn)
        threshold = int(conn.execute("SELECT value FROM settings WHERE key='low_threshold'").fetchone()['value'])
    return jsonify({'stock': stock, 'threshold': threshold})

@app.route('/api/inventory')
@login_required
def get_inventory():
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to', '')
    grade_id  = request.args.get('grade_id', '')
    search    = request.args.get('search', '')

    q = """
        SELECT i.id, i.date, s.name as seller, g.name as grade,
               g.id as grade_id, s.id as seller_id,
               i.qty, i.notes, i.created_at
        FROM inventory i
        JOIN sellers s ON s.id = i.seller_id
        JOIN grades  g ON g.id = i.grade_id
        WHERE 1=1
    """
    params = []
    if from_date: q += " AND i.date >= ?"; params.append(from_date)
    if to_date:   q += " AND i.date <= ?"; params.append(to_date)
    if grade_id:  q += " AND i.grade_id = ?"; params.append(grade_id)
    if search:    q += " AND s.name LIKE ?"; params.append(f'%{search}%')
    q += " ORDER BY i.date DESC, i.id DESC"

    with get_db() as conn:
        rows = conn.execute(q, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/inventory', methods=['POST'])
@login_required
def add_inventory():
    d = request.json
    date    = d.get('date', '').strip()
    seller  = d.get('seller', '').strip()
    grade_id= d.get('grade_id')
    qty     = d.get('qty')
    notes   = d.get('notes', '').strip()

    if not all([date, seller, grade_id, qty]):
        return jsonify({'error': 'Missing required fields'}), 400
    try:
        qty = int(qty)
        assert qty > 0
    except:
        return jsonify({'error': 'Invalid quantity'}), 400

    with get_db() as conn:
        seller_id = get_or_create_seller(conn, seller)
        conn.execute(
            "INSERT INTO inventory (date, seller_id, grade_id, qty, notes) VALUES (?,?,?,?,?)",
            (date, seller_id, grade_id, qty, notes)
        )
    return jsonify({'ok': True})

@app.route('/api/inventory/<int:iid>', methods=['PUT'])
@login_required
def update_inventory(iid):
    d = request.json
    date    = d.get('date', '').strip()
    seller  = d.get('seller', '').strip()
    grade_id= d.get('grade_id')
    qty     = d.get('qty')
    notes   = d.get('notes', '').strip()

    if not all([date, seller, grade_id, qty]):
        return jsonify({'error': 'Missing required fields'}), 400
    try:
        qty = int(qty)
        assert qty > 0
    except:
        return jsonify({'error': 'Invalid quantity'}), 400

    with get_db() as conn:
        seller_id = get_or_create_seller(conn, seller)
        conn.execute(
            "UPDATE inventory SET date=?, seller_id=?, grade_id=?, qty=?, notes=? WHERE id=?",
            (date, seller_id, grade_id, qty, notes, iid)
        )
    return jsonify({'ok': True})

@app.route('/api/inventory/<int:iid>', methods=['DELETE'])
@login_required
def delete_inventory(iid):
    with get_db() as conn:
        conn.execute("DELETE FROM inventory WHERE id=?", (iid,))
    return jsonify({'ok': True})

# ─────────────────────────────────────────────
# SALES API
# ─────────────────────────────────────────────

@app.route('/api/sales')
@login_required
def get_sales():
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to', '')
    grade_id  = request.args.get('grade_id', '')
    search    = request.args.get('search', '')

    q = """
        SELECT s.id, s.date, b.name as buyer, g.name as grade,
               g.id as grade_id, b.id as buyer_id,
               s.qty, s.notes, s.created_at
        FROM sales s
        JOIN buyers b ON b.id = s.buyer_id
        JOIN grades g ON g.id = s.grade_id
        WHERE 1=1
    """
    params = []
    if from_date: q += " AND s.date >= ?"; params.append(from_date)
    if to_date:   q += " AND s.date <= ?"; params.append(to_date)
    if grade_id:  q += " AND s.grade_id = ?"; params.append(grade_id)
    if search:    q += " AND b.name LIKE ?"; params.append(f'%{search}%')
    q += " ORDER BY s.date DESC, s.id DESC"

    with get_db() as conn:
        rows = conn.execute(q, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/sales', methods=['POST'])
@login_required
def add_sale():
    d = request.json
    date    = d.get('date', '').strip()
    buyer   = d.get('buyer', '').strip()
    grade_id= d.get('grade_id')
    qty     = d.get('qty')
    notes   = d.get('notes', '').strip()

    if not all([date, buyer, grade_id, qty]):
        return jsonify({'error': 'Missing required fields'}), 400
    try:
        qty = int(qty)
        assert qty > 0
    except:
        return jsonify({'error': 'Invalid quantity'}), 400

    with get_db() as conn:
        available = calc_grade_stock(conn, grade_id)
        if qty > available:
            return jsonify({'error': f'Only {max(0,available)} BKT available for this grade'}), 400
        buyer_id = get_or_create_buyer(conn, buyer)
        conn.execute(
            "INSERT INTO sales (date, buyer_id, grade_id, qty, notes) VALUES (?,?,?,?,?)",
            (date, buyer_id, grade_id, qty, notes)
        )
    return jsonify({'ok': True})

@app.route('/api/sales/<int:sid>', methods=['PUT'])
@login_required
def update_sale(sid):
    d = request.json
    date    = d.get('date', '').strip()
    buyer   = d.get('buyer', '').strip()
    grade_id= d.get('grade_id')
    qty     = d.get('qty')
    notes   = d.get('notes', '').strip()

    if not all([date, buyer, grade_id, qty]):
        return jsonify({'error': 'Missing required fields'}), 400
    try:
        qty = int(qty)
        assert qty > 0
    except:
        return jsonify({'error': 'Invalid quantity'}), 400

    with get_db() as conn:
        available = calc_grade_stock(conn, grade_id, exclude_sale_id=sid)
        if qty > available:
            return jsonify({'error': f'Only {max(0,available)} BKT available'}), 400
        buyer_id = get_or_create_buyer(conn, buyer)
        conn.execute(
            "UPDATE sales SET date=?, buyer_id=?, grade_id=?, qty=?, notes=? WHERE id=?",
            (date, buyer_id, grade_id, qty, notes, sid)
        )
    return jsonify({'ok': True})

@app.route('/api/sales/<int:sid>', methods=['DELETE'])
@login_required
def delete_sale(sid):
    with get_db() as conn:
        conn.execute("DELETE FROM sales WHERE id=?", (sid,))
    return jsonify({'ok': True})

# ─────────────────────────────────────────────
# DASHBOARD API
# ─────────────────────────────────────────────

@app.route('/api/dashboard')
@login_required
def dashboard():
    today = date.today().isoformat()
    with get_db() as conn:
        stock = calc_stock_all(conn)
        threshold = int(conn.execute("SELECT value FROM settings WHERE key='low_threshold'").fetchone()['value'])

        today_added = conn.execute(
            "SELECT COALESCE(SUM(qty),0) as t FROM inventory WHERE date=?", (today,)
        ).fetchone()['t']
        today_sold = conn.execute(
            "SELECT COALESCE(SUM(qty),0) as t FROM sales WHERE date=?", (today,)
        ).fetchone()['t']

        recent = conn.execute("""
            SELECT 'IN' as type, i.date, s.name as party, g.name as grade, i.qty
            FROM inventory i JOIN sellers s ON s.id=i.seller_id JOIN grades g ON g.id=i.grade_id
            UNION ALL
            SELECT 'OUT' as type, sa.date, b.name as party, g.name as grade, sa.qty
            FROM sales sa JOIN buyers b ON b.id=sa.buyer_id JOIN grades g ON g.id=sa.grade_id
            ORDER BY date DESC, qty DESC LIMIT 15
        """).fetchall()

    return jsonify({
        'stock': stock,
        'threshold': threshold,
        'today_added': today_added,
        'today_sold': today_sold,
        'recent': [dict(r) for r in recent]
    })

# ─────────────────────────────────────────────
# REPORTS API
# ─────────────────────────────────────────────

@app.route('/api/report')
@login_required
def report():
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to', '')

    with get_db() as conn:
        inv_q = "SELECT g.name as grade, COALESCE(SUM(i.qty),0) as added FROM inventory i JOIN grades g ON g.id=i.grade_id WHERE 1=1"
        sal_q = "SELECT g.name as grade, COALESCE(SUM(s.qty),0) as sold FROM sales s JOIN grades g ON g.id=s.grade_id WHERE 1=1"
        params = []
        if from_date:
            inv_q += " AND i.date >= ?"; sal_q += " AND s.date >= ?"; params.append(from_date)
        if to_date:
            inv_q += " AND i.date <= ?"; sal_q += " AND s.date <= ?"; params.append(to_date)
        inv_q += " GROUP BY g.id, g.name"
        sal_q += " GROUP BY g.id, g.name"

        inv_rows = {r['grade']: r['added'] for r in conn.execute(inv_q, params[:len(params)//2 or len(params)]).fetchall()}
        sal_rows = {r['grade']: r['sold']  for r in conn.execute(sal_q, params[len(params)//2:] if len(params)>1 else params).fetchall()}

        # re-run properly with correct params
        p1 = []; p2 = []
        inv_q2 = "SELECT g.name as grade, COALESCE(SUM(i.qty),0) as added FROM inventory i JOIN grades g ON g.id=i.grade_id WHERE 1=1"
        sal_q2 = "SELECT g.name as grade, COALESCE(SUM(s.qty),0) as sold FROM sales s JOIN grades g ON g.id=s.grade_id WHERE 1=1"
        if from_date:
            inv_q2 += " AND i.date >= ?"; p1.append(from_date)
            sal_q2 += " AND s.date >= ?"; p2.append(from_date)
        if to_date:
            inv_q2 += " AND i.date <= ?"; p1.append(to_date)
            sal_q2 += " AND s.date <= ?"; p2.append(to_date)
        inv_q2 += " GROUP BY g.id, g.name"
        sal_q2 += " GROUP BY g.id, g.name"

        inv_data = {r['grade']: r['added'] for r in conn.execute(inv_q2, p1).fetchall()}
        sal_data = {r['grade']: r['sold']  for r in conn.execute(sal_q2, p2).fetchall()}
        stock    = calc_stock_all(conn)

        grades = conn.execute("SELECT name FROM grades ORDER BY name").fetchall()

    result = []
    for g in grades:
        name = g['name']
        added = inv_data.get(name, 0)
        sold  = sal_data.get(name, 0)
        if added > 0 or sold > 0:
            result.append({'grade': name, 'added': added, 'sold': sold, 'balance': max(0, stock.get(name, 0))})

    return jsonify(result)

# ─────────────────────────────────────────────
# SETTINGS API
# ─────────────────────────────────────────────

@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r['key']: r['value'] for r in rows})

@app.route('/api/settings', methods=['POST'])
@login_required
def save_settings():
    d = request.json
    with get_db() as conn:
        for k, v in d.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
    return jsonify({'ok': True})

# ─────────────────────────────────────────────
# EXPORT CSV
# ─────────────────────────────────────────────

@app.route('/api/export/<string:kind>')
@login_required
def export_csv(kind):
    from flask import Response
    output = io.StringIO()
    writer = csv.writer(output)

    with get_db() as conn:
        if kind == 'inventory':
            writer.writerow(['Date','Seller','Grade','Qty(BKT)','Notes'])
            rows = conn.execute("""
                SELECT i.date, s.name, g.name, i.qty, i.notes
                FROM inventory i JOIN sellers s ON s.id=i.seller_id JOIN grades g ON g.id=i.grade_id
                ORDER BY i.date DESC
            """).fetchall()
            for r in rows: writer.writerow(list(r))
            filename = 'cashew_inventory.csv'
        elif kind == 'sales':
            writer.writerow(['Date','Buyer','Grade','Qty(BKT)','Notes'])
            rows = conn.execute("""
                SELECT s.date, b.name, g.name, s.qty, s.notes
                FROM sales s JOIN buyers b ON b.id=s.buyer_id JOIN grades g ON g.id=s.grade_id
                ORDER BY s.date DESC
            """).fetchall()
            for r in rows: writer.writerow(list(r))
            filename = 'cashew_sales.csv'
        else:
            return jsonify({'error': 'Unknown export type'}), 400

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

# ─────────────────────────────────────────────
# GRADE STOCK (for sale form)
# ─────────────────────────────────────────────

@app.route('/api/grade-stock/<int:grade_id>')
@login_required
def grade_stock(grade_id):
    exclude = request.args.get('exclude_sale', None)
    with get_db() as conn:
        s = calc_grade_stock(conn, grade_id, exclude_sale_id=int(exclude) if exclude else None)
    return jsonify({'available': max(0, s)})

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    print("\n✅ CashewTrack is running!")
    print("📂 Database: cashewtrack.db")
    print("🌐 Open your browser at: http://localhost:5000\n")
    app.run(debug=True, port=5000)

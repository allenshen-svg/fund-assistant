#!/usr/bin/env python3
"""
时光记 (TimeLog) — 用户认证 & 数据同步后端
- POST /api/tl/register  → 注册
- POST /api/tl/login     → 登录
- GET  /api/tl/sync      → 拉取数据
- POST /api/tl/sync      → 上传数据
- DELETE /api/tl/account  → 删除账户
- GET  /api/tl/status     → 健康检查
"""

import os, json, time, sqlite3, hashlib, hmac, secrets
from datetime import datetime, timezone

import bcrypt
import jwt
from flask import Flask, request, jsonify

# ─── Config ───
PORT = int(os.environ.get('TL_PORT', 8001))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'timelog_users.db')
JWT_SECRET = os.environ.get('TL_JWT_SECRET', secrets.token_hex(32))
JWT_EXPIRY = 86400 * 30  # 30 days

app = Flask(__name__)

# ─── CORS ───
@app.after_request
def cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    if request.method == 'OPTIONS':
        resp.status_code = 204
    return resp

# ─── Database ───
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            nickname TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_data (
            user_id INTEGER PRIMARY KEY,
            profile TEXT DEFAULT '{}',
            plans TEXT DEFAULT '[]',
            diary TEXT DEFAULT '[]',
            motto TEXT DEFAULT '',
            city TEXT DEFAULT '',
            theme TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()

# ─── Auth helpers ───
def hash_password(pw):
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(pw, pw_hash):
    return bcrypt.checkpw(pw.encode('utf-8'), pw_hash.encode('utf-8'))

def make_token(user_id):
    payload = {
        'uid': user_id,
        'exp': int(time.time()) + JWT_EXPIRY,
        'iat': int(time.time()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload['uid']
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

def get_current_user():
    """Extract user_id from Authorization header. Returns (user_id, error_response)."""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None, (jsonify({'error': '未登录'}), 401)
    uid = verify_token(auth[7:])
    if uid is None:
        return None, (jsonify({'error': '登录已过期，请重新登录'}), 401)
    return uid, None

# ─── Routes ───
@app.route('/api/tl/status')
def status():
    return jsonify({'ok': True, 'ts': datetime.now(timezone.utc).isoformat()})

@app.route('/api/tl/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''

    if not phone or len(phone) < 6 or len(phone) > 20:
        return jsonify({'error': '请输入有效的手机号'}), 400
    if not password or len(password) < 6:
        return jsonify({'error': '密码至少6位'}), 400

    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
        if existing:
            conn.close()
            return jsonify({'error': '该手机号已注册'}), 409

        pw_hash = hash_password(password)
        cursor = conn.execute(
            "INSERT INTO users (phone, pw_hash, nickname) VALUES (?, ?, ?)",
            (phone, pw_hash, data.get('nickname', ''))
        )
        user_id = cursor.lastrowid
        conn.execute("INSERT INTO user_data (user_id) VALUES (?)", (user_id,))
        conn.commit()
        token = make_token(user_id)
        return jsonify({'token': token, 'uid': user_id}), 201
    finally:
        conn.close()

@app.route('/api/tl/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''

    if not phone or not password:
        return jsonify({'error': '请输入手机号和密码'}), 400

    conn = get_db()
    try:
        user = conn.execute("SELECT id, pw_hash FROM users WHERE phone=?", (phone,)).fetchone()
        if not user or not check_password(password, user['pw_hash']):
            return jsonify({'error': '手机号或密码错误'}), 401
        token = make_token(user['id'])
        return jsonify({'token': token, 'uid': user['id']})
    finally:
        conn.close()

@app.route('/api/tl/sync', methods=['GET'])
def sync_get():
    uid, err = get_current_user()
    if err:
        return err

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM user_data WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return jsonify({'profile': '{}', 'plans': '[]', 'diary': '[]', 'motto': '', 'city': '', 'theme': ''})
        return jsonify({
            'profile': row['profile'],
            'plans': row['plans'],
            'diary': row['diary'],
            'motto': row['motto'],
            'city': row['city'],
            'theme': row['theme'],
            'updated_at': row['updated_at'],
        })
    finally:
        conn.close()

@app.route('/api/tl/sync', methods=['POST'])
def sync_post():
    uid, err = get_current_user()
    if err:
        return err

    data = request.get_json(silent=True) or {}

    # Validate JSON strings to prevent storing malformed data
    for key in ('profile', 'plans', 'diary'):
        val = data.get(key)
        if val is not None:
            try:
                json.loads(val) if isinstance(val, str) else None
            except json.JSONDecodeError:
                return jsonify({'error': f'{key} 数据格式错误'}), 400

    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO user_data (user_id, profile, plans, diary, motto, city, theme, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                profile=excluded.profile,
                plans=excluded.plans,
                diary=excluded.diary,
                motto=excluded.motto,
                city=excluded.city,
                theme=excluded.theme,
                updated_at=excluded.updated_at
        """, (
            uid,
            data.get('profile', '{}'),
            data.get('plans', '[]'),
            data.get('diary', '[]'),
            data.get('motto', ''),
            data.get('city', ''),
            data.get('theme', ''),
            now,
        ))
        conn.commit()
        return jsonify({'ok': True, 'updated_at': now})
    finally:
        conn.close()

@app.route('/api/tl/account', methods=['DELETE'])
def delete_account():
    uid, err = get_current_user()
    if err:
        return err

    conn = get_db()
    try:
        conn.execute("DELETE FROM user_data WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
        return jsonify({'ok': True, 'message': 'Account deleted'})
    finally:
        conn.close()

# ─── Main ───
if __name__ == '__main__':
    init_db()
    print(f"🕰️  TimeLog API running on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)

import sqlite3
import threading
from datetime import datetime

class DB:
    def __init__(self, path='data/bot.db'):
        self.path = path
        self.lock = threading.Lock()
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.path, check_same_thread=False)

    def _init_db(self):
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    prompt TEXT,
                    temperature REAL DEFAULT 0.7,
                    history_length INTEGER DEFAULT 10,
                    model TEXT,
                    image_size TEXT DEFAULT "1024x1024",
                    enabled INTEGER DEFAULT 1
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TEXT
                )
            ''')
            # users table for per-user API keys and preferences
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    api_key_enc TEXT
                )
            ''')
            conn.commit()
            conn.close()

    def ensure_chat(self, chat_id):
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute('INSERT OR IGNORE INTO chats (chat_id) VALUES (?)', (chat_id,))
            conn.commit()
            conn.close()

    def set_prompt(self, chat_id, prompt):
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute('UPDATE chats SET prompt = ? WHERE chat_id = ?', (prompt, chat_id))
            conn.commit()
            conn.close()

    def get_prompt(self, chat_id):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute('SELECT prompt FROM chats WHERE chat_id = ?', (chat_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else ''

    def add_message(self, chat_id, role, content):
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute('INSERT INTO messages (chat_id, role, content, created_at) VALUES (?,?,?,?)',
                        (chat_id, role, content, datetime.utcnow().isoformat()))
            conn.commit()
            conn.close()

    def get_history(self, chat_id, limit=20):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute('SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?', (chat_id, limit))
        rows = cur.fetchall()
        rows.reverse()
        return rows

    def get_settings(self, chat_id):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute('SELECT prompt, temperature, history_length, model, image_size, enabled FROM chats WHERE chat_id = ?', (chat_id,))
        row = cur.fetchone()
        if not row:
            return {'prompt': '', 'temperature': 0.7, 'history_length': 10, 'model': None, 'image_size': '1024x1024', 'enabled': True}
        return {'prompt': row[0] or '', 'temperature': row[1] or 0.7, 'history_length': row[2] or 10, 'model': row[3], 'image_size': row[4] or '1024x1024', 'enabled': bool(row[5])}

    def get_setting(self, chat_id, key):
        s = self.get_settings(chat_id)
        return s.get(key)

    def set_setting(self, chat_id, key, value):
        # limited set: prompt, temperature, history_length, model, image_size, enabled
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            if key == 'prompt':
                cur.execute('UPDATE chats SET prompt = ? WHERE chat_id = ?', (value, chat_id))
            elif key == 'temperature':
                cur.execute('UPDATE chats SET temperature = ? WHERE chat_id = ?', (float(value), chat_id))
            elif key == 'history_length':
                cur.execute('UPDATE chats SET history_length = ? WHERE chat_id = ?', (int(value), chat_id))
            elif key == 'model':
                cur.execute('UPDATE chats SET model = ? WHERE chat_id = ?', (value, chat_id))
            elif key == 'image_size':
                cur.execute('UPDATE chats SET image_size = ? WHERE chat_id = ?', (value, chat_id))
            elif key == 'enabled':
                cur.execute('UPDATE chats SET enabled = ? WHERE chat_id = ?', (1 if value else 0, chat_id))
            conn.commit()
            conn.close()

    def set_enabled(self, chat_id, enabled: bool):
        self.set_setting(chat_id, 'enabled', enabled)

    def reset_settings(self, chat_id):
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute('UPDATE chats SET prompt = NULL, temperature = 0.7, history_length = 10, model = NULL, image_size = "1024x1024", enabled = 1 WHERE chat_id = ?', (chat_id,))
            conn.commit()
            conn.close()

    # User API key management (per-user keys)
    def set_user_api_key_enc(self, user_id, api_key_enc):
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute('INSERT OR REPLACE INTO users (user_id, api_key_enc) VALUES (?,?)', (user_id, api_key_enc))
            conn.commit()
            conn.close()

    def get_user_api_key_enc(self, user_id):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute('SELECT api_key_enc FROM users WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def remove_user_api_key(self, user_id):
        with self.lock:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()

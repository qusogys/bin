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
                    history_length INTEGER DEFAULT 10
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
        cur.execute('SELECT prompt, temperature, history_length FROM chats WHERE chat_id = ?', (chat_id,))
        row = cur.fetchone()
        if not row:
            return {}
        return {'prompt': row[0], 'temperature': row[1], 'history_length': row[2]}

    def get_setting(self, chat_id, key):
        s = self.get_settings(chat_id)
        return s.get(key)

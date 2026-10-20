"""Original SQLite store extracted from the October development snapshot.

Roles and identifiers anonymized. HTTP/authentication and production wiring excluded.
This is a trusted local library, not a network security boundary.
"""
import secrets
import sqlite3
import time
from contextlib import contextmanager

ROOMS = {
    'group': ['user', 'agent_a', 'agent_b'],
    'user-agent_a': ['user', 'agent_a'],
    'user-agent_b': ['user', 'agent_b'],
    'agent_a-agent_b': ['agent_a', 'agent_b'],
}
READ_OBSERVERS = {'agent_a-agent_b': ['user']}

class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS rooms(id TEXT PRIMARY KEY, epoch INTEGER DEFAULT 0, used INTEGER DEFAULT 0, visibility_version INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS buffers(id TEXT PRIMARY KEY, released INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, room TEXT, sender TEXT, body TEXT, buffer TEXT, created REAL, request_key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, room TEXT, actor TEXT, trigger_id TEXT, epoch INTEGER, buffer TEXT, status TEXT, created REAL, UNIQUE(actor,trigger_id));
            CREATE TABLE IF NOT EXISTS job_dispatch(job_id TEXT PRIMARY KEY, started REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_a_context_cursor(
                room TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                created REAL NOT NULL,
                PRIMARY KEY(room,thread_id)
            );
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires REAL);
            CREATE TABLE IF NOT EXISTS limits(ip TEXT PRIMARY KEY, attempts INTEGER, expires REAL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(rooms)')}
            if 'visibility_version' not in columns:
                db.execute('ALTER TABLE rooms ADD COLUMN visibility_version INTEGER DEFAULT 0')
            db.execute('CREATE INDEX IF NOT EXISTS messages_room_created_id ON messages(room, created, id)')
            for room in ROOMS:
                db.execute('INSERT OR IGNORE INTO rooms(id) VALUES(?)', (room,))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE status IN ('running','waiting_agent_a')")

    def get_agent_a_context_cursor(self, room, thread_id):
        self.check(room, 'agent_a', read=True)
        with self.db() as db:
            row = db.execute(
                'SELECT message_id,created FROM agent_a_context_cursor WHERE room=? AND thread_id=?',
                (room, thread_id),
            ).fetchone()
        return {'message_id': row['message_id'], 'created': row['created']} if row else None

    def set_agent_a_context_cursor(self, room, thread_id, cursor):
        self.check(room, 'agent_a', read=True)
        if not thread_id or not cursor or not cursor.get('message_id'):
            raise ValueError('Invalid Agent A context cursor')
        with self.db() as db:
            db.execute(
                'INSERT INTO agent_a_context_cursor(room,thread_id,message_id,created) VALUES(?,?,?,?) '
                'ON CONFLICT(room,thread_id) DO UPDATE SET message_id=excluded.message_id,created=excluded.created',
                (room, thread_id, cursor['message_id'], float(cursor['created'])),
            )

    def expire_waiting_agent_a(self, now=None, timeout_seconds=600):
        if timeout_seconds <= 0:
            raise ValueError('等待超时必须大于零')
        cutoff = (time.time() if now is None else now) - timeout_seconds
        with self.db() as db:
            result = db.execute(
                "UPDATE jobs SET status='failed' "
                "WHERE status='waiting_agent_a' AND id IN "
                "(SELECT job_id FROM job_dispatch WHERE started<=?)",
                (cutoff,),
            )
            return result.rowcount

    def read(self, room, actor):
        self.check(room, actor, read=True)
        with self.db() as db:
            rows = db.execute('SELECT m.*, coalesce(b.released,0) released FROM messages m LEFT JOIN buffers b ON b.id=m.buffer WHERE room=? ORDER BY created,id', (room,)).fetchall()
            state = dict(db.execute('SELECT * FROM rooms WHERE id=?', (room,)).fetchone())
            jobs = [dict(r) for r in db.execute(
                'SELECT id,actor,status,created FROM jobs WHERE room=? AND epoch=? ORDER BY created,id',
                (room, state['epoch']),
            )]
        items = []
        for row in rows:
            item = dict(row)
            item.pop('request_key')
            item['private'] = bool(item['buffer']) and not item.pop('released')
            if actor == 'user' and item['private']:
                item['body'] = None
            items.append(item)
        return {'messages': items, 'paused': state['used'] >= 6, 'jobs': jobs}

    @staticmethod
    def _cursor(row):
        return {'created': row['created'], 'id': row['id']}

    @staticmethod
    def _items(rows, actor):
        items = []
        for row in rows:
            item = dict(row)
            item.pop('request_key')
            item['private'] = bool(item['buffer']) and not item.pop('released')
            if actor == 'user' and item['private']:
                item['body'] = None
            items.append(item)
        return items

    def read_page(self, room, actor, before=None, after=None, limit=50):
        """Return a bounded, chronological page without changing full-context reads."""
        self.check(room, actor, read=True)
        if before and after:
            raise ValueError('分页方向冲突')
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('分页大小无效')
        for cursor in (before, after):
            if cursor and (not isinstance(cursor, dict) or not isinstance(cursor.get('id'), str)):
                raise ValueError('分页游标无效')
        with self.db() as db:
            state = dict(db.execute('SELECT * FROM rooms WHERE id=?', (room,)).fetchone())
            jobs = [dict(r) for r in db.execute(
                'SELECT id,actor,status,created FROM jobs WHERE room=? AND epoch=? ORDER BY created,id',
                (room, state['epoch']),
            )]
            total = db.execute('SELECT count(*) AS count FROM messages WHERE room=?', (room,)).fetchone()['count']
            base = 'SELECT m.*, coalesce(b.released,0) released FROM messages m LEFT JOIN buffers b ON b.id=m.buffer WHERE m.room=?'
            params = [room]
            if before:
                rows = db.execute(
                    base + ' AND (m.created<? OR (m.created=? AND m.id<?)) ORDER BY m.created DESC,m.id DESC LIMIT ?',
                    params + [float(before['created']), float(before['created']), before['id'], limit + 1],
                ).fetchall()
                has_more_before = len(rows) > limit
                rows = list(reversed(rows[:limit]))
                has_more_after = bool(rows)
            elif after:
                rows = db.execute(
                    base + ' AND (m.created>? OR (m.created=? AND m.id>?)) ORDER BY m.created,m.id LIMIT ?',
                    params + [float(after['created']), float(after['created']), after['id'], limit + 1],
                ).fetchall()
                has_more_after = len(rows) > limit
                rows = rows[:limit]
                has_more_before = True
            else:
                rows = db.execute(base + ' ORDER BY m.created DESC,m.id DESC LIMIT ?', params + [limit + 1]).fetchall()
                has_more_before = len(rows) > limit
                rows = list(reversed(rows[:limit]))
                has_more_after = False
        return {
            'messages': self._items(rows, actor),
            'paused': state['used'] >= 6,
            'jobs': jobs,
            'total': total,
            'before_cursor': self._cursor(rows[0]) if rows else None,
            'after_cursor': self._cursor(rows[-1]) if rows else None,
            'has_more_before': has_more_before,
            'has_more_after': has_more_after,
            'visibility_version': state['visibility_version'],
        }

    def summaries(self, actor):
        room_ids = []
        for room in ROOMS:
            try:
                self.check(room, actor, read=True)
            except PermissionError:
                continue
            room_ids.append(room)
        with self.db() as db:
            counts = {
                row['room']: row['count']
                for row in db.execute(
                    'SELECT room, count(*) AS count FROM messages GROUP BY room'
                )
            }
        return {
            'rooms': [
                {'id': room, 'count': counts.get(room, 0)}
                for room in room_ids
            ]
        }

    @staticmethod
    def check(room, actor, read=False):
        allowed = room in ROOMS and (
            actor in ROOMS[room] or (read and actor in READ_OBSERVERS.get(room, ()))
        )
        if not allowed:
            raise PermissionError('无权访问此窗口')

    def post(self, room, actor, body, key, private=False, buffer=None, job_id=None, initiate=False):
        self.check(room, actor)
        if not isinstance(body, str) or not body.strip() or len(body) > 16000:
            raise ValueError('消息不能为空，且最多 16000 字符')
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError('缺少消息编号')
        if private and (actor != 'agent_a' or room != 'agent_a-agent_b'):
            raise PermissionError('只有 Agent A 可以开启暂不公开缓冲区')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            request_key = actor + ':' + room + ':' + key
            old = db.execute('SELECT id FROM messages WHERE request_key=?', (request_key,)).fetchone()
            if old:
                return old['id']
            state = db.execute('SELECT * FROM rooms WHERE id=?', (room,)).fetchone()
            epoch, used = state['epoch'], state['used']
            if initiate:
                if actor != 'agent_a' or job_id:
                    raise PermissionError('只有原会话主动发送可以开启新话题')
                if db.execute("SELECT 1 FROM jobs WHERE room=? AND epoch=? AND status IN ('pending','running','waiting_agent_a')",(room,epoch)).fetchone():
                    raise ValueError('该窗口仍在交流，请先处理待回复消息')
                epoch += 1
                used = 0
            if job_id:
                job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
                if not job or job['actor'] != actor or job['room'] != room or job['epoch'] != epoch or job['status'] not in ('running','waiting_agent_a'):
                    raise ValueError('回复任务已过期')
                buffer = job['buffer']
            if buffer:
                if room != 'agent_a-agent_b' or not db.execute('SELECT 1 FROM buffers WHERE id=?', (buffer,)).fetchone():
                    raise ValueError('缓冲区不存在')
            if private and not buffer:
                buffer = secrets.token_hex(16)
                db.execute('INSERT INTO buffers(id) VALUES(?)', (buffer,))
            if actor == 'user':
                epoch += 1
                used = 0
                db.execute("UPDATE jobs SET status='cancelled' WHERE room=? AND status IN ('pending','running','waiting_agent_a','failed')", (room,))
            elif used >= 6:
                raise ValueError('三轮已结束，请由用户点击继续')
            else:
                used += 1
            if used >= 6:
                db.execute("UPDATE jobs SET status='cancelled' WHERE room=? AND status='pending'", (room,))
            db.execute('UPDATE rooms SET epoch=?,used=? WHERE id=?', (epoch, used, room))
            mid = secrets.token_hex(16)
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (mid, room, actor, body.strip(), buffer, time.time(), request_key))
            if job_id:
                db.execute("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
            targets = [a for a in ROOMS[room] if a not in ('user', actor)]
            mentions = [a for a in targets if '@' + a in body.lower()]
            if mentions:
                targets = mentions
            if used < 6:
                for target in targets:
                    # Coalesce turns already queued for the same participant.
                    active = db.execute("SELECT 1 FROM jobs WHERE room=? AND actor=? AND epoch=? AND buffer IS ? AND status IN ('pending','running','waiting_agent_a')", (room, target, epoch, buffer)).fetchone()
                    if not active:
                        db.execute(
                            'INSERT INTO jobs(id,room,actor,trigger_id,epoch,buffer,status,created) '
                            'VALUES(?,?,?,?,?,?,?,?)',
                            (secrets.token_hex(16), room, target, mid, epoch, buffer, 'pending', time.time()),
                        )
            return mid

    def action(self, actor, action, data):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if action == 'release':
                if actor != 'agent_a':
                    raise PermissionError('由 Agent A 手动公开')
                db.execute('UPDATE buffers SET released=1 WHERE id=?', (data['buffer'],))
                db.execute(
                    'UPDATE rooms SET visibility_version=visibility_version+1 '
                    'WHERE id IN (SELECT DISTINCT room FROM messages WHERE buffer=?)',
                    (data['buffer'],),
                )
            elif action == 'retry':
                if actor != 'user':
                    raise PermissionError('由用户重试')
                job = db.execute(
                    "SELECT jobs.* FROM jobs JOIN rooms ON rooms.id=jobs.room "
                    "WHERE jobs.id=? AND jobs.status='failed' AND jobs.epoch=rooms.epoch",
                    (data['job'],),
                ).fetchone()
                if job:
                    original_trigger = job['trigger_id']
                    fresh_id = secrets.token_hex(16)
                    db.execute(
                        "UPDATE jobs SET status='cancelled', trigger_id=? WHERE id=?",
                        ('retried:' + job['id'], job['id']),
                    )
                    db.execute(
                        'INSERT INTO jobs(id,room,actor,trigger_id,epoch,buffer,status,created) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (
                            fresh_id,
                            job['room'],
                            job['actor'],
                            original_trigger,
                            job['epoch'],
                            job['buffer'],
                            'pending',
                            time.time(),
                        ),
                    )
            elif action in ('continue', 'start'):
                if actor != 'user':
                    raise PermissionError('由用户明确继续')
                room = data['room']
                self.check(room, actor, read=True)
                state = db.execute('SELECT * FROM rooms WHERE id=?', (room,)).fetchone()
                if action == 'continue' and state['used'] < 6:
                    raise ValueError('当前尚未达到暂停轮数')
                if action == 'start' and (room != 'agent_a-agent_b' or db.execute('SELECT 1 FROM messages WHERE room=?', (room,)).fetchone() or db.execute('SELECT 1 FROM jobs WHERE room=?', (room,)).fetchone()):
                    raise ValueError('该窗口已经开始过对话')
                last = db.execute('SELECT * FROM messages WHERE room=? ORDER BY created DESC LIMIT 1', (room,)).fetchone()
                db.execute('UPDATE rooms SET used=0,epoch=epoch+1 WHERE id=?', (room,))
                if last:
                    participants = [name for name in ROOMS[room] if name != 'user']
                    targets = [name for name in participants if name != last['sender']]
                    target = (targets or participants)[0]
                    db.execute(
                        'INSERT INTO jobs(id,room,actor,trigger_id,epoch,buffer,status,created) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (secrets.token_hex(16), room, target, 'continue:' + secrets.token_hex(8), state['epoch']+1, last['buffer'], 'pending', time.time()),
                    )
                else:
                    db.execute(
                        'INSERT INTO jobs(id,room,actor,trigger_id,epoch,buffer,status,created) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (secrets.token_hex(16), room, 'agent_a', 'start', state['epoch']+1, None, 'pending', time.time()),
                    )
            else:
                raise ValueError('未知操作')

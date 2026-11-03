# -*- coding: utf-8 -*-
"""报修工单数据库：tickets / events / assets。"""
import json
import os
import sqlite3
import threading
from datetime import datetime

from core import paths

STATUS_LABEL = {'pending': '待处理', 'processing': '处理中', 'done': '已完成', 'closed': '已关闭'}
STATUSES = list(STATUS_LABEL.keys())
CATEGORIES = ['网络', '水电', '门锁', '设备', '其他']
PRIORITIES = ['高', '中', '低']

_TICKET_LOCK = threading.Lock()


def db_path():
    return paths.ticket_db()


def get_db():
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def init_db():
    conn = get_db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS tickets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticket_no TEXT UNIQUE NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      done_at TEXT,
      reporter TEXT DEFAULT '',
      contact TEXT DEFAULT '',
      location TEXT DEFAULT '',
      description TEXT NOT NULL,
      category TEXT NOT NULL,
      priority TEXT NOT NULL,
      confidence REAL DEFAULT 0,
      ai_summary TEXT DEFAULT '',
      ai_checks TEXT DEFAULT '[]',
      ai_sources TEXT DEFAULT '[]',
      ai_mode TEXT DEFAULT 'rule',
      status TEXT DEFAULT 'pending',
      assignee TEXT DEFAULT '',
      note TEXT DEFAULT '',
      asset_id INTEGER,
      sla_due_at TEXT DEFAULT '',
      first_response_at TEXT DEFAULT '',
      prompt_tokens INTEGER DEFAULT 0,
      completion_tokens INTEGER DEFAULT 0,
      total_tokens INTEGER DEFAULT 0,
      ai_ms INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticket_id INTEGER NOT NULL,
      from_status TEXT DEFAULT '',
      to_status TEXT DEFAULT '',
      note TEXT DEFAULT '',
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS assets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      category TEXT DEFAULT '其他',
      location TEXT DEFAULT '',
      owner TEXT DEFAULT '',
      status TEXT DEFAULT '在用',
      warranty_until TEXT DEFAULT '',
      vendor TEXT DEFAULT '',
      manual TEXT DEFAULT '',
      note TEXT DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
    CREATE INDEX IF NOT EXISTS idx_tickets_no ON tickets(ticket_no);
    CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_assets_code ON assets(code);
    ''')
    migrate_db(conn)
    conn.commit()
    conn.close()


def migrate_db(conn):
    """旧库平滑升级：补齐统计字段、来源、设备关联。"""
    cols = {r[1] for r in conn.execute('PRAGMA table_info(tickets)').fetchall()}
    for col, ddl in (
            ('prompt_tokens', 'INTEGER DEFAULT 0'),
            ('completion_tokens', 'INTEGER DEFAULT 0'),
            ('total_tokens', 'INTEGER DEFAULT 0'),
            ('ai_ms', 'INTEGER DEFAULT 0'),
            ('ai_sources', "TEXT DEFAULT '[]'"),
            ('asset_id', 'INTEGER'),
            ('sla_due_at', "TEXT DEFAULT ''"),
            ('first_response_at', "TEXT DEFAULT ''")):
        if col not in cols:
            conn.execute('ALTER TABLE tickets ADD COLUMN %s %s' % (col, ddl))


def gen_ticket_no(conn):
    """BX + 日期 + 3 位当日序号。演示级并发足够。"""
    prefix = 'BX' + datetime.now().strftime('%Y%m%d')
    row = conn.execute(
        'SELECT COUNT(*) AS c FROM tickets WHERE ticket_no LIKE ?', (prefix + '%',)).fetchone()
    return '%s%03d' % (prefix, (row['c'] if row else 0) + 1)


def _json_list(raw):
    try:
        value = json.loads(raw or '[]')
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _sla_flags(d):
    """计算是否超时/是否在完成前已超时（只做展示，不发通知）。"""
    due_raw = d.get('sla_due_at') or ''
    if not due_raw:
        return
    try:
        due = datetime.strptime(due_raw, '%Y-%m-%d %H:%M:%S')
    except Exception:
        return
    now = datetime.now()
    d['overdue'] = d.get('status') not in ('done', 'closed') and now > due
    done_raw = d.get('done_at') or ''
    if done_raw:
        try:
            done = datetime.strptime(done_raw, '%Y-%m-%d %H:%M:%S')
            d['sla_breached'] = done > due
        except Exception:
            pass


def row_to_ticket(r):
    d = dict(r)
    d['ai_checks'] = _json_list(d.get('ai_checks'))
    d['ai_sources'] = _json_list(d.get('ai_sources'))
    d['status_label'] = STATUS_LABEL.get(d['status'], d['status'])
    _sla_flags(d)
    return d


def row_to_asset(r):
    return dict(r)


def list_assets(conn, q=''):
    sql = 'SELECT * FROM assets WHERE 1=1'
    args = []
    if q:
        like = '%' + q + '%'
        sql += ' AND (code LIKE ? OR name LIKE ? OR location LIKE ? OR owner LIKE ?)'
        args += [like, like, like, like]
    sql += ' ORDER BY id DESC LIMIT 200'
    return [row_to_asset(r) for r in conn.execute(sql, args).fetchall()]


def get_asset(conn, asset_id=None, code=None):
    if code:
        row = conn.execute('SELECT * FROM assets WHERE code=?', (code,)).fetchone()
    else:
        row = conn.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
    return row_to_asset(row) if row else None


def asset_tickets(conn, asset_id, limit=10):
    rows = conn.execute(
        'SELECT * FROM tickets WHERE asset_id=? ORDER BY id DESC LIMIT ?',
        (asset_id, limit)).fetchall()
    return [row_to_ticket(r) for r in rows]

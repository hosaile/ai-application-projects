# -*- coding: utf-8 -*-
"""报修模块：学生端 / 管理端 / 工单 API / 设备资产。"""
import csv
import hmac
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import (Blueprint, Response, after_this_request, jsonify, redirect,
                   request, send_file, send_from_directory, session)

from core import config, paths
from modules.repair import db, triage

bp = Blueprint('repair', __name__)
REPAIR_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

LAST_PING = time.time()
_DEMO = {'active': False, 'prev': None}


def _sla_due(priority, cfg):
    """按优先级计算首次响应/处理时限（默认 高4h / 中24h / 低72h）。"""
    hours = (cfg.get('repair') or {}).get('sla_hours') or {}
    try:
        value = float(hours.get(priority, 24))
    except Exception:
        value = 24.0
    return (datetime.now() + timedelta(hours=value)).strftime('%Y-%m-%d %H:%M:%S')


# ---------------- 管理员口令（可选） ----------------
LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>管理员登录 · 设备运维工作台</title>
<style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#f2f1ec;color:#1b1e23;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}
.box{width:340px;max-width:92vw;background:#fffef9;border:1px solid #d9d6cc;border-radius:10px;
  padding:24px;box-shadow:0 12px 30px rgba(0,0,0,.08)}
h1{font-size:19px;margin:0 0 4px}
p{color:#6b6f76;font-size:13px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;border:1px solid #d9d6cc;border-radius:6px;
  padding:10px 12px;font:inherit}
input:focus{outline:none;border-color:#f0b429;box-shadow:0 0 0 3px rgba(240,180,41,.15)}
button{width:100%;margin-top:12px;background:#1b1e23;color:#fff;border:0;border-radius:6px;
  padding:11px;font:inherit;font-weight:600;cursor:pointer}
.err{color:#a5342b;font-size:13px;margin-top:10px}
a{color:#2f4f6f;font-size:12px;text-decoration:none}
</style></head><body>
<div class="box">
  <h1>管理员登录</h1>
  <p>工单管理、设备资产与数据操作需要口令。</p>
  <form method="post">
    <input type="password" name="password" placeholder="管理员口令" autofocus autocomplete="current-password">
    <button type="submit">登录</button>
    __ERROR__
  </form>
  <p style="margin:14px 0 0"><a href="/">返回工作台</a></p>
</div></body></html>"""


def _admin_password():
    repair = config.load_cfg().get('repair') or {}
    env_name = repair.get('admin_password_env') or 'WORKBENCH_ADMIN_PASSWORD'
    return str(os.environ.get(env_name) or repair.get('admin_password') or '')


@bp.before_request
def _guard_admin():
    """设置了口令才启用；默认本机无口令，不影响现有使用。"""
    password = _admin_password()
    if not password or session.get('admin'):
        return None
    path = request.path
    method = request.method
    protected = False
    if path == '/repair/admin' or path.startswith('/repair/api/data/')             or path.startswith('/repair/api/demo/'):
        protected = True
    elif path.startswith('/repair/api/assets') and method != 'GET':
        protected = True
    elif path.startswith('/repair/api/tickets/') and             (method == 'PATCH' or path.endswith('/experience')):
        protected = True
    if not protected:
        return None
    if path.startswith('/repair/api/'):
        return jsonify({'error': '需要管理员登录', 'login': '/repair/login'}), 401
    nxt = path + (('?' + request.query_string.decode('utf-8', 'replace'))
                  if request.query_string else '')
    return redirect('/repair/login?next=' + quote(nxt, safe=''))


@bp.route('/login', methods=['GET', 'POST'])
def login():
    password = _admin_password()
    if not password:
        return redirect('/repair/admin')
    error = ''
    if request.method == 'POST':
        entered = str(request.form.get('password') or '')
        if entered and hmac.compare_digest(entered, password):
            session['admin'] = True
            nxt = request.args.get('next') or '/repair/admin'
            if not nxt.startswith('/repair/'):
                nxt = '/repair/admin'
            return redirect(nxt)
        error = '<div class="err">口令不正确</div>'
    html = LOGIN_HTML.replace('__ERROR__', error)
    return Response(html, mimetype='text/html; charset=utf-8')


@bp.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect('/repair/login')


# ---------------- 页面 ----------------
@bp.route('/')
def index():
    return send_from_directory(REPAIR_STATIC, 'index.html')


@bp.route('/admin')
def admin():
    return send_from_directory(REPAIR_STATIC, 'admin.html')


@bp.route('/a/<code>')
def asset_page(code):
    return send_from_directory(REPAIR_STATIC, 'asset.html')


# ---------------- 健康检查 / 统一模型信息 ----------------
@bp.route('/api/health')
def health():
    cfg = config.load_cfg()
    info = config.active_info(cfg)
    return jsonify({
        'ok': True,
        'ai_enabled': bool(info.get('configured')),
        'model': info.get('model') or '',
        'model_name': info.get('model_name') or '',
        'api_format': info.get('api_format') or '',
        'configured': bool(info.get('configured')),
        'rag': (cfg.get('repair') or {}).get('kb_mode', 'rag'),
    })


@bp.route('/api/settings', methods=['GET'])
def get_settings():
    """兼容旧管理端：只返回统一配置的当前模型，不返回 Key。"""
    cfg = config.load_cfg()
    info = config.active_info(cfg)
    return jsonify({
        'model': info.get('model') or '',
        'model_name': info.get('model_name') or '',
        'base_url': info.get('base_url') or '',
        'api_format': info.get('api_format') or 'openai',
        'has_key': bool(info.get('has_key')),
        'configured': bool(info.get('configured')),
        'provider_name': info.get('provider_name') or '',
        'unified': True,
        'manage_url': '/',
    })


@bp.route('/api/settings', methods=['POST'])
def save_settings():
    return jsonify({
        'error': '模型已统一配置：请在工作台「模型设置」中修改',
        'manage_url': '/',
    }), 409


# ---------------- 演示模式 ----------------
def _demo_db():
    return os.path.join(paths.demo_dir(), 'tickets_demo.db')


@bp.route('/api/demo/status')
def demo_status():
    return jsonify({
        'active': bool(_DEMO['active']),
        'db_path': db.db_path(),
    })


@bp.route('/api/demo/enter', methods=['POST'])
def demo_enter():
    if not _DEMO['active']:
        _DEMO['prev'] = os.environ.get('WORKBENCH_TICKETS_DB')
        _DEMO['active'] = True
    os.environ['WORKBENCH_TICKETS_DB'] = _demo_db()
    db.init_db()
    from modules.repair import demo_data
    conn = db.get_db()
    n = demo_data.insert_demo(conn, triage.triage_rule, triage.match_kb, reset=True)
    conn.close()
    return jsonify({'ok': True, 'active': True, 'inserted': n, 'db_path': db.db_path()})


@bp.route('/api/demo/exit', methods=['POST'])
def demo_exit():
    if _DEMO['prev'] is None:
        os.environ.pop('WORKBENCH_TICKETS_DB', None)
    else:
        os.environ['WORKBENCH_TICKETS_DB'] = _DEMO['prev']
    _DEMO['active'] = False
    _DEMO['prev'] = None
    return jsonify({'ok': True, 'active': False, 'db_path': db.db_path()})


@bp.route('/api/ping')
def ping():
    global LAST_PING
    LAST_PING = time.time()
    return jsonify({'ok': True})


# ---------------- 数据管理 ----------------
@bp.route('/api/data/info')
def data_info():
    path = db.db_path()
    size = os.path.getsize(path) if os.path.exists(path) else 0
    conn = db.get_db()
    tickets = conn.execute('SELECT COUNT(*) c FROM tickets').fetchone()['c']
    events = conn.execute('SELECT COUNT(*) c FROM events').fetchone()['c']
    assets = conn.execute('SELECT COUNT(*) c FROM assets').fetchone()['c']
    tokens = conn.execute(
        'SELECT COALESCE(SUM(total_tokens),0) t FROM tickets').fetchone()['t']
    conn.close()
    base = os.path.dirname(path)
    return jsonify({
        'db_path': path,
        'db_size': size,
        'tickets': tickets,
        'events': events,
        'assets': assets,
        'total_tokens': tokens,
        'log_path': paths.log_path(),
        'backup_path': os.path.join(base, 'tickets.db.bak'),
    })


@bp.route('/api/data/backup')
def data_backup():
    """用 SQLite 在线备份 API 导出一致性快照，供浏览器下载。"""
    conn = db.get_db()
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        conn.backup(dst)
        dst.close()
    finally:
        conn.close()

    @after_this_request
    def _cleanup(resp):
        try:
            os.remove(tmp)
        except Exception:
            pass
        return resp

    return send_file(tmp, as_attachment=True, download_name='tickets_backup.db')


@bp.route('/api/data/reset', methods=['POST'])
def data_reset():
    from modules.repair import demo_data
    conn = db.get_db()
    n = demo_data.insert_demo(conn, triage.triage_rule, triage.match_kb, reset=True)
    conn.close()
    return jsonify({'ok': True, 'inserted': n})


# ---------------- 工单 API ----------------
@bp.route('/api/tickets', methods=['POST'])
def create_ticket():
    data = request.get_json(silent=True) or {}
    desc = (data.get('description') or '').strip()
    if not desc:
        return jsonify({'error': '请填写报修描述'}), 400
    if len(desc) > 500:
        return jsonify({'error': '描述最多 500 字'}), 400

    location = (data.get('location') or '').strip()[:60]
    reporter = (data.get('reporter') or '').strip()[:20]
    contact = (data.get('contact') or '').strip()[:30]

    conn = db.get_db()
    asset = None
    asset_id = data.get('asset_id')
    asset_code = str(data.get('asset_code') or '').strip()
    if asset_code:
        asset = db.get_asset(conn, code=asset_code)
    elif asset_id:
        try:
            asset = db.get_asset(conn, asset_id=int(asset_id))
        except Exception:
            asset = None
    if asset:
        asset_id = asset['id']
        if not location:
            location = (asset.get('location') or '')[:60]
        desc = desc[:460] + ('\n[设备] %s（%s）' % (asset['name'], asset['code']))
    else:
        asset_id = None

    triage_cfg = config.load_cfg()
    result = triage.triage(desc, location, triage_cfg)
    sla_due_at = _sla_due(result['priority'], triage_cfg)

    # 设备类别是强先验：AP/交换机不应因为描述里出现“灯”被分成水电
    if asset:
        cat = asset.get('category') or ''
        hint = None
        for key, value in (('网络', '网络'), ('门禁', '门锁'), ('水电', '水电'), ('设备', '设备')):
            if key in cat:
                hint = value
                break
        if hint and (result['category'] == '其他' or result['ai_mode'] == 'rule'):
            result['category'] = hint

    # 重复报修检测：同地点（或同设备）+ 同类别且未关闭
    if not data.get('force'):
        row = None
        if asset_id:
            row = conn.execute(
                "SELECT ticket_no, status, created_at FROM tickets "
                "WHERE asset_id=? AND status IN ('pending','processing') "
                "ORDER BY id DESC LIMIT 1", (asset_id,)).fetchone()
        if not row and location:
            row = conn.execute(
                "SELECT ticket_no, status, created_at FROM tickets "
                "WHERE location=? AND category=? AND status IN ('pending','processing') "
                "ORDER BY id DESC LIMIT 1", (location, result['category'])).fetchone()
        if row:
            conn.close()
            return jsonify({
                'duplicate': {
                    'ticket_no': row['ticket_no'],
                    'status': row['status'],
                    'status_label': db.STATUS_LABEL.get(row['status'], row['status']),
                    'created_at': row['created_at'],
                },
                'triage': result,
            })

    usage = result.get('usage') or {}
    with db._TICKET_LOCK:
        ticket_no = db.gen_ticket_no(conn)
        ts = db.now_str()
        cur = conn.execute(
            '''INSERT INTO tickets
               (ticket_no, created_at, updated_at, reporter, contact, location, description,
                category, priority, confidence, ai_summary, ai_checks, ai_sources, ai_mode,
                status, asset_id, prompt_tokens, completion_tokens, total_tokens, ai_ms,
                sla_due_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (ticket_no, ts, ts, reporter, contact, location, desc,
             result['category'], result['priority'], result['confidence'],
             result['summary'], json.dumps(result['checks'], ensure_ascii=False),
             json.dumps(result.get('sources') or [], ensure_ascii=False),
             result['ai_mode'], 'pending', asset_id,
             int(usage.get('prompt_tokens') or 0),
             int(usage.get('completion_tokens') or 0),
             int(usage.get('total_tokens') or 0),
             int(result.get('ai_ms') or 0), sla_due_at))
        conn.execute(
            'INSERT INTO events (ticket_id, from_status, to_status, note, created_at) '
            'VALUES (?,?,?,?,?)',
            (cur.lastrowid, '', 'pending',
             '报修提交，AI 分诊完成' if result['ai_mode'] == 'ai' else '报修提交，规则分诊完成',
             ts))
        conn.commit()
    conn.close()
    return jsonify({'ticket_no': ticket_no, 'triage': result, 'duplicate': None})


@bp.route('/api/tickets')
def list_tickets():
    q = request.args
    sql = 'SELECT * FROM tickets WHERE 1=1'
    args = []
    if q.get('status') in db.STATUSES:
        sql += ' AND status=?'
        args.append(q['status'])
    if q.get('category') in db.CATEGORIES:
        sql += ' AND category=?'
        args.append(q['category'])
    if q.get('priority') in db.PRIORITIES:
        sql += ' AND priority=?'
        args.append(q['priority'])
    if q.get('asset_id'):
        try:
            sql += ' AND asset_id=?'
            args.append(int(q['asset_id']))
        except Exception:
            pass
    sql += ' ORDER BY id DESC LIMIT 200'
    conn = db.get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return jsonify({'tickets': [db.row_to_ticket(r) for r in rows]})


@bp.route('/api/tickets/<ticket_no>')
def get_ticket(ticket_no):
    conn = db.get_db()
    row = conn.execute('SELECT * FROM tickets WHERE ticket_no=?', (ticket_no,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': '工单不存在'}), 404
    events = conn.execute(
        'SELECT * FROM events WHERE ticket_id=? ORDER BY id ASC', (row['id'],)).fetchall()
    asset = db.get_asset(conn, asset_id=row['asset_id']) if row['asset_id'] else None
    conn.close()
    return jsonify({'ticket': db.row_to_ticket(row), 'events': [dict(e) for e in events],
                    'asset': asset})


@bp.route('/api/tickets/<ticket_no>', methods=['PATCH'])
def update_ticket(ticket_no):
    data = request.get_json(silent=True) or {}
    conn = db.get_db()
    row = conn.execute('SELECT * FROM tickets WHERE ticket_no=?', (ticket_no,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': '工单不存在'}), 404

    ts = db.now_str()
    sets, args = [], []
    if data.get('status') in db.STATUSES and data['status'] != row['status']:
        sets.append('status=?')
        args.append(data['status'])
        if data['status'] == 'processing' and not row['first_response_at']:
            sets.append('first_response_at=?')
            args.append(ts)
        if data['status'] == 'done':
            sets.append('done_at=?')
            args.append(ts)
        conn.execute(
            'INSERT INTO events (ticket_id, from_status, to_status, note, created_at) '
            'VALUES (?,?,?,?,?)',
            (row['id'], row['status'], data['status'], str(data.get('note') or '')[:100], ts))
    if data.get('category') in db.CATEGORIES and data['category'] != row['category']:
        sets.append('category=?')
        args.append(data['category'])
        conn.execute(
            'INSERT INTO events (ticket_id, from_status, to_status, note, created_at) '
            'VALUES (?,?,?,?,?)',
            (row['id'], row['status'], row['status'],
             '人工调整类别：%s→%s' % (row['category'], data['category']), ts))
    if 'assignee' in data:
        sets.append('assignee=?')
        args.append(str(data.get('assignee'))[:20])
    if 'note' in data:
        sets.append('note=?')
        args.append(str(data.get('note'))[:200])

    if sets:
        sets.append('updated_at=?')
        args.append(ts)
        args.append(row['id'])
        conn.execute('UPDATE tickets SET ' + ', '.join(sets) + ' WHERE id=?', args)
        conn.commit()

    updated = conn.execute('SELECT * FROM tickets WHERE id=?', (row['id'],)).fetchone()
    events = conn.execute(
        'SELECT * FROM events WHERE ticket_id=? ORDER BY id DESC', (row['id'],)).fetchall()
    conn.close()
    return jsonify({'ticket': db.row_to_ticket(updated),
                    'events': [dict(e) for e in events]})


@bp.route('/api/stats')
def stats():
    conn = db.get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    today_new = conn.execute(
        'SELECT COUNT(*) c FROM tickets WHERE created_at LIKE ?', (today + '%',)).fetchone()['c']
    pending = conn.execute(
        "SELECT COUNT(*) c FROM tickets WHERE status IN ('pending','processing')").fetchone()['c']
    row = conn.execute(
        'SELECT AVG((julianday(done_at)-julianday(created_at))*24) h '
        "FROM tickets WHERE done_at IS NOT NULL AND done_at != ''").fetchone()
    avg_hours = round(row['h'], 1) if row and row['h'] is not None else None
    since = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    cats = conn.execute(
        'SELECT category, COUNT(*) c FROM tickets WHERE created_at >= ? GROUP BY category',
        (since,)).fetchall()
    tokens = conn.execute(
        'SELECT COALESCE(SUM(total_tokens),0) t FROM tickets').fetchone()['t']
    assets = conn.execute('SELECT COUNT(*) c FROM assets').fetchone()['c']
    overdue = conn.execute(
        "SELECT COUNT(*) c FROM tickets WHERE status IN ('pending','processing') "
        "AND sla_due_at != '' AND sla_due_at < ?",
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),)).fetchone()['c']
    conn.close()
    by_cat = {c: 0 for c in db.CATEGORIES}
    for r in cats:
        if r['category'] in by_cat:
            by_cat[r['category']] = r['c']
    return jsonify({
        'today_new': today_new,
        'pending': pending,
        'avg_hours': avg_hours,
        'by_category_7d': by_cat,
        'total_tokens': tokens,
        'assets': assets,
        'overdue': overdue,
    })


# ---------------- 工单经验沉淀（KCS） ----------------
_PHONE = re.compile(r'1[3-9]\d{9}')
_ID_CARD = re.compile(r'\b\d{17}[\dXx]\b')
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
_KCS_LOCK = threading.Lock()


def _mask_text(text, ticket=None):
    """脱敏：手机号、身份证、邮箱，以及工单里填写的姓名/联系方式。"""
    text = _PHONE.sub('1**********', text or '')
    text = _ID_CARD.sub('***', text)
    text = _EMAIL.sub('***', text)
    if ticket:
        for value in (ticket.get('reporter'), ticket.get('contact')):
            value = (value or '').strip()
            if value and len(value) >= 2:
                text = text.replace(value, '***')
    return text


def _mask_location(location):
    """保留楼栋/楼层，去掉具体房间号。"""
    text = str(location or '')
    text = re.sub(r'(\d{1,3}\s*号楼)\s*\d{2,4}', r'\1', text)
    text = re.sub(r'(\d{1,2}\s*层)\s*\d{2,4}', r'\1', text)
    return text.strip() or '未填写'


def _experience_draft(ticket, events):
    checks = ticket.get('ai_checks') or []
    notes = []
    for e in events or []:
        note = (e.get('note') or '').strip()
        if note:
            notes.append('- %s %s' % (e.get('created_at', ''), note))
    if not notes:
        notes.append('- （暂无处理记录，请补充）')
    lines = [
        '# 工单经验：%s' % ticket.get('ticket_no', ''),
        '',
        '- 类别：%s' % ticket.get('category', ''),
        '- 优先级：%s' % ticket.get('priority', ''),
        '- 地点：%s' % _mask_location(ticket.get('location')),
        '- 完成时间：%s' % (ticket.get('done_at') or ''),
        '',
        '## 现象',
        _mask_text(ticket.get('description') or '', ticket),
        '',
        '## 处理过程',
        '\n'.join(notes),
        '',
        '## 学生可先自查',
        '\n'.join('- %s' % _mask_text(c) for c in checks) or '- （无）',
        '',
        '## 结论',
        _mask_text(ticket.get('note') or '（请管理员补充最终原因与处理方案）', ticket),
        '',
        '> 来源：工单 %s；已脱敏，仅保留楼栋/楼层。' % ticket.get('ticket_no', ''),
    ]
    return '\n'.join(lines)


def _experience_path(ticket_no):
    safe = re.sub(r'[^A-Za-z0-9_-]', '', ticket_no or '')
    folder = os.path.join(paths.docs_dir(), 'experience')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, '工单_%s.md' % safe), 'experience/工单_%s.md' % safe


@bp.route('/api/tickets/<ticket_no>/experience', methods=['GET', 'POST'])
def ticket_experience(ticket_no):
    conn = db.get_db()
    row = conn.execute('SELECT * FROM tickets WHERE ticket_no=?', (ticket_no,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': '工单不存在'}), 404
    ticket = db.row_to_ticket(row)
    events = [dict(e) for e in conn.execute(
        'SELECT * FROM events WHERE ticket_id=? ORDER BY id ASC', (row['id'],)).fetchall()]
    conn.close()

    path, rel = _experience_path(ticket_no)
    if request.method == 'GET':
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                content = f.read()
            return jsonify({'ok': True, 'exists': True, 'path': rel, 'content': content})
        return jsonify({'ok': True, 'exists': False, 'path': rel,
                        'content': _experience_draft(ticket, events)})

    data = request.get_json(silent=True) or {}
    if ticket.get('status') not in ('done', 'closed'):
        return jsonify({'error': '只能沉淀已完成/已关闭的工单'}), 400
    content = str(data.get('content') or '').strip()
    if not content:
        content = _experience_draft(ticket, events)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content.rstrip() + '\n')

    result = {'ok': True, 'exists': True, 'path': rel, 'rebuilt': False}
    if data.get('rebuild'):
        with _KCS_LOCK:
            try:
                from modules.qa.rag import ingest, store
                cfg = config.load_cfg()
                chunks = ingest.build_chunks(ingest.load_docs(paths.docs_dir()),
                                             cfg['chunk_size'], cfg['chunk_overlap'])
                n = store.rebuild(chunks, paths.chroma_dir(), cfg['embedding_model'])
                result['rebuilt'] = True
                result['chunks'] = n
            except Exception as e:
                result['warning'] = '已保存，但重建索引失败：%s' % str(e)[:160]
    return jsonify(result)


# ---------------- 运营指标 ----------------
@bp.route('/api/metrics')
def metrics():
    """用真实工单回答“这套系统有没有用”：AI 占比、SLA 达成、重复报修、经验沉淀。"""
    conn = db.get_db()
    since = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    total = conn.execute('SELECT COUNT(*) c FROM tickets').fetchone()['c']
    last30 = conn.execute(
        'SELECT COUNT(*) c FROM tickets WHERE created_at >= ?', (since,)).fetchone()['c']
    ai30 = conn.execute(
        "SELECT COUNT(*) c FROM tickets WHERE created_at >= ? AND ai_mode='ai'",
        (since,)).fetchone()['c']
    done_rows = conn.execute(
        "SELECT done_at, sla_due_at FROM tickets WHERE status IN ('done','closed') "
        "AND done_at IS NOT NULL AND done_at != '' AND sla_due_at != ''").fetchall()
    on_time = sum(1 for r in done_rows if r['done_at'] <= r['sla_due_at'])
    repeat = conn.execute(
        'SELECT COUNT(*) c FROM tickets t WHERE t.created_at >= ? AND '
        '(SELECT COUNT(*) FROM tickets x WHERE x.location=t.location '
        'AND x.category=t.category AND x.created_at >= ?) > 1',
        (since, since)).fetchone()['c']
    avg_row = conn.execute(
        'SELECT AVG((julianday(done_at)-julianday(created_at))*24) h FROM tickets '
        "WHERE done_at IS NOT NULL AND done_at != ''").fetchone()
    conn.close()
    exp_dir = os.path.join(paths.docs_dir(), 'experience')
    try:
        exp_docs = len([f for f in os.listdir(exp_dir) if f.endswith('.md')])
    except Exception:
        exp_docs = 0

    def rate(a, b):
        return round(a / float(b), 3) if b else None

    return jsonify({
        'total': total,
        'last30': last30,
        'ai_rate': rate(ai30, last30),
        'sla_rate': rate(on_time, len(done_rows)),
        'repeat_rate': rate(repeat, last30),
        'experience_docs': exp_docs,
        'avg_hours': round(avg_row['h'], 1) if avg_row and avg_row['h'] is not None else None,
    })


# ---------------- 相似工单 ----------------
def _bigrams(text):
    text = ''.join(ch for ch in (text or '').lower() if not ch.isspace())
    return {text[i:i + 2] for i in range(max(0, len(text) - 1))}


def _similarity(a, b):
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    # 用重叠系数而不是 Jaccard：短描述也能匹配到较长的历史工单
    return len(ga & gb) / float(min(len(ga), len(gb)))


@bp.route('/api/similar')
def similar_tickets():
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify({'items': []})
    category = request.args.get('category')
    conn = db.get_db()
    sql = "SELECT * FROM tickets WHERE status IN ('done','closed')"
    args = []
    if category in db.CATEGORIES:
        sql += ' AND category=?'
        args.append(category)
    sql += ' ORDER BY id DESC LIMIT 300'
    rows = conn.execute(sql, args).fetchall()
    conn.close()

    items = []
    for r in rows:
        score = _similarity(q, r['description'])
        if score < 0.12:
            continue
        items.append({
            'ticket_no': r['ticket_no'],
            'description': r['description'][:120],
            'category': r['category'],
            'priority': r['priority'],
            'status': r['status'],
            'status_label': db.STATUS_LABEL.get(r['status'], r['status']),
            'summary': (r['ai_summary'] or '').strip(),
            'resolution': (r['note'] or '').strip()[:160],
            'done_at': r['done_at'] or '',
            'score': round(score, 3),
        })
    items.sort(key=lambda x: -x['score'])
    return jsonify({'items': items[:5]})


# ---------------- 设备资产 ----------------
def _asset_payload(data):
    code = str(data.get('code') or '').strip()[:40]
    name = str(data.get('name') or '').strip()[:80]
    if not code:
        raise ValueError('设备编号不能为空')
    if not name:
        raise ValueError('设备名称不能为空')
    return {
        'code': code,
        'name': name,
        'category': str(data.get('category') or '其他')[:20],
        'location': str(data.get('location') or '')[:60],
        'owner': str(data.get('owner') or '')[:20],
        'status': str(data.get('status') or '在用')[:20],
        'warranty_until': str(data.get('warranty_until') or '')[:20],
        'vendor': str(data.get('vendor') or '')[:60],
        'manual': str(data.get('manual') or '')[:120],
        'note': str(data.get('note') or '')[:300],
    }


@bp.route('/api/assets', methods=['GET'])
def list_assets():
    conn = db.get_db()
    items = db.list_assets(conn, (request.args.get('q') or '').strip())
    conn.close()
    return jsonify({'assets': items})


@bp.route('/api/assets', methods=['POST'])
def create_asset():
    try:
        payload = _asset_payload(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    ts = db.now_str()
    conn = db.get_db()
    try:
        cur = conn.execute(
            '''INSERT INTO assets
               (code, name, category, location, owner, status, warranty_until, vendor,
                manual, note, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (payload['code'], payload['name'], payload['category'], payload['location'],
             payload['owner'], payload['status'], payload['warranty_until'],
             payload['vendor'], payload['manual'], payload['note'], ts, ts))
        conn.commit()
        asset = db.get_asset(conn, asset_id=cur.lastrowid)
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': '设备编号已存在'}), 409
    conn.close()
    return jsonify({'ok': True, 'asset': asset})


@bp.route('/api/assets/import', methods=['POST'])
def import_assets():
    """CSV 台账导入：按 code 存在则更新，不存在则新增。"""
    raw = ''
    if 'file' in request.files:
        raw = request.files['file'].read().decode('utf-8-sig', 'replace')
    else:
        raw = str((request.get_json(silent=True) or {}).get('csv') or '')
    if not raw.strip():
        return jsonify({'error': 'CSV 内容为空'}), 400
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or 'code' not in [f.strip() for f in reader.fieldnames]:
        return jsonify({'error': 'CSV 至少需要 code 与 name 两列'}), 400

    conn = db.get_db()
    inserted = updated = 0
    skipped = []
    ts = db.now_str()
    for row in reader:
        row = {str(k or '').strip(): (v or '').strip() for k, v in row.items()}
        try:
            payload = _asset_payload(row)
        except ValueError as e:
            skipped.append('%s(%s)' % (row.get('code') or '未命名', e))
            continue
        exists = conn.execute('SELECT id FROM assets WHERE code=?',
                              (payload['code'],)).fetchone()
        if exists:
            conn.execute(
                '''UPDATE assets SET name=?, category=?, location=?, owner=?, status=?,
                   warranty_until=?, vendor=?, manual=?, note=?, updated_at=? WHERE id=?''',
                (payload['name'], payload['category'], payload['location'], payload['owner'],
                 payload['status'], payload['warranty_until'], payload['vendor'],
                 payload['manual'], payload['note'], ts, exists['id']))
            updated += 1
        else:
            conn.execute(
                '''INSERT INTO assets
                   (code, name, category, location, owner, status, warranty_until, vendor,
                    manual, note, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (payload['code'], payload['name'], payload['category'], payload['location'],
                 payload['owner'], payload['status'], payload['warranty_until'],
                 payload['vendor'], payload['manual'], payload['note'], ts, ts))
            inserted += 1
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'inserted': inserted, 'updated': updated,
                    'skipped': skipped})


@bp.route('/api/assets/<int:asset_id>', methods=['GET', 'PATCH', 'DELETE'])
def asset_detail(asset_id):
    conn = db.get_db()
    asset = db.get_asset(conn, asset_id=asset_id)
    if not asset:
        conn.close()
        return jsonify({'error': '设备不存在'}), 404
    if request.method == 'GET':
        tickets = db.asset_tickets(conn, asset_id)
        conn.close()
        return jsonify({'asset': asset, 'tickets': tickets})
    if request.method == 'DELETE':
        conn.execute('DELETE FROM assets WHERE id=?', (asset_id,))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    try:
        payload = _asset_payload({**asset, **(request.get_json(silent=True) or {})})
    except ValueError as e:
        conn.close()
        return jsonify({'error': str(e)}), 400
    try:
        conn.execute(
            '''UPDATE assets SET code=?, name=?, category=?, location=?, owner=?, status=?,
               warranty_until=?, vendor=?, manual=?, note=?, updated_at=? WHERE id=?''',
            (payload['code'], payload['name'], payload['category'], payload['location'],
             payload['owner'], payload['status'], payload['warranty_until'],
             payload['vendor'], payload['manual'], payload['note'], db.now_str(), asset_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': '设备编号已存在'}), 409
    asset = db.get_asset(conn, asset_id=asset_id)
    conn.close()
    return jsonify({'ok': True, 'asset': asset})


@bp.route('/api/assets/by-code/<code>')
def asset_by_code(code):
    conn = db.get_db()
    asset = db.get_asset(conn, code=code)
    if not asset:
        conn.close()
        return jsonify({'error': '设备不存在'}), 404
    tickets = db.asset_tickets(conn, asset['id'])
    conn.close()
    return jsonify({'asset': asset, 'tickets': tickets})


@bp.route('/api/assets/<int:asset_id>/qr.png')
def asset_qr(asset_id):
    import qrcode
    conn = db.get_db()
    asset = db.get_asset(conn, asset_id=asset_id)
    conn.close()
    if not asset:
        return jsonify({'error': '设备不存在'}), 404
    app_cfg = config.load_cfg().get('app') or {}
    base = (app_cfg.get('public_url') or request.url_root).rstrip('/')
    target = base + '/a/' + quote(asset['code'])
    img = qrcode.make(target)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png',
                     download_name='asset_%s_qr.png' % asset['code'])

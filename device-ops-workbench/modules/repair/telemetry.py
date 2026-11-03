# -*- coding: utf-8 -*-
"""硬件遥测接入：令牌校验、幂等、遥测入库、规则告警、自动建单。

当前用于模拟/单点硬件验证；真实硬件未接入。
"""
import json
import math
import os
from datetime import datetime, timedelta

from core import config
from modules.repair import db

OPS = ('>', '>=', '<', '<=', '==', '!=')


def ingest_token(cfg=None):
    cfg = cfg or config.load_cfg()
    hw = cfg.get('hardware') or {}
    env_name = hw.get('ingest_token_env') or 'WORKBENCH_INGEST_TOKEN'
    return str(os.environ.get(env_name) or hw.get('ingest_token') or '')


def enabled(cfg=None):
    cfg = cfg or config.load_cfg()
    return bool((cfg.get('hardware') or {}).get('enabled'))


def _num(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
        if math.isfinite(value):
            return value
    if isinstance(value, str):
        try:
            value = float(value)
            if math.isfinite(value):
                return value
        except Exception:
            pass
    return None


def _ts(raw):
    if not raw:
        return db.now_str()
    try:
        text = str(raw).strip().replace('Z', '+00:00')
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        # 时钟异常保护：未来超过 10 分钟按当前时间记
        if dt > datetime.now() + timedelta(minutes=10):
            return db.now_str()
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return db.now_str()


def _rules(cfg):
    raw = (cfg.get('hardware') or {}).get('rules')
    if not isinstance(raw, list) or not raw:
        return []
    out = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        metric = str(item.get('metric') or '').strip()
        op = str(item.get('op') or '').strip()
        if not metric or op not in OPS:
            continue
        value = _num(item.get('value'))
        if value is None:
            continue
        out.append({
            'key': str(item.get('key') or metric)[:40],
            'metric': metric[:40],
            'op': op,
            'value': value,
            'level': str(item.get('level') or '中')[:4],
            'message': str(item.get('message') or '设备指标异常')[:120],
        })
    return out


def _hit(value, op, target):
    return {
        '>': value > target,
        '>=': value >= target,
        '<': value < target,
        '<=': value <= target,
        '==': abs(value - target) < 1e-9,
        '!=': abs(value - target) >= 1e-9,
    }.get(op, False)


def _asset_category(asset):
    cat = asset.get('category') or ''
    for key, value in (('网络', '网络'), ('门禁', '门锁'), ('水电', '水电'),
                       ('监控', '设备'), ('办公', '设备')):
        if key in cat:
            return value
    return '设备'


def _sla_due(priority, cfg):
    hours = (cfg.get('repair') or {}).get('sla_hours') or {}
    try:
        value = float(hours.get(priority, 24))
    except Exception:
        value = 24.0
    return (datetime.now() + timedelta(hours=value)).strftime('%Y-%m-%d %H:%M:%S')


def _create_ticket(conn, asset, rule, value, cfg):
    """硬件高危告警直接建单，不经过 LLM，避免告警被模型延迟拖住。"""
    category = _asset_category(asset)
    priority = rule['level'] if rule['level'] in ('高', '中', '低') else '中'
    desc = ('[硬件告警] %s（%s）%s，当前值 %s'
            % (asset.get('name'), asset.get('code'), rule['message'], value))[:500]
    ts = db.now_str()
    checks = ['检查设备供电与接线', '查看最近遥测曲线', '必要时到现场确认']
    with db._TICKET_LOCK:
        ticket_no = db.gen_ticket_no(conn)
        cur = conn.execute(
            '''INSERT INTO tickets
               (ticket_no, created_at, updated_at, reporter, contact, location, description,
                category, priority, confidence, ai_summary, ai_checks, ai_sources, ai_mode,
                status, asset_id, sla_due_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (ticket_no, ts, ts, '硬件告警', '', asset.get('location') or '', desc,
             category, priority, 1.0, rule['message'],
             json.dumps(checks, ensure_ascii=False), '[]', 'rule', 'pending',
             asset['id'], _sla_due(priority, cfg)))
        conn.execute(
            'INSERT INTO events (ticket_id, from_status, to_status, note, created_at) '
            'VALUES (?,?,?,?,?)',
            (cur.lastrowid, '', 'pending', '硬件告警自动建单：%s' % rule['message'], ts))
    return ticket_no


def ingest(data, cfg=None):
    cfg = cfg or config.load_cfg()
    if not isinstance(data, dict):
        raise ValueError('请求格式错误')
    code = str(data.get('asset_code') or '').strip()
    if not code:
        raise ValueError('asset_code 不能为空')
    metrics = data.get('metrics')
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError('metrics 不能为空')
    units = data.get('units') if isinstance(data.get('units'), dict) else {}
    values = {}
    for key, raw in list(metrics.items())[:20]:
        metric = str(key or '').strip()[:40]
        value = _num(raw)
        if metric and value is not None:
            values[metric] = value
    if not values:
        raise ValueError('metrics 中没有有效数值')

    conn = db.get_db()
    try:
        asset = db.get_asset(conn, code=code)
        if not asset:
            raise ValueError('设备不存在：%s' % code)
        msg_id = str(data.get('msg_id') or '').strip()[:100]
        if msg_id:
            exists = conn.execute('SELECT 1 FROM device_messages WHERE msg_id=?',
                                  (msg_id,)).fetchone()
            if exists:
                return {'ok': True, 'duplicate': True, 'accepted': 0, 'alerts': []}
        ts = _ts(data.get('ts'))
        source = str(data.get('source') or 'hardware')[:40]
        if msg_id:
            try:
                conn.execute(
                    'INSERT INTO device_messages (msg_id, asset_id, ts, created_at) '
                    'VALUES (?,?,?,?)', (msg_id, asset['id'], ts, db.now_str()))
            except Exception:
                return {'ok': True, 'duplicate': True, 'accepted': 0, 'alerts': []}
        for metric, value in values.items():
            conn.execute(
                'INSERT INTO device_telemetry (asset_id, metric, value, unit, ts, source, '
                'msg_id, created_at) VALUES (?,?,?,?,?,?,?,?)',
                (asset['id'], metric, value, str(units.get(metric) or '')[:20],
                 ts, source, msg_id, db.now_str()))
        conn.execute('UPDATE assets SET last_seen=?, online=1, updated_at=? WHERE id=?',
                     (ts, db.now_str(), asset['id']))

        alerts = []
        cooldown = int((cfg.get('hardware') or {}).get('alert_cooldown_minutes') or 10)
        cutoff = (datetime.now() - timedelta(minutes=max(1, cooldown))).strftime(
            '%Y-%m-%d %H:%M:%S')
        auto_ticket = bool((cfg.get('hardware') or {}).get('auto_ticket', True))
        for rule in _rules(cfg):
            if rule['metric'] not in values:
                continue
            value = values[rule['metric']]
            if not _hit(value, rule['op'], rule['value']):
                continue
            recent = conn.execute(
                'SELECT 1 FROM device_alerts WHERE asset_id=? AND rule_key=? '
                'AND created_at >= ? LIMIT 1',
                (asset['id'], rule['key'], cutoff)).fetchone()
            if recent:
                continue
            ts_now = db.now_str()
            cur = conn.execute(
                '''INSERT INTO device_alerts
                   (asset_id, rule_key, level, message, metric, value, status,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                (asset['id'], rule['key'], rule['level'], rule['message'],
                 rule['metric'], value, 'open', ts_now, ts_now))
            alert_id = cur.lastrowid
            ticket_no = ''
            if auto_ticket and rule['level'] == '高':
                try:
                    ticket_no = _create_ticket(conn, asset, rule, value, cfg)
                    conn.execute(
                        'UPDATE device_alerts SET ticket_no=?, updated_at=? WHERE id=?',
                        (ticket_no, db.now_str(), alert_id))
                except Exception:
                    pass
            alerts.append({'id': alert_id, 'rule': rule['key'],
                           'level': rule['level'], 'message': rule['message'],
                           'value': value, 'ticket_no': ticket_no})
        conn.commit()
        return {'ok': True, 'duplicate': False, 'accepted': len(values),
                'asset_code': code, 'ts': ts, 'alerts': alerts}
    finally:
        conn.close()


def latest(conn, asset_id, limit=50):
    rows = conn.execute(
        'SELECT metric, value, unit, ts, source FROM device_telemetry '
        'WHERE asset_id=? ORDER BY id DESC LIMIT ?',
        (asset_id, max(1, min(200, int(limit))))).fetchall()
    return [dict(r) for r in rows]


def latest_by_metric(conn, asset_id, limit=50):
    """每个指标取最新一条，方便资产页显示。"""
    rows = latest(conn, asset_id, limit)
    out = {}
    for row in rows:
        if row['metric'] not in out:
            out[row['metric']] = row
    return list(out.values())


def alerts(conn, status=None, limit=50):
    sql = ('SELECT a.*, s.name AS asset_name, s.code AS asset_code '
           'FROM device_alerts a LEFT JOIN assets s ON s.id=a.asset_id')
    args = []
    if status in ('open', 'ack', 'closed'):
        sql += ' WHERE a.status=?'
        args.append(status)
    sql += ' ORDER BY a.id DESC LIMIT ?'
    args.append(max(1, min(200, int(limit))))
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def ack_alert(conn, alert_id, status='ack'):
    if status not in ('open', 'ack', 'closed'):
        return None
    conn.execute('UPDATE device_alerts SET status=?, updated_at=? WHERE id=?',
                 (status, db.now_str(), alert_id))
    conn.commit()
    row = conn.execute('SELECT * FROM device_alerts WHERE id=?', (alert_id,)).fetchone()
    return dict(row) if row else None

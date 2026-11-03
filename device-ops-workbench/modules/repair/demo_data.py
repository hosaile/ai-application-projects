# -*- coding: utf-8 -*-
"""演示数据：seed.py 与 app.py 的“重置演示数据”共用。"""
import json
import random
from datetime import datetime, timedelta

# (描述, 地点, 姓名, 状态, 分诊方式)
ITEMS = [
    ('3号楼502宿舍网断了，路由器指示灯不亮，手机连 WiFi 也上不了', '3号楼502', '小林', 'pending', 'ai'),
    ('图书馆三楼自习区 WiFi 连不上，一直转圈', '图书馆三楼', '小王', 'done', 'ai'),
    ('实验楼401网速特别慢，打开网页要等很久', '实验楼401', '小陈', 'processing', 'ai'),
    ('整栋教学楼都断网了，上课没法投屏', '教学楼A', '张老师', 'pending', 'ai'),
    ('2号楼301卫生间水管漏水，地面都是水', '2号楼301', '小周', 'processing', 'ai'),
    ('5号楼走廊灯不亮，晚上看不清路', '5号楼3层', '小李', 'done', 'ai'),
    ('宿舍楼停水了，水龙头没水', '6号楼', '小吴', 'done', 'ai'),
    ('实训室插座没电，两台电脑开不了机', '实训楼205', '小郑', 'processing', 'ai'),
    ('4号楼208门锁刷卡没反应，进不去', '4号楼208', '小冯', 'done', 'rule'),
    ('图书馆自习室门打不开，里面还有人', '图书馆四楼', '小何', 'processing', 'ai'),
    ('教学楼A302投影仪开不了机，下午有课', '教学楼A302', '刘老师', 'done', 'ai'),
    ('宿舍空调不制冷，出风是热的', '7号楼412', '小许', 'pending', 'ai'),
    ('一卡通打印机卡纸了，后面排了好多人', '一卡通中心', '小方', 'closed', 'rule'),
    ('操场看台第三排座椅损坏，横杆断了', '操场看台', '小唐', 'closed', 'rule'),
    ('宿舍楼门禁识别不了卡，刷几次都没反应', '8号楼门禁', '小宋', 'done', 'ai'),
]

NOTES = {
    'processing': '已派单，师傅上门中',
    'done': '维修完成，已回访',
    'closed': '确认处理完毕，归档',
}


def insert_demo(conn, triage_rule, match_kb, reset=False):
    """写入 15 条演示工单，返回写入条数。

    triage_rule / match_kb 由调用方（app 模块）传入，避免循环导入。
    """
    if reset:
        conn.executescript('DELETE FROM events; DELETE FROM tickets;')
        try:
            conn.execute('DELETE FROM sqlite_sequence')
        except Exception:
            pass

    now = datetime.now()
    n = 0
    for desc, loc, who, status, mode in ITEMS:
        tri = triage_rule(desc, match_kb(desc))
        created = now - timedelta(
            days=random.randint(0, 6), hours=random.randint(1, 9), minutes=random.randint(0, 59))
        ts = created.strftime('%Y-%m-%d %H:%M:%S')
        done_at = None
        if status in ('done', 'closed'):
            done_at = (created + timedelta(hours=round(random.uniform(0.6, 16), 1))).strftime('%Y-%m-%d %H:%M:%S')
        conf = 0.6 if mode == 'rule' else round(random.uniform(0.72, 0.95), 2)
        sla_hours = {'高': 4, '中': 24, '低': 72}.get(tri['priority'], 24)
        sla_due = (created + timedelta(hours=sla_hours)).strftime('%Y-%m-%d %H:%M:%S')
        # 演示数据不虚构模型消耗：token/耗时统一记 0，累计 AI 用量只反映真实调用
        p_tok, c_tok, t_tok, ms = 0, 0, 0, 0

        prefix = 'BX' + created.strftime('%Y%m%d')
        seq = conn.execute(
            'SELECT COUNT(*) c FROM tickets WHERE ticket_no LIKE ?', (prefix + '%',)).fetchone()['c'] + 1
        ticket_no = '%s%03d' % (prefix, seq)

        cur = conn.execute(
            '''INSERT INTO tickets
               (ticket_no, created_at, updated_at, done_at, reporter, contact, location, description,
                category, priority, confidence, ai_summary, ai_checks, ai_mode, status, assignee, note,
                prompt_tokens, completion_tokens, total_tokens, ai_ms, sla_due_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (ticket_no, ts, ts, done_at, who, '138****' + str(random.randint(1000, 9999)),
             loc, desc, tri['category'], tri['priority'], conf, tri['summary'],
             json.dumps(tri['checks'], ensure_ascii=False), mode, status,
             random.choice(['王师傅', '李师傅', '陈师傅', '']) if status != 'pending' else '',
             NOTES.get(status, ''), p_tok, c_tok, t_tok, ms, sla_due))
        tid = cur.lastrowid
        events = [(tid, '', 'pending',
                   '报修提交，AI 分诊完成' if mode == 'ai' else '报修提交，规则分诊完成', ts)]
        if status in ('processing', 'done', 'closed'):
            t1 = (created + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            events.append((tid, 'pending', 'processing', '派单给维修师傅', t1))
        if status in ('done', 'closed'):
            events.append((tid, 'processing', 'done', '维修完成', done_at))
        if status == 'closed':
            events.append((tid, 'done', 'closed', '确认归档', done_at))
        conn.executemany(
            'INSERT INTO events (ticket_id, from_status, to_status, note, created_at) '
            'VALUES (?,?,?,?,?)', events)
        n += 1
    conn.commit()
    return n

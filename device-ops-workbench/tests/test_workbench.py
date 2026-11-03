# -*- coding: utf-8 -*-
"""设备运维智能工作台 · 统一 smoke / 回归测试。

覆盖：三入口、统一模型配置、RAG 检索、报修分诊（AI + 规则兜底）、
工单流转、相似工单、设备资产/二维码、演示模式、全局搜索、Key 脱敏。

运行：python tests/test_workbench.py
退出码：0 全部通过 / 1 有失败
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_KEY = 'fake-' + os.urandom(8).hex()
INGEST_TOKEN = 'ingest-' + os.urandom(6).hex()
BASE = 'http://127.0.0.1:8102'
MOCK = 'http://127.0.0.1:8799'
passed = 0
failed = 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print('  PASS  ' + name)
    else:
        failed += 1
        print('  FAIL  ' + name + (('  -> ' + str(detail)) if detail else ''))


def wait_port(host, port, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def wait_ready(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(BASE + '/api/health', timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def build_config(tmp):
    with open(os.path.join(ROOT, 'config.example.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    cfg['app']['port'] = 8102
    cfg['providers'][0]['base_url'] = MOCK + '/v1'
    cfg['providers'][0]['api_key'] = FAKE_KEY
    cfg['providers'][0]['timeout'] = 30
    cfg['hardware'] = {
        'enabled': True,
        'ingest_token': '',
        'ingest_token_env': 'WORKBENCH_INGEST_TOKEN',
        'auto_ticket': True,
        'alert_cooldown_minutes': 10,
        'offline_minutes': 5,
        'rules': [
            {'key': 'temp_high', 'metric': 'temperature', 'op': '>', 'value': 40,
             'level': '高', 'message': '设备温度过高'},
            {'key': 'current_zero', 'metric': 'current', 'op': '<=', 'value': 0.05,
             'level': '中', 'message': '设备电流为 0，可能断电'},
            {'key': 'door_open', 'metric': 'door', 'op': '>=', 'value': 1,
             'level': '中', 'message': '机柜门被打开'},
        ],
    }
    path = os.path.join(tmp, 'config.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def set_key(config_path, key, base_url=None):
    with open(config_path, encoding='utf-8') as f:
        cfg = json.load(f)
    cfg['providers'][0]['api_key'] = key
    if base_url:
        cfg['providers'][0]['base_url'] = base_url
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def main():
    tmp = tempfile.mkdtemp(prefix='workbench_test_')
    data = os.path.join(tmp, 'data')
    os.makedirs(data)
    for sub in ('docs', 'chroma', 'tests'):
        src = os.path.join(ROOT, 'data', sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(data, sub))
    config_path = build_config(tmp)

    mock = subprocess.Popen([sys.executable, os.path.join(ROOT, 'tests', 'mock_llm.py'),
                             '8799'], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = dict(os.environ, WORKBENCH_DATA_DIR=data, WORKBENCH_CONFIG=config_path,
               PORT='8102', PYTHONIOENCODING='utf-8', USE_TF='0',
               WORKBENCH_INGEST_TOKEN=INGEST_TOKEN)
    proc = subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        check('mock 模型服务启动', wait_port('127.0.0.1', 8799, 20))
        check('工作台服务启动', wait_ready())
        if failed:
            return 1

        print('\n-- 入口与统一配置 --')
        h = requests.get(BASE + '/api/health', timeout=10).json()
        check('健康检查三模块齐全', h.get('ok') and h.get('qa') and h.get('repair')
              and h.get('field'))
        check('只配置 DeepSeek V4.1 Flash', h.get('model') == 'deepseek-v4.1-flash',
              h.get('model'))
        for path, name in (('/', '问答首页'), ('/start', '角色首页'),
                           ('/repair/', '报修学生端'), ('/repair/admin', '工单管理端'),
                           ('/field/', '现场排障包装页'), ('/field/assistant.html', '离线助手'),
                           ('/a/NET-AP-001', '设备扫码页')):
            check(name + ' 200', requests.get(BASE + path, timeout=10).status_code == 200)
        assistant = requests.get(BASE + '/field/assistant.html', timeout=10)
        check('离线助手内容完整', len(assistant.content) > 100000)

        models = requests.get(BASE + '/api/settings/models', timeout=10)
        pub = models.json()
        text = json.dumps(pub, ensure_ascii=False)
        check('模型接口不返回 Key 明文', FAKE_KEY not in text and 'api_key' not in text)
        check('供应商数量为 1', len(pub.get('providers') or []) == 1)
        check('模型数量为 1', len(pub['providers'][0]['models']) == 1)
        check('模型 ID 正确', pub['providers'][0]['models'][0]['model_id']
              == 'deepseek-v4.1-flash')
        check('当前模型正确', pub['active_info']['model'] == 'deepseek-v4.1-flash')
        check('当前模型已配置', pub['active_info']['configured'] is True)

        print('\n-- RAG 问答 --')
        stats = requests.get(BASE + '/api/stats', timeout=30).json()
        check('索引知识块 > 80', stats.get('count', 0) > 80, stats.get('count'))
        docs = requests.get(BASE + '/api/docs', timeout=10).json()
        check('知识库 9 篇文档', docs.get('total') == 9, docs.get('total'))
        ev = requests.get(BASE + '/api/eval', timeout=10).json()
        check('评估指标完整', 'Recall@5' in (ev.get('metrics') or {})
              and 'mrr' in ev)
        ask = requests.post(BASE + '/api/ask',
                            json={'question': '光猫 LOS 红灯怎么办', 'top_k': 5},
                            timeout=120).json()
        check('LLM 问答模式', ask.get('mode') == 'llm', ask.get('mode'))
        check('回答来自 mock 模型', 'MOCK' in (ask.get('answer') or ''))
        check('回答带来源', bool(ask.get('sources')))
        check('返回当前模型', (ask.get('model') or {}).get('model') == 'deepseek-v4.1-flash')
        check('检索命中 >= 1', len(ask.get('hits') or []) >= 1)
        check('空问题 400', requests.post(BASE + '/api/ask', json={'question': ''}).status_code == 400)
        check('超长问题 400',
              requests.post(BASE + '/api/ask', json={'question': 'x' * 301}).status_code == 400)
        check('上传路径穿越被拒',
              requests.post(BASE + '/api/upload',
                            files={'files': ('../evil.md', b'x')}).status_code == 200
              and 'evil' not in ''.join(
                  requests.post(BASE + '/api/upload',
                                files={'files': ('../evil.md', b'x')}).json().get('saved', [])))
        fb = requests.post(BASE + '/api/feedback',
                           json={'question': '测试', 'helpful': True, 'mode': 'llm'})
        check('反馈写入成功', fb.status_code == 200 and fb.json().get('ok'))

        print('\n-- 报修分诊（AI + 来源） --')
        r = requests.post(BASE + '/repair/api/tickets',
                          json={'description': '实验楼305交换机端口灯不亮，整层上不了网',
                                'location': '实验楼305'}, timeout=120)
        d = r.json()
        check('AI 分诊创建工单', r.status_code == 200 and bool(d.get('ticket_no')))
        check('AI 分诊模式', d.get('triage', {}).get('ai_mode') == 'ai',
              d.get('triage', {}).get('ai_mode'))
        check('AI 分类为网络', d.get('triage', {}).get('category') == '网络')
        check('分诊带知识库来源', len(d.get('triage', {}).get('sources') or []) >= 1)
        check('记录 token 用量', d.get('triage', {}).get('usage', {}).get('total_tokens', 0) > 0)
        no = d.get('ticket_no')

        det = requests.get(BASE + '/repair/api/tickets/' + no, timeout=10).json()
        check('工单详情与事件', det.get('ticket', {}).get('ticket_no') == no
              and len(det.get('events') or []) >= 1)
        check('详情保存分诊来源', len(det['ticket'].get('ai_sources') or []) >= 1)

        dup = requests.post(BASE + '/repair/api/tickets',
                            json={'description': '实验楼305交换机还是不通',
                                  'location': '实验楼305'}, timeout=30).json()
        check('重复报修提示', bool(dup.get('duplicate')))
        forced = requests.post(BASE + '/repair/api/tickets',
                               json={'description': '实验楼305交换机仍不通',
                                     'location': '实验楼305', 'force': True}, timeout=120).json()
        check('强制提交成功', bool(forced.get('ticket_no')))

        r = requests.patch(BASE + '/repair/api/tickets/' + no,
                           json={'status': 'processing', 'note': '已派单'})
        check('流转到处理中', r.json()['ticket']['status'] == 'processing')
        r = requests.patch(BASE + '/repair/api/tickets/' + no, json={'status': 'done'})
        check('流转到已完成', r.json()['ticket']['status'] == 'done'
              and bool(r.json()['ticket']['done_at']))
        r = requests.patch(BASE + '/repair/api/tickets/' + no, json={'category': '设备'})
        check('人工改类别留痕', '人工调整类别' in json.dumps(r.json(), ensure_ascii=False))

        print()
        print('-- SLA / 工单经验沉淀 --')
        det = requests.get(BASE + '/repair/api/tickets/' + no, timeout=10).json()
        check('工单带 SLA 时限', bool(det['ticket'].get('sla_due_at')))
        check('首次响应时间已记录', bool(det['ticket'].get('first_response_at')))
        exp = requests.get(BASE + '/repair/api/tickets/' + no + '/experience',
                           timeout=10).json()
        check('经验草稿自动生成', exp.get('exists') is False
              and '## 现象' in exp.get('content', '')
              and no in exp.get('content', ''))
        check('经验草稿不含联系电话', '138' not in exp.get('content', ''))
        saved = requests.post(BASE + '/repair/api/tickets/' + no + '/experience',
                              json={'content': exp['content'], 'rebuild': True},
                              timeout=180).json()
        check('经验保存并重建索引', saved.get('ok') is True
              and saved.get('rebuilt') is True and saved.get('chunks', 0) > 0, saved)
        exp2 = requests.get(BASE + '/repair/api/tickets/' + no + '/experience',
                            timeout=10).json()
        check('经验可再次读取', exp2.get('exists') is True)
        docs_after = requests.get(BASE + '/api/docs', timeout=10).json()
        check('经验文档进入知识库', docs_after.get('total') == 10, docs_after.get('total'))
        pending_exp = requests.post(
            BASE + '/repair/api/tickets/' + forced['ticket_no'] + '/experience',
            json={'content': 'x'}, timeout=10)
        check('未完成工单拒绝沉淀', pending_exp.status_code == 400)

        print('\n-- 规则兜底 --')
        set_key(config_path, '', 'https://api.example.com')
        check('清除 Key 后健康检查同步', requests.get(
            BASE + '/repair/api/health', timeout=10).json()['configured'] is False)
        rule = requests.post(BASE + '/repair/api/tickets',
                             json={'description': '5号楼201卫生间水管漏水',
                                   'location': '5号楼201'}, timeout=30).json()
        check('无 Key 规则分诊', rule['triage']['ai_mode'] == 'rule')
        check('规则分类为水电', rule['triage']['category'] == '水电')
        check('规则模式 token 为 0', rule['triage']['usage'].get('total_tokens', 0) == 0)
        set_key(config_path, FAKE_KEY)

        print('\n-- 统计 / 相似工单 / 搜索 --')
        st = requests.get(BASE + '/repair/api/stats', timeout=10).json()
        check('统计字段完整', all(k in st for k in
              ('today_new', 'pending', 'avg_hours', 'by_category_7d', 'total_tokens')))
        check('分类分布齐全', set(st['by_category_7d']) ==
              {'网络', '水电', '门锁', '设备', '其他'})
        sim = requests.get(BASE + '/repair/api/similar',
                           params={'q': '投影仪开不了机'}, timeout=10).json()
        check('相似工单有结果', len(sim.get('items') or []) >= 1)
        check('相似工单不含联系方式',
              'contact' not in json.dumps(sim, ensure_ascii=False)
              and 'reporter' not in json.dumps(sim, ensure_ascii=False))
        search = requests.get(BASE + '/api/search', params={'q': '交换机'}, timeout=10).json()
        types = {x.get('type') for x in search.get('results') or []}
        check('全局检索含知识/工单', 'doc' in types and 'ticket' in types, types)
        act = requests.get(BASE + '/api/search', params={'q': '设备'}, timeout=10).json()
        check('全局检索含操作入口', 'action' in {x.get('type') for x in act.get('results') or []})
        check('全局检索无参数返回动作', len(
            requests.get(BASE + '/api/search', timeout=10).json().get('results') or []) >= 6)

        print('\n-- 设备资产 / 二维码 / 关联 --')
        a = requests.post(BASE + '/repair/api/assets',
                          json={'code': 'NET-AP-001', 'name': '3号楼502 无线AP',
                                'category': '网络设备', 'location': '3号楼502',
                                'owner': '王师傅', 'warranty_until': '2027-06-30',
                                'vendor': 'TP-Link', 'aliases': '三楼无线,502AP'}, timeout=10)
        check('新增设备成功', a.status_code == 200 and a.json()['asset']['code'] == 'NET-AP-001')
        check('设备编号唯一', requests.post(
            BASE + '/repair/api/assets',
            json={'code': 'NET-AP-001', 'name': '重复设备'}, timeout=10).status_code == 409)
        assets = requests.get(BASE + '/repair/api/assets', timeout=10).json()
        check('设备列表可查', len(assets.get('assets') or []) == 1)
        one = requests.get(BASE + '/repair/api/assets/by-code/NET-AP-001', timeout=10).json()
        check('按编号查设备', one['asset']['name'] == '3号楼502 无线AP')
        qr = requests.get(BASE + '/repair/api/assets/1/qr.png', timeout=20)
        check('二维码 PNG 可下载', qr.status_code == 200
              and qr.headers.get('Content-Type') == 'image/png' and qr.content[:4] == b'\x89PNG')
        csv_data = ('code,name,category,location,owner,status\n'
                    'NET-AP-002,实验楼305 AP,网络设备,实验楼305,李师傅,在用\n')
        imp = requests.post(BASE + '/repair/api/assets/import',
                            files={'file': ('assets.csv', csv_data.encode('utf-8'))},
                            timeout=10).json()
        check('CSV 导入设备', imp.get('inserted') == 1 and imp.get('updated') == 0, imp)
        check('CSV 重复导入变更新',
              requests.post(BASE + '/repair/api/assets/import',
                            files={'file': ('assets.csv', csv_data.encode('utf-8'))},
                            timeout=10).json().get('updated') == 1)
        patched = requests.patch(BASE + '/repair/api/assets/1',
                                 json={'owner': '李师傅'}, timeout=10).json()
        check('设备编辑成功', patched['asset']['owner'] == '李师傅')
        with_asset = requests.post(BASE + '/repair/api/tickets',
                                   json={'description': '这台AP指示灯不亮',
                                         'asset_code': 'NET-AP-001', 'force': True},
                                   timeout=120).json()
        t2 = with_asset.get('ticket_no')
        check('扫码设备可报修', bool(t2))
        det2 = requests.get(BASE + '/repair/api/tickets/' + t2, timeout=10).json()
        check('工单关联设备', det2.get('asset', {}).get('code') == 'NET-AP-001')
        check('设备类别修正分诊', det2['ticket']['category'] == '网络',
              det2['ticket']['category'])
        public_asset = requests.get(BASE + '/repair/api/assets/by-code/NET-AP-001',
                                    timeout=10).json()
        public_text = json.dumps(public_asset, ensure_ascii=False)
        check('扫码接口不泄露联系方式', 'contact' not in public_text
              and 'reporter' not in public_text)
        check('扫码接口保留工单进度', len(public_asset.get('tickets') or []) >= 1
              and public_asset['tickets'][0].get('ticket_no'))
        check('设备历史工单可查', len(
            requests.get(BASE + '/repair/api/assets/1', timeout=10).json().get('tickets') or []) >= 1)

        print()
        print('-- 硬件遥测 / 告警 --')
        headers = {'X-Ingest-Token': INGEST_TOKEN}
        payload = {'asset_code': 'NET-AP-001', 'msg_id': 'test-msg-001',
                   'ts': '2027-03-01T10:00:00+08:00',
                   'metrics': {'temperature': 45.2, 'current': 0.0, 'door': 1},
                   'units': {'temperature': 'C', 'current': 'A'}}
        tel = requests.post(BASE + '/repair/api/telemetry', json=payload,
                            headers=headers, timeout=10)
        td = tel.json()
        check('遥测上报成功', tel.status_code == 200 and td.get('accepted') == 3, td)
        check('温度规则触发', any(a.get('rule') == 'temp_high' for a in td.get('alerts', [])))
        check('高危告警自动建单', any(a.get('ticket_no') for a in td.get('alerts', [])))
        latest = requests.get(BASE + '/repair/api/assets/by-code/NET-AP-001/telemetry',
                              timeout=10).json()
        check('资产页可读最新遥测', len(latest.get('latest') or []) == 3
              and latest.get('online') is True)
        check('遥测接口不含联系方式',
              'contact' not in json.dumps(latest, ensure_ascii=False))
        dup = requests.post(BASE + '/repair/api/telemetry', json=payload,
                            headers=headers, timeout=10).json()
        check('msg_id 幂等', dup.get('duplicate') is True and dup.get('accepted') == 0)
        bad = requests.post(BASE + '/repair/api/telemetry', json=payload, timeout=10)
        check('无令牌拒绝', bad.status_code == 401)
        alerts = requests.get(BASE + '/repair/api/alerts?status=open', timeout=10).json()
        check('告警列表可查', len(alerts.get('alerts') or []) >= 3)
        ack = requests.patch(BASE + '/repair/api/alerts/%s'
                             % alerts['alerts'][0]['id'],
                             json={'status': 'ack'}, timeout=10)
        check('告警可确认', ack.status_code == 200
              and ack.json().get('alert', {}).get('status') == 'ack')

        print()
        print('-- 资产感知问答 / 运营指标 --')
        qa_asset = requests.post(BASE + '/api/ask',
                                 json={'question': 'NET-AP-001 这台设备最近出过什么问题',
                                       'top_k': 5}, timeout=120).json()
        check('问答命中设备档案', qa_asset.get('hits') and
              qa_asset['hits'][0].get('source') == '设备档案',
              (qa_asset.get('hits') or [{}])[0].get('source'))
        check('设备档案含历史工单', 'NET-AP-001' in (qa_asset['hits'][0].get('text') or '')
              and 'BX' in (qa_asset['hits'][0].get('text') or ''))
        check('返回匹配设备列表', bool(qa_asset.get('assets')))
        alias_ask = requests.post(BASE + '/api/ask',
                                  json={'question': '三楼无线 这台设备最近出过什么问题',
                                        'top_k': 5}, timeout=120).json()
        check('设备别名可命中', alias_ask.get('hits') and
              alias_ask['hits'][0].get('source') == '设备档案')
        met = requests.get(BASE + '/repair/api/metrics', timeout=10).json()
        check('运营指标字段完整', all(k in met for k in
              ('ai_rate', 'sla_rate', 'repeat_rate', 'experience_docs', 'avg_hours')))
        check('经验文档数已统计', met.get('experience_docs') == 1, met.get('experience_docs'))

        print('\n-- 演示模式 / 数据管理 --')
        reset = requests.post(BASE + '/repair/api/data/reset', timeout=30).json()
        check('重置演示数据 15 条', reset.get('inserted') == 15)
        info = requests.get(BASE + '/repair/api/data/info', timeout=10).json()
        check('数据信息含工单/设备/日志', info['tickets'] >= 15 and info['assets'] >= 1
              and info['log_path'].endswith('workbench.log'))
        backup = requests.get(BASE + '/repair/api/data/backup', timeout=20)
        check('数据库备份可下载', backup.status_code == 200 and len(backup.content) > 1000)
        enter = requests.post(BASE + '/repair/api/demo/enter', timeout=30).json()
        check('进入演示模式', enter.get('active') is True
              and enter.get('inserted') == 15
              and 'tickets_demo.db' in enter.get('db_path', ''))
        demo_tickets = requests.get(BASE + '/repair/api/tickets', timeout=10).json()
        check('演示库独立 15 条', len(demo_tickets.get('tickets') or []) == 15)
        leave = requests.post(BASE + '/repair/api/demo/exit', timeout=10).json()
        check('退出演示模式', leave.get('active') is False
              and 'tickets_demo.db' not in leave.get('db_path', ''))
        check('恢复真实库', len(requests.get(
            BASE + '/repair/api/tickets', timeout=10).json().get('tickets') or []) >= 15)

        print('\n-- 边界 / 安全 --')
        check('空描述 400', requests.post(BASE + '/repair/api/tickets',
                                           json={'description': ''}).status_code == 400)
        check('超长描述 400', requests.post(BASE + '/repair/api/tickets',
                                             json={'description': 'x' * 501}).status_code == 400)
        check('不存在工单 404', requests.get(
            BASE + '/repair/api/tickets/BX00000000000').status_code == 404)
        check('报修设置 POST 被拒', requests.post(
            BASE + '/repair/api/settings', json={'api_key': 'x'}).status_code == 409)
        key_info = requests.get(BASE + '/repair/api/settings', timeout=10).json()
        check('报修设置不返回 Key', 'key_masked' not in key_info
              and 'api_key' not in key_info and key_info.get('unified') is True)
        set_key(config_path, '')
        with open(config_path, encoding='utf-8') as f:
            raw = f.read()
        check('测试结束配置无 Key', FAKE_KEY not in raw)
        print('\n结果：%d 通过 / %d 失败' % (passed, failed))
        return 0 if failed == 0 else 1
    finally:
        for p in (proc, mock):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())

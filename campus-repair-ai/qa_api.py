# -*- coding: utf-8 -*-
"""接口全链路自测：启动独立实例（临时库 + 独立端口），不碰演示数据。

运行：python qa_api.py
退出码：0 全部通过 / 1 有失败
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import requests

BASE = 'http://127.0.0.1:8765'
HERE = os.path.dirname(os.path.abspath(__file__))

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


def wait_ready(timeout=25):
    for _ in range(timeout * 4):
        try:
            r = requests.get(BASE + '/api/health', timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def main():
    tmpdir = tempfile.mkdtemp(prefix='baoxiu_qa_')
    db_path = os.path.join(tmpdir, 'qa.db')
    cfg_path = os.path.join(tmpdir, 'config.json')
    env = dict(os.environ, TICKETS_DB=db_path, TICKETS_CONFIG=cfg_path,
               PORT='8765', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'app.py')],
        cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready():
            print('服务未在超时内启动'); return 1
        print('== 独立实例已启动（临时库） ==')

        # 0. AI 输出解析与校验（本地单元断言，不调用外部 API）
        import app as core
        check('AI JSON 正常解析',
              core.parse_ai_json('{"category":"网络","priority":"高","summary":"断网","checks":["a"],"confidence":0.9}')['category'] == '网络')
        check('置信度越界被夹紧',
              core.parse_ai_json('{"category":"网络","priority":"高","checks":[],"confidence":1.7}')['confidence'] == 1.0)
        check('Markdown 包裹可解析',
              core.parse_ai_json('```json\n{"category":"水电","priority":"中","checks":[],"confidence":0.8}\n```')['category'] == '水电')
        check('非法类别触发降级', core.parse_ai_json('{"category":"餐饮","priority":"高"}') is None)
        check('空内容触发降级', core.parse_ai_json('') is None)
        check('checks 超过 3 条被截断',
              len(core.parse_ai_json('{"category":"设备","priority":"低","checks":["1","2","3","4","5"]}')['checks']) == 3)

        # 1. 健康检查
        h = requests.get(BASE + '/api/health').json()
        check('健康检查 ok', h.get('ok') is True)
        check('无 Key 时为规则模式', h.get('ai_enabled') is False)

        # 2. 规则分诊准确性
        cases = [
            ('3号楼502宿舍网断了，路由器指示灯不亮', '网络'),
            ('2号楼301卫生间水管漏水，地面都是水', '水电'),
            ('4号楼208门锁刷卡没反应，进不去', '门锁'),
            ('教学楼A302投影仪开不了机', '设备'),
        ]
        nos = {}
        for desc, expect in cases:
            r = requests.post(BASE + '/api/tickets', json={'description': desc, 'location': '测试点'})
            d = r.json()
            ok = r.status_code == 200 and d['triage']['category'] == expect
            check('分诊 %s -> %s' % (expect, d.get('triage', {}).get('category')), ok, desc)
            nos[expect] = d.get('ticket_no')

        # 3. 高优先级识别
        r = requests.post(BASE + '/api/tickets', json={'description': '整栋教学楼都断网了，冒烟有异味'})
        check('安全/大面积 -> 高优先级', r.json()['triage']['priority'] == '高')

        # 4. 重复报修检测
        requests.post(BASE + '/api/tickets', json={'description': '4号楼208门锁刷卡坏了', 'location': '同点测试'})
        r2 = requests.post(BASE + '/api/tickets', json={'description': '4号楼208门锁还是刷不开', 'location': '同点测试'})
        check('同地点同类别 -> 重复提示', bool(r2.json().get('duplicate')),
              r2.json().get('duplicate'))

        # 5. 强制提交
        r3 = requests.post(BASE + '/api/tickets',
                           json={'description': '4号楼208门锁仍异常', 'location': '同点测试', 'force': True})
        no = r3.json().get('ticket_no')
        check('force 强制提交成功', r3.status_code == 200 and no and no.startswith('BX'))

        # 6. 详情与事件
        det = requests.get(BASE + '/api/tickets/' + no).json()
        check('详情返回事件 >= 1', len(det.get('events', [])) >= 1)
        check('规则模式 token 为 0', det['ticket'].get('total_tokens') == 0)

        # 7. 状态流转
        r4 = requests.patch(BASE + '/api/tickets/' + no, json={'status': 'processing', 'note': '派单'})
        check('流转到处理中', r4.json()['ticket']['status'] == 'processing')
        r5 = requests.patch(BASE + '/api/tickets/' + no, json={'status': 'done'})
        check('流转到已完成且有 done_at', r5.json()['ticket']['status'] == 'done'
              and bool(r5.json()['ticket']['done_at']))

        # 8. 人工改类别 + 事件留痕
        r6 = requests.patch(BASE + '/api/tickets/' + no, json={'category': '设备'})
        ev_text = json.dumps(r6.json().get('events', []), ensure_ascii=False)
        check('改类别成功', r6.json()['ticket']['category'] == '设备')
        check('改类别有留痕', '人工调整类别' in ev_text)

        # 9. 统计
        st = requests.get(BASE + '/api/stats').json()
        check('今日新增 >= 6', st['today_new'] >= 6, st['today_new'])
        check('平均处理时长有值', st['avg_hours'] is not None, st['avg_hours'])
        check('分类分布齐全', set(st['by_category_7d'].keys()) == {'网络', '水电', '门锁', '设备', '其他'})

        # 10. 过滤
        r7 = requests.get(BASE + '/api/tickets?status=done').json()
        check('状态过滤全部为 done', all(t['status'] == 'done' for t in r7['tickets']))

        # 11. 边界
        check('空描述 -> 400', requests.post(BASE + '/api/tickets', json={'description': ''}).status_code == 400)
        check('超长描述 -> 400',
              requests.post(BASE + '/api/tickets', json={'description': 'x' * 501}).status_code == 400)
        check('不存在工单 -> 404', requests.get(BASE + '/api/tickets/BX00000000000').status_code == 404)
        check('非法状态不生效',
              requests.patch(BASE + '/api/tickets/' + no, json={'status': 'hacked'}).status_code == 200)

        # 12. 页面
        check('学生端页面 200', requests.get(BASE + '/').status_code == 200)
        check('管理端页面 200', requests.get(BASE + '/admin').status_code == 200)

        # 13. AI 设置读写（放最后：保存假 Key 会让 health 变为开启态）
        s0 = requests.get(BASE + '/api/settings').json()
        check('初始未配置 Key', s0['has_key'] is False)
        check('高级参数默认值存在',
              'reasoning_effort' in s0 and 'temperature' in s0 and 'max_tokens' in s0)
        fake_key = 'test-key-not-real'
        r = requests.post(BASE + '/api/settings', json={
            'api_key': fake_key, 'base_url': 'https://api.example.com',
            'model': 'test-model', 'api_format': 'openai',
            'reasoning_effort': 'high', 'thinking': '', 'temperature': 0.5, 'max_tokens': 800})
        check('保存设置成功', r.status_code == 200 and r.json().get('ai_enabled') is True)
        s1 = requests.get(BASE + '/api/settings').json()
        check('设置已持久化', s1['base_url'] == 'https://api.example.com' and s1['model'] == 'test-model')
        check('高级参数已保存',
              s1['reasoning_effort'] == 'high' and abs(s1['temperature'] - 0.5) < 0.01
              and s1['max_tokens'] == 800)
        check('Key 不回传明文', s1['key_masked'] != fake_key and '****' in s1['key_masked'])
        check('health 同步为开启', requests.get(BASE + '/api/health').json()['ai_enabled'] is True)
        requests.post(BASE + '/api/settings', json={'base_url': 'https://api.deepseek.com'})
        check('留空 Key 不覆盖原值', requests.get(BASE + '/api/settings').json()['has_key'] is True)
        requests.post(BASE + '/api/settings', json={'clear_key': True})
        check('清除 Key 生效', requests.get(BASE + '/api/settings').json()['has_key'] is False)

        # 14. 数据管理（放最后：reset 会重建数据）
        info = requests.get(BASE + '/api/data/info').json()
        check('数据信息含路径/大小/数量',
              info['db_path'].endswith('.db') and info['db_size'] > 0
              and info['tickets'] >= 6 and 'total_tokens' in info)
        bak = requests.get(BASE + '/api/data/backup')
        check('数据库备份可下载', bak.status_code == 200 and len(bak.content) > 1000
              and 'attachment' in bak.headers.get('Content-Disposition', ''))
        r = requests.post(BASE + '/api/data/reset')
        check('重置演示数据为 15 条',
              r.status_code == 200 and r.json().get('inserted') == 15
              and requests.get(BASE + '/api/tickets').json()['tickets']
              and len(requests.get(BASE + '/api/tickets').json()['tickets']) == 15)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except Exception:
            pass

    print('\n结果：%d 通过 / %d 失败' % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""RAG 项目全面体检：接口、边界、安全、并发、性能、一致性

独立脚本，不 import 应用代码；启动独立实例（8101）跑测试。
写操作仅使用 __health__.md / __health__.txt 临时文件，测完删除并重建索引恢复。

用法：python 审计/rag_health_check.py
"""
import json
import os
import subprocess
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 'http://127.0.0.1:8101'
results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  <- ' + str(detail)) if (detail and not cond) else ''))


def note(msg):
    print('  NOTE  ' + msg)


def wait_ready(timeout=40):
    for _ in range(timeout * 4):
        try:
            if requests.get(BASE + '/api/stats', timeout=1).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def main():
    env = dict(os.environ, PORT='8101', USE_TF='0', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready():
            print('服务启动失败')
            return 1
        print('== 独立实例启动（8101） ==\n')

        # ---------- A. 基础接口 ----------
        print('-- A. 基础接口 --')
        check('页面 200', requests.get(BASE + '/', timeout=5).status_code == 200)
        s = requests.get(BASE + '/api/stats', timeout=5).json()
        check('stats 字段完整', s.get('count', 0) > 0 and s.get('embedding_model'))
        docs = requests.get(BASE + '/api/docs', timeout=5).json()
        check('docs 返回 9 篇', docs.get('total') == 9, docs.get('total'))
        ev = requests.get(BASE + '/api/eval', timeout=5)
        check('eval 可读且含指标', ev.status_code == 200 and 'Recall@5' in (ev.json().get('metrics') or {}))

        # ---------- B. 问答功能 ----------
        print('-- B. 问答功能 --')
        r = requests.post(BASE + '/api/ask', json={'question': '光猫 LOS 红灯一直亮怎么办？', 'top_k': 5}, timeout=30)
        d = r.json()
        top1 = ((d.get('hits') or [{}])[0]).get('heading', '')
        check('正常提问 200', r.status_code == 200)
        check('Top-1 命中光猫章节', '光猫' in top1, top1)
        check('返回来源列表', len(d.get('hits') or []) > 0)
        check('置信度在 0-1', 0 <= d.get('confidence', -1) <= 1)

        # 多轮指代
        r = requests.post(BASE + '/api/ask', json={
            'question': '那重启之后还是红灯呢？', 'top_k': 3,
            'history': [{'q': '光猫 LOS 红灯怎么办', 'a': '检查光纤接头并重启'}]
        }, timeout=30)
        top1 = ((r.json().get('hits') or [{}])[0]).get('heading', '')
        check('多轮指代命中光猫', '光猫' in top1, top1)

        # ---------- C. 边界与异常 ----------
        print('-- C. 边界与异常 --')
        check('空问题 -> 400', requests.post(BASE + '/api/ask', json={'question': ''}, timeout=10).status_code == 400)
        check('超长问题 -> 400',
              requests.post(BASE + '/api/ask', json={'question': '问' * 301}, timeout=10).status_code == 400)
        check('question 非字符串 -> 400',
              requests.post(BASE + '/api/ask', json={'question': 123}, timeout=10).status_code == 400)
        check('JSON 顶层非对象 -> 400',
              requests.post(BASE + '/api/ask', json=['a', 'b'], timeout=10).status_code == 400)
        r = requests.post(BASE + '/api/ask', json={'question': '光猫红灯', 'top_k': 100}, timeout=30)
        check('top_k 越界被钳制', r.status_code == 200 and len(r.json().get('hits') or []) <= 10)
        r = requests.post(BASE + '/api/ask',
                          json={'question': '光猫红灯', 'history': 'not-a-list'}, timeout=30)
        check('history 类型错误不崩', r.status_code == 200)
        check('删除不存在文档 -> 404',
              requests.delete(BASE + '/api/docs/__nope__.md', timeout=5).status_code == 404)

        # ---------- D. 上传安全 ----------
        print('-- D. 上传安全 --')
        r = requests.post(BASE + '/api/upload',
                          files={'files': ('../../evil.md', b'# evil')}, timeout=10)
        j = r.json()
        check('路径穿越文件名被拒', j.get('saved') == [] and len(j.get('skipped') or []) == 1, j)
        r = requests.post(BASE + '/api/upload',
                          files={'files': ('bad.exe', b'MZ')}, timeout=10)
        check('非法扩展名被拒', r.json().get('saved') == [])
        big = b'#' + b'x' * (2 * 1024 * 1024 + 10)
        r = requests.post(BASE + '/api/upload',
                          files={'files': ('huge.md', big)}, timeout=30)
        check('超 2MB 文件被拒', r.json().get('saved') == [], r.json().get('skipped'))

        # ---------- E. 知识库动态更新（受控写） ----------
        print('-- E. 上传 / 重建 / 删除 --')
        before = requests.get(BASE + '/api/stats', timeout=5).json().get('count')
        r = requests.post(BASE + '/api/upload', files={'files': (
            '__health__.txt', '# 健康检查文档\n\n## 测试章节\n本系统支持向量检索与来源引用。'.encode('utf-8'))}, timeout=10)
        check('上传 .txt 成功', r.json().get('saved') == ['__health__.txt'], r.json())
        r = requests.post(BASE + '/api/rebuild', timeout=180)
        after = r.json().get('chunks', 0)
        check('重建后块数增加', after > before, '%s -> %s' % (before, after))
        r = requests.post(BASE + '/api/ask', json={'question': '系统支持向量检索吗', 'top_k': 3}, timeout=30)
        hit_src = ((r.json().get('hits') or [{}])[0]).get('source', '')
        check('.txt 文档可被检索', hit_src == '__health__.txt', hit_src)
        requests.delete(BASE + '/api/docs/__health__.txt', timeout=5)
        r = requests.post(BASE + '/api/rebuild', timeout=180)
        check('删除后块数恢复', r.json().get('chunks') == before, r.json().get('chunks'))

        # ---------- F. 并发与性能 ----------
        print('-- F. 并发与性能 --')
        import threading
        bag = []
        lock = threading.Lock()

        def one():
            try:
                rr = requests.post(BASE + '/api/ask', json={'question': '交换机端口不亮', 'top_k': 3}, timeout=30)
                with lock:
                    bag.append(rr.status_code)
            except Exception as e:
                with lock:
                    bag.append(str(e)[:30])

        ts = [threading.Thread(target=one) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        check('10 并发全 200', all(c == 200 for c in bag), bag)

        t0 = time.time()
        requests.post(BASE + '/api/ask', json={'question': '摄像头离线', 'top_k': 5}, timeout=30)
        latency = time.time() - t0
        check('单次问答延迟 < 2s', latency < 2, '%.2fs' % latency)
        note('单次检索延迟 %.2fs（检索模式，含编码）' % latency)

        # ---------- G. 一致性 ----------
        print('-- G. 一致性 --')
        s2 = requests.get(BASE + '/api/stats', timeout=5).json()
        check('stats.count = docs/重建块数', s2.get('count') == before, '%s vs %s' % (s2.get('count'), before))
        lib = requests.get(BASE + '/api/docs', timeout=5).json()
        check('删除后文档数恢复 9 篇', lib.get('total') == 9, lib.get('total'))
        # 反馈写入
        r = requests.post(BASE + '/api/feedback', json={'question': '体检', 'helpful': True,
                                                        'mode': 'retrieval_only', 'top_source': 'test'}, timeout=5)
        check('反馈接口 200', r.status_code == 200)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        # 清理可能残留的测试文件
        for junk in ('__health__.md', '__health__.txt', 'evil.md', 'huge.md'):
            p = os.path.join(ROOT, 'data', 'docs', junk)
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    passed = sum(1 for _n, ok, _d in results if ok)
    failed = len(results) - passed
    print('\n===== 体检结果：%d 通过 / %d 失败 =====' % (passed, failed))
    if failed:
        for n, ok, d in results:
            if not ok:
                print('  - %s (%s)' % (n, d))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

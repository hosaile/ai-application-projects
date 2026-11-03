# -*- coding: utf-8 -*-
"""管理员口令（可选）回归：未登录拦截、登录、读写、退出。

运行：python tests/test_admin_auth.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8103
BASE = 'http://127.0.0.1:%d' % PORT
PASSWORD = 'test-' + os.urandom(6).hex()
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


def wait_ready(timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get(BASE + '/api/health', timeout=1).status_code == 200:
                return True
        except Exception:
            time.sleep(0.35)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix='workbench_auth_')
    data = os.path.join(tmp, 'data')
    os.makedirs(data)
    for sub in ('docs', 'chroma', 'tests'):
        src = os.path.join(ROOT, 'data', sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(data, sub))
    shutil.copyfile(os.path.join(ROOT, 'config.example.json'),
                    os.path.join(tmp, 'config.json'))
    env = dict(os.environ, PORT=str(PORT), WORKBENCH_DATA_DIR=data,
               WORKBENCH_CONFIG=os.path.join(tmp, 'config.json'),
               WORKBENCH_ADMIN_PASSWORD=PASSWORD, PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        check('服务启动', wait_ready())
        if failed:
            return 1
        r = requests.get(BASE + '/repair/admin', allow_redirects=False)
        check('未登录访问管理端跳转登录', r.status_code == 302
              and '/repair/login' in (r.headers.get('Location') or ''))
        r = requests.get(BASE + '/repair/api/data/info')
        check('未登录写接口 401', r.status_code == 401 and r.json().get('login'))
        r = requests.get(BASE + '/repair/api/assets')
        check('设备 GET 仍可扫码访问', r.status_code == 200)

        s = requests.Session()
        r = s.post(BASE + '/repair/login', data={'password': 'wrong'})
        check('错误口令提示', r.status_code == 200 and '口令不正确' in r.text)
        r = s.post(BASE + '/repair/login', data={'password': PASSWORD},
                   allow_redirects=False)
        check('正确口令登录', r.status_code == 302
              and (r.headers.get('Location') or '').endswith('/repair/admin'))
        check('登录后管理端 200', s.get(BASE + '/repair/admin').status_code == 200)
        check('登录后数据接口 200', s.get(BASE + '/repair/api/data/info').status_code == 200)
        check('登录后可以写工单', s.post(BASE + '/repair/api/tickets',
                                       json={'description': '登录后的测试报修',
                                             'location': '认证测试点'}).status_code == 200)
        s.get(BASE + '/repair/logout')
        check('退出后重新拦截', s.get(BASE + '/repair/api/data/info').status_code == 401)
        print('\n结果：%d 通过 / %d 失败' % (passed, failed))
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())

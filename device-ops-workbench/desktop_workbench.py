# -*- coding: utf-8 -*-
"""设备运维智能工作台 · 桌面启动器（PyInstaller 入口）

双击 exe：启动本地服务 → 打开应用窗口。
数据（文档/索引/工单）放 exe 同级 data/，配置放 exe 同级 config.json。
"""
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    return os.path.join(getattr(sys, '_MEIPASS', app_dir()), name)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def setup_logging(d):
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        handlers=[logging.FileHandler(os.path.join(d, 'desktop.log'), encoding='utf-8')],
        force=True)


def log(msg):
    logging.getLogger('desktop').info(msg)


def wait_ready(url, timeout=180):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(timeout * 4):
        try:
            opener.open(url, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.25)
    return False


def find_edge():
    cands = [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%LocalAppData%\Microsoft\Edge\Application\msedge.exe'),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def main():
    d = app_dir()
    setup_logging(d)
    log('=== workbench start ===')
    data = os.path.join(d, 'data')
    os.makedirs(data, exist_ok=True)
    for sub in ('docs', 'chroma', 'tests'):
        src = resource_path(os.path.join('data', sub))
        dst = os.path.join(data, sub)
        if not os.path.exists(dst) and os.path.isdir(src):
            try:
                shutil.copytree(src, dst)
                log('seeded data/%s' % sub)
            except Exception:
                log('seed %s failed:\n%s' % (sub, traceback.format_exc()))
    os.environ['WORKBENCH_DATA_DIR'] = data
    os.environ.setdefault('USE_TF', '0')

    cfg_path = os.path.join(d, 'config.json')
    if not os.path.exists(cfg_path):
        try:
            shutil.copyfile(resource_path('config.example.json'), cfg_path)
            log('seeded config.json')
        except Exception:
            log('seed config failed:\n' + traceback.format_exc())
    os.environ['WORKBENCH_CONFIG'] = cfg_path

    import app as core
    port = free_port()

    def run_server():
        try:
            core.app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
        except Exception:
            log('server crashed:\n' + traceback.format_exc())

    threading.Thread(target=run_server, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    if not wait_ready(base + '/api/health'):
        log('server not ready')
        return
    log('port %d ready' % port)

    edge = find_edge()
    if edge:
        prof = os.path.join(os.environ.get('LOCALAPPDATA', d), 'DeviceOpsWorkbenchEdge')
        try:
            os.makedirs(prof, exist_ok=True)
        except Exception:
            prof = os.path.join(d, 'edge_profile')
            os.makedirs(prof, exist_ok=True)
        try:
            subprocess.Popen([edge, '--app=' + base + '/', '--user-data-dir=' + prof,
                              '--no-first-run', '--no-default-browser-check', '--disable-sync',
                              '--no-proxy-server', '--window-size=1500,1000'], close_fds=True)
            log('edge opened')
        except Exception:
            log('edge failed:\n' + traceback.format_exc())
            import webbrowser
            webbrowser.open(base + '/')
    else:
        import webbrowser
        webbrowser.open(base + '/')

    while True:
        time.sleep(3600)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        log('uncaught:\n' + traceback.format_exc())
        raise

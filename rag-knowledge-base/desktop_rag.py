# -*- coding: utf-8 -*-
"""RAG 知识库问答系统 · 桌面启动器（打包入口）

双击 exe：启动本地服务 → 打开应用窗口。
数据目录（文档/索引）放在 exe 同级 data/，首次运行自动从内置资源复制。
"""
import ctypes
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
    """windowed 模式没有控制台，必须把日志（含 werkzeug）写文件，否则服务线程会崩。"""
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        handlers=[logging.FileHandler(os.path.join(d, 'desktop.log'), encoding='utf-8')],
        force=True)


def log(msg):
    logging.getLogger('desktop').info(msg)


def wait_ready(url, timeout=120):
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
    log('=== start ===')
    data = os.path.join(d, 'data')
    os.makedirs(data, exist_ok=True)
    # 首次运行：复制内置文档与预建索引（之后用户可增删，不再覆盖）
    for sub in ('docs', 'chroma'):
        src = resource_path(os.path.join('data', sub))
        dst = os.path.join(data, sub)
        if not os.path.exists(dst) and os.path.isdir(src):
            try:
                shutil.copytree(src, dst)
                log('seeded data/%s' % sub)
            except Exception:
                log('seed %s failed:\n%s' % (sub, traceback.format_exc()))
    os.environ['RAG_DATA_DIR'] = data
    os.environ.setdefault('USE_TF', '0')
    # 配置文件放 exe 同级（首次从内置模板生成，用户可自行填 API Key）
    cfg_path = os.path.join(d, 'config.json')
    if not os.path.exists(cfg_path):
        try:
            shutil.copyfile(resource_path('config.example.json'), cfg_path)
            log('seeded config.json')
        except Exception:
            log('seed config failed:\n' + traceback.format_exc())
    os.environ['RAG_CONFIG'] = cfg_path

    import app as core
    core.app.static_folder = resource_path('static')
    port = free_port()

    def run_server():
        try:
            core.app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
        except Exception:
            log('server crashed:\n' + traceback.format_exc())

    threading.Thread(target=run_server, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    if not wait_ready(base + '/api/stats'):
        log('server not ready')
        return
    log('port %d ready' % port)

    edge = find_edge()
    if edge:
        prof = os.path.join(os.environ.get('LOCALAPPDATA', d), 'RagKbEdge')
        try:
            os.makedirs(prof, exist_ok=True)
        except Exception:
            prof = os.path.join(d, 'edge_profile')
            os.makedirs(prof, exist_ok=True)
        try:
            subprocess.Popen([edge, '--app=' + base + '/', '--user-data-dir=' + prof,
                              '--no-first-run', '--no-default-browser-check', '--disable-sync',
                              '--no-proxy-server', '--window-size=1400,1000'], close_fds=True)
            log('edge opened')
        except Exception:
            log('edge failed:\n' + traceback.format_exc())
            import webbrowser
            webbrowser.open(base + '/')
    else:
        import webbrowser
        webbrowser.open(base + '/')

    # 常驻（关闭窗口后进程保留；如需退出可结束进程或重启系统）
    while True:
        time.sleep(3600)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        log('uncaught:\n' + traceback.format_exc())
        raise

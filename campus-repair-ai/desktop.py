# -*- coding: utf-8 -*-
"""校园报修 · 桌面版启动器

双击运行：启动本地服务，打开学生端与工单管理台两个应用窗口。
打包：python -m PyInstaller --noconfirm --onefile --windowed --name 校园报修系统 ^
        --icon app.ico --add-data "static;static" --add-data "kb_snippet.json;." ^
        --add-data "config.example.json;." desktop.py
"""
import ctypes
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request


def app_dir():
    """exe（或脚本）所在目录：数据库、日志、配置都放这里（相对路径，随文件夹移动）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def find_edge():
    cands = [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%LocalAppData%\Microsoft\Edge\Application\msedge.exe'),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe') as k:
            p = winreg.QueryValue(k, None)
            if p and os.path.exists(p):
                return p
    except Exception:
        pass
    return None


def screen_scale():
    """系统 DPI 缩放（125% -> 1.25）。"""
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def setup_logging(d):
    """统一日志：写入 exe 同目录 desktop.log，超过 2MB 自动轮转（保留 2 份）。

    Flask/werkzeug 的请求与错误日志也会一并写入，便于排查。
    """
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(
        os.path.join(d, 'desktop.log'),
        maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        handlers=[handler], force=True)


def log(msg):
    logging.getLogger('desktop').info(msg)


def backup_db(d):
    """启动时把上次的数据库快照为 tickets.db.bak（覆盖上一份）。"""
    db = os.path.join(d, 'tickets.db')
    try:
        if os.path.exists(db) and os.path.getsize(db) > 0:
            import shutil
            shutil.copyfile(db, os.path.join(d, 'tickets.db.bak'))
            log('db backup ok')
    except Exception:
        log('db backup failed:\n' + traceback.format_exc())


def wait_ready(url, timeout=20):
    """本地回环健康检查：显式禁用系统代理（坏代理会劫持 127.0.0.1 请求）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(timeout * 5):
        try:
            opener.open(url, timeout=0.5).read()
            return True
        except Exception:
            time.sleep(0.2)
    return False


def window_exists(keyword='校园报修'):
    """系统中是否还有标题包含 keyword 的可见窗口。

    返回 True / False；检测过程异常返回 None（此时不退出，避免误杀）。
    """
    try:
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _lparam):
            try:
                if user32.IsWindowVisible(hwnd):
                    n = user32.GetWindowTextLengthW(hwnd)
                    if n > 0:
                        buf = ctypes.create_unicode_buffer(n + 1)
                        user32.GetWindowTextW(hwnd, buf, n + 1)
                        if keyword in buf.value:
                            found.append(hwnd)
            except Exception:
                pass
            return True

        user32.EnumWindows(callback, 0)
        return len(found) > 0
    except Exception:
        return None


def main():
    d = app_dir()
    setup_logging(d)
    log('=== start (frozen=%s) ===' % bool(getattr(sys, 'frozen', False)))
    os.environ.setdefault('TICKETS_DB', os.path.join(d, 'tickets.db'))
    os.environ.setdefault('TICKETS_CONFIG', os.path.join(d, 'config.json'))
    backup_db(d)

    import app as core
    core.app.static_folder = resource_path('static')
    core.init_db()
    try:
        import seed
        seed.seed()  # 空库时自动写入演示数据
    except Exception:
        log('seed failed:\n' + traceback.format_exc())

    port = free_port()
    threading.Thread(
        target=lambda: core.app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False),
        daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    if not wait_ready(base + '/api/health'):
        log('server not ready')
        return

    # 窗口关闭后退出：检测系统里是否还有“校园报修”窗口（不受页面定时器节流影响）
    def watchdog():
        time.sleep(60)  # 启动宽限：等窗口加载完成
        gone = 0
        while True:
            time.sleep(10)
            exists = window_exists()
            if exists is None or exists:
                gone = 0
                continue
            gone += 1
            if gone >= 3:  # 连续 30 秒找不到窗口才退出
                log('windows closed, exit')
                os._exit(0)
    threading.Thread(target=watchdog, daemon=True).start()

    # 按 DPI 缩放换算窗口尺寸，保证不同缩放下布局正确
    scale = screen_scale()
    try:
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        sw, sh = 1920, 1080
    sw_css, sh_css = sw / scale, sh / scale
    margin, gap = 12, 10
    w1_css = min(580, max(420, sw_css * 0.36))
    w2_css = min(1120, max(540, sw_css - w1_css - margin * 2 - gap))
    h_css = min(900, max(560, sh_css - 70))
    w1, w2, h = int(w1_css * scale), int(w2_css * scale), int(h_css * scale)
    x1 = int(margin * scale)
    x2 = x1 + w1 + int(gap * scale)
    y = int(margin * scale)

    edge = find_edge()
    if not edge:
        import webbrowser
        log('Edge not found, fallback to default browser')
        webbrowser.open(base + '/')
        while True:
            time.sleep(3600)

    log('Edge: ' + edge)
    log('port: %d  scale: %.2f  windows: %dx%d, %dx%d' % (port, scale, w1, h, w2, h))

    # 两个独立配置实例：窗口尺寸/位置各自生效，且本地请求不走系统代理
    local = os.environ.get('LOCALAPPDATA', d)
    common = ['--no-first-run', '--no-default-browser-check', '--disable-sync', '--no-proxy-server']
    try:
        for name, url, prof, size, pos in [
            ('student', base + '/', os.path.join(local, 'CampusRepairEdge', 'student'),
             '%d,%d' % (w1, h), '%d,%d' % (x1, y)),
            ('admin', base + '/admin', os.path.join(local, 'CampusRepairEdge', 'admin'),
             '%d,%d' % (w2, h), '%d,%d' % (x2, y)),
        ]:
            try:
                os.makedirs(prof, exist_ok=True)
            except Exception:
                prof = os.path.join(d, 'edge_profile_' + name)
                os.makedirs(prof, exist_ok=True)
            subprocess.Popen(
                [edge, '--app=' + url, '--user-data-dir=' + prof] + common +
                ['--window-size=' + size, '--window-position=' + pos], close_fds=True)
            time.sleep(1.2)
    except Exception:
        log('open window failed:\n' + traceback.format_exc())
        return

    while True:
        time.sleep(3600)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        log('uncaught error:\n' + traceback.format_exc())
        raise

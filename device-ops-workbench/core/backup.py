# -*- coding: utf-8 -*-
"""SQLite 自动备份：在线备份 API，保留最近 N 份。"""
import os
import sqlite3
from datetime import datetime

from core import paths


def backup_db(db_path=None, keep=7):
    db_path = db_path or paths.ticket_db()
    if not os.path.exists(db_path):
        return None
    folder = os.path.join(paths.data_root(), 'backups')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, 'tickets_%s.db' % datetime.now().strftime('%Y%m%d_%H%M%S'))
    tmp = target + '.tmp'
    src = sqlite3.connect(db_path, timeout=15)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, target)
    files = sorted(
        (os.path.join(folder, n) for n in os.listdir(folder)
         if n.startswith('tickets_') and n.endswith('.db')),
        key=os.path.getmtime, reverse=True)
    for old in files[max(1, int(keep)):]:
        try:
            os.remove(old)
        except Exception:
            pass
    return target

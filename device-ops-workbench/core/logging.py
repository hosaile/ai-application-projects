# -*- coding: utf-8 -*-
"""日志：控制台 + data/workbench.log（桌面版 windowed 模式无控制台时仍有记录）。"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from core import paths


def setup(level=logging.INFO):
    paths.ensure_dirs()
    logger = logging.getLogger('workbench')
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')

    try:
        fh = RotatingFileHandler(paths.log_path(), maxBytes=5 * 1024 * 1024,
                                 backupCount=3, encoding='utf-8')
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger

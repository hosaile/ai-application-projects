# -*- coding: utf-8 -*-
"""统一路径：源码运行与桌面版（PyInstaller）共用一套数据目录规则。

- 源码：数据在项目根 `data/`，配置为根 `config.json`
- 桌面版：数据在 exe 同级 `data/`，配置为 exe 同级 `config.json`
- 环境变量覆盖：WORKBENCH_DATA_DIR / WORKBENCH_CONFIG / WORKBENCH_MODELS_DIR
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = bool(getattr(sys, 'frozen', False))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT
BUNDLE_DIR = getattr(sys, '_MEIPASS', ROOT)


def data_root():
    return os.environ.get('WORKBENCH_DATA_DIR') or os.path.join(APP_DIR, 'data')


def docs_dir():
    return os.path.join(data_root(), 'docs')


def chroma_dir():
    return os.path.join(data_root(), 'chroma')


def tests_dir():
    return os.path.join(data_root(), 'tests')


def demo_dir():
    return os.path.join(data_root(), 'demo')


def ticket_db():
    return os.environ.get('WORKBENCH_TICKETS_DB') or os.path.join(data_root(), 'tickets.db')


def feedback_path():
    return os.path.join(data_root(), 'feedback.jsonl')


def config_path():
    return os.environ.get('WORKBENCH_CONFIG') or os.path.join(APP_DIR, 'config.json')


def models_dir():
    """本地向量模型目录（打包进 bundle；源码时可放项目根）。"""
    env = os.environ.get('WORKBENCH_MODELS_DIR')
    if env:
        return env
    candidates = [os.path.join(APP_DIR, 'models'), os.path.join(BUNDLE_DIR, 'models')]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]


def log_path():
    return os.path.join(data_root(), 'workbench.log')


def ensure_dirs():
    for path in (data_root(), docs_dir(), chroma_dir(), tests_dir(), demo_dir()):
        os.makedirs(path, exist_ok=True)

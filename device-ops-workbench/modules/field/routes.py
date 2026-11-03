# -*- coding: utf-8 -*-
"""现场排障模块：包装页 + 原单文件助手（离线版保持不变）。"""
import os

from flask import Blueprint, Response, send_from_directory

bp = Blueprint('field', __name__)
FIELD_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT = '网络排障助手.html'

WRAPPER = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>现场排障 · 设备运维工作台</title>
<style>
  :root { --ink:#1b1e23; --paper:#f2f1ec; --line:#d9d6cc; --accent:#c2410c; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; background:var(--paper); color:var(--ink);
    font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  .bar { height:48px; display:flex; align-items:center; gap:14px; padding:0 18px;
    background:#fff; border-bottom:1px solid var(--line); }
  .bar b { font-size:14px; letter-spacing:.02em; }
  .bar .sep { color:#b7b2a6; }
  .bar a { color:var(--accent); text-decoration:none; font-weight:600; }
  .bar a:hover { text-decoration:underline; }
  .bar .spacer { flex:1; }
  .bar .hint { color:#7a7568; font-size:12px; }
  iframe { display:block; width:100%; height:calc(100% - 48px); border:0; background:#fff; }
  @media (max-width:640px) { .bar .hint { display:none; } .bar { padding:0 12px; } }
</style>
</head>
<body>
  <div class="bar">
    <b>现场排障</b><span class="sep">/</span>
    <a href="/">← 返回工作台</a>
    <span class="spacer"></span>
    <span class="hint">离线单文件仍可单独打开使用</span>
    <a href="/field/assistant.html" target="_blank" rel="noopener">新窗口打开</a>
  </div>
  <iframe src="/field/assistant.html" title="现场排障助手"></iframe>
</body>
</html>'''


@bp.route('/')
def index():
    return Response(WRAPPER, mimetype='text/html; charset=utf-8')


@bp.route('/assistant.html')
def assistant():
    return send_from_directory(FIELD_DIR, ASSISTANT)

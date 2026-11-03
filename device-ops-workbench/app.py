# -*- coding: utf-8 -*-
"""设备运维智能工作台 · 统一 Flask 入口

一个进程、一个端口：
  /              知识问答（RAG 工作台）
  /repair        报修学生端
  /repair/admin  报修管理端
  /field         现场排障（包装页 + 离线单文件）
  /start         角色首页

启动：python app.py  →  http://127.0.0.1:8100/
"""
import os
import secrets
import sys
import threading
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core import config, logging as wlog, paths  # noqa: E402
from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from modules.field.routes import bp as field_bp  # noqa: E402
from modules.qa.routes import bp as qa_bp  # noqa: E402
from modules.repair import db as repair_db  # noqa: E402
from modules.repair.routes import bp as repair_bp  # noqa: E402

wlog.setup()
paths.ensure_dirs()
repair_db.init_db()


def _prewarm_embedding():
    """后台预热向量模型，避免第一次报修分诊/问答等待几十秒。"""
    try:
        from modules.qa.rag import embed
        embed.embed_one('预热')
    except Exception:
        pass


threading.Thread(target=_prewarm_embedding, daemon=True, name='rag-prewarm').start()

app = Flask(__name__, static_folder=None)
app.json.ensure_ascii = False
_app_cfg = config.load_cfg().get('app') or {}
app.secret_key = (os.environ.get('WORKBENCH_SECRET')
                  or _app_cfg.get('secret_key')
                  or secrets.token_hex(16))

app.register_blueprint(qa_bp)
app.register_blueprint(repair_bp, url_prefix='/repair')
app.register_blueprint(field_bp, url_prefix='/field')

ACTIONS = [
    {'type': 'action', 'title': '新建报修工单', 'sub': '学生端', 'url': '/repair/', 'keywords': '报修 新建 故障 提交'},
    {'type': 'action', 'title': '工单管理', 'sub': '管理端', 'url': '/repair/admin', 'keywords': '工单 管理 派单 统计'},
    {'type': 'action', 'title': '设备资产台账', 'sub': '管理端', 'url': '/repair/admin#assets', 'keywords': '设备 资产 二维码 台账'},
    {'type': 'action', 'title': '现场排障助手', 'sub': '离线工具', 'url': '/field/', 'keywords': '排障 现场 网络 弱电'},
    {'type': 'action', 'title': '知识库文档', 'sub': '知识问答', 'url': '/#kb', 'keywords': '知识库 文档 上传 重建'},
    {'type': 'action', 'title': '检索评估', 'sub': '知识问答', 'url': '/#eval', 'keywords': '评估 recall mrr 指标'},
    {'type': 'action', 'title': '模型设置', 'sub': '统一配置', 'url': '/#models', 'keywords': '模型 供应商 key 设置'},
    {'type': 'action', 'title': '角色首页', 'sub': '工作台', 'url': '/start', 'keywords': '首页 角色 导航'},
]


@app.route('/api/health')
def health():
    cfg = config.load_cfg()
    info = config.active_info(cfg)
    return jsonify({
        'ok': True,
        'app': '设备运维智能工作台',
        'qa': True,
        'repair': True,
        'field': True,
        'model': info.get('model') or '',
        'model_name': info.get('model_name') or '',
        'configured': bool(info.get('configured')),
    })


@app.route('/api/search')
def global_search():
    """Ctrl+K 全局搜索：知识文档 / 工单 / 设备 / 动作。"""
    q = (request.args.get('q') or '').strip()
    limit = 5
    results = []
    if not q:
        return jsonify({'q': q, 'results': ACTIONS[:6]})
    low = q.lower()

    # 动作
    for item in ACTIONS:
        hay = (item['title'] + ' ' + item['sub'] + ' ' + item['keywords']).lower()
        if low in hay:
            results.append({'type': 'action', 'title': item['title'],
                            'sub': item['sub'], 'url': item['url']})

    # 知识文档：文件名 + 正文（只读前 30KB，9 篇文档足够快）
    try:
        for root, dirs, files in os.walk(paths.docs_dir()):
            dirs[:] = sorted(x for x in dirs if not x.startswith('.'))
            for name in sorted(files):
                if not name.lower().endswith(('.md', '.txt')):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, paths.docs_dir()).replace(os.sep, '/')
                matched = low in rel.lower()
                if not matched:
                    try:
                        with open(path, encoding='utf-8', errors='replace') as f:
                            matched = low in f.read(30000).lower()
                    except Exception:
                        matched = False
                if matched:
                    results.append({'type': 'doc', 'title': rel, 'sub': '知识库文档',
                                    'url': '/?q=' + quote(request.args.get('q', ''))})
    except Exception:
        pass

    # 工单
    try:
        conn = repair_db.get_db()
        rows = conn.execute(
            'SELECT ticket_no, description, location, status FROM tickets '
            'WHERE ticket_no LIKE ? OR description LIKE ? OR location LIKE ? '
            'ORDER BY id DESC LIMIT ?',
            ('%' + q + '%', '%' + q + '%', '%' + q + '%', limit)).fetchall()
        conn.close()
        for r in rows:
            results.append({
                'type': 'ticket',
                'title': r['ticket_no'] + ' · ' + (r['description'] or '')[:30],
                'sub': (r['location'] or '未填地点') + ' · ' + repair_db.STATUS_LABEL.get(
                    r['status'], r['status']),
                'url': '/repair/admin?ticket=' + r['ticket_no'],
            })
    except Exception:
        pass

    # 设备
    try:
        conn = repair_db.get_db()
        rows = conn.execute(
            'SELECT code, name, location FROM assets '
            'WHERE code LIKE ? OR name LIKE ? OR location LIKE ? '
            'ORDER BY id DESC LIMIT ?',
            ('%' + q + '%', '%' + q + '%', '%' + q + '%', limit)).fetchall()
        conn.close()
        for r in rows:
            results.append({'type': 'asset', 'title': r['name'] + '（' + r['code'] + '）',
                            'sub': r['location'] or '设备资产',
                            'url': '/a/' + r['code']})
    except Exception:
        pass

    return jsonify({'q': q, 'results': results[:12]})


@app.route('/a/<code>')
def asset_page(code):
    """设备二维码短链：/a/<code>（手机扫码直接打开档案）。"""
    return send_from_directory(os.path.join(ROOT, 'modules', 'repair', 'static'),
                               'asset.html')


@app.route('/start')
def start():
    return send_from_directory(os.path.join(ROOT, 'modules', 'qa', 'static'), 'start.html')


@app.route('/favicon.ico')
def favicon():
    return '', 204


def main():
    cfg = config.load_cfg()
    app_cfg = cfg.get('app') or {}
    port = int(os.environ.get('PORT') or app_cfg.get('port') or 8100)
    host = os.environ.get('HOST') or app_cfg.get('host') or '127.0.0.1'
    print('设备运维智能工作台：http://%s:%d/' % (host, port))
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""测试用本地 mock 模型：OpenAI / Anthropic 两种格式。

- 系统提示包含“报修分诊员” → 返回分诊 JSON
- 系统提示包含“知识库助手” → 返回问答 JSON
- 其他 → 返回 OK
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _content_for(body):
    messages = body.get('messages') or []
    system = ' '.join(str(m.get('content') or '') for m in messages
                      if m.get('role') == 'system')
    if '报修分诊员' in system:
        return json.dumps({
            'category': '网络',
            'priority': '高',
            'summary': '交换机端口故障，整层无法上网',
            'checks': ['看交换机端口灯是否亮', '换一根网线测同一个口'],
            'confidence': 0.91,
            'sources': [1],
        }, ensure_ascii=False)
    if '知识库助手' in system:
        return json.dumps({
            'answer': 'MOCK 回答：先看光猫 LOS 灯，红灯则检查光纤接头并重启。',
            'sources': [1],
            'confidence': 0.9,
            'covered': True,
        }, ensure_ascii=False)
    return 'OK'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _read(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n).decode('utf-8') or '{}')

    def _send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = self._read()
        content = _content_for(body)
        if self.path.rstrip('/').endswith('/chat/completions'):
            self._send({
                'id': 'mock', 'object': 'chat.completion', 'model': body.get('model'),
                'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': content}}],
                'usage': {'prompt_tokens': 21, 'completion_tokens': 13, 'total_tokens': 34},
            })
            return
        if self.path.rstrip('/').endswith('/messages'):
            self._send({
                'id': 'msg_mock', 'type': 'message', 'role': 'assistant',
                'content': [{'type': 'text', 'text': content}],
                'usage': {'input_tokens': 18, 'output_tokens': 11},
            })
            return
        self._send({'error': {'message': 'not found'}}, 404)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()

# -*- coding: utf-8 -*-
"""本地 mock 模型服务：验证 OpenAI / Anthropic 两种调用格式。"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = '审计/mock_requests.jsonl'


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
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'path': self.path, 'body': body}, ensure_ascii=False) + '\n')
        if self.path.rstrip('/').endswith('/chat/completions'):
            answer = ('MOCK-OPENAI 收到 model=%s max_tokens=%s temperature=%s '
                      'reasoning_effort=%s' % (
                          body.get('model'), body.get('max_tokens'),
                          body.get('temperature'),
                          (body.get('reasoning_effort') or
                           (body.get('reasoning') or {}).get('effort') or 'none')))
            content = json.dumps({'answer': answer, 'sources': [1],
                                  'confidence': 0.9, 'covered': True},
                                 ensure_ascii=False)
            self._send({
                'id': 'mock', 'object': 'chat.completion', 'model': body.get('model'),
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content},
                             'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18},
            })
            return
        if self.path.rstrip('/').endswith('/messages'):
            answer = ('MOCK-ANTHROPIC 收到 model=%s max_tokens=%s' % (
                body.get('model'), body.get('max_tokens')))
            content = json.dumps({'answer': answer, 'sources': [1],
                                  'confidence': 0.88, 'covered': True},
                                 ensure_ascii=False)
            self._send({
                'id': 'msg_mock', 'type': 'message', 'role': 'assistant',
                'content': [{'type': 'text', 'text': content}],
                'usage': {'input_tokens': 12, 'output_tokens': 8},
            })
            return
        self._send({'error': {'message': 'not found'}}, 404)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8199
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()

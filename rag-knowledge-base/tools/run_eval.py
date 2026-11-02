# -*- coding: utf-8 -*-
"""运行检索评估：python tools/run_eval.py [--hybrid 0/1]"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rag import evaluate, retrieve


def main():
    hybrid = '0' not in sys.argv
    with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    with open(os.path.join(ROOT, 'data/tests/eval_set.json'), encoding='utf-8') as f:
        dataset = json.load(f)['items']

    chroma_dir = os.path.join(ROOT, cfg['chroma_dir'])
    model = cfg['embedding_model']

    def fn(q, k):
        return retrieve.retrieve(q, k, chroma_dir, model, hybrid=hybrid)

    result = evaluate.evaluate(dataset, fn, ks=(1, 3, 5))
    result['hybrid'] = hybrid
    out = os.path.join(ROOT, 'data/tests/eval_result.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print('测试集: %d 题（%s）' % (result['total'], '混合检索' if hybrid else '纯向量'))
    for k, v in result['metrics'].items():
        print('  %-10s %.4f' % (k, v))
    print('  %-10s %.4f' % ('MRR', result['mrr']))
    if result['missed']:
        print('未命中 %d 题，示例：' % len(result['missed']))
        for m in result['missed'][:3]:
            print('  Q: %s' % m['q'])
            print('     gold: %s' % m['gold'])
            print('     top1: %s' % m['top1'])
    print('结果已保存：', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())

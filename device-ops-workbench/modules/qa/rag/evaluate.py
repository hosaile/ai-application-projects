# -*- coding: utf-8 -*-
"""评估：检索指标 Recall@K / Hit@K / MRR，答案关键词命中率。"""
import re


def hit_key(item):
    meta = item.get('meta') or {}
    return '%s#%s' % (meta.get('source', ''), meta.get('heading', ''))


def evaluate(dataset, retrieve_fn, ks=(1, 3, 5)):
    """dataset: [{'q': str, 'gold': [source#heading, ...], 'must_have': [关键词]}]

    retrieve_fn(question, k) -> hits 列表
    """
    per_k = {k: {'recall': 0.0, 'hit': 0, 'n': 0} for k in ks}
    mrr_total = 0.0
    missing = []
    max_k = max(ks)
    for item in dataset:
        gold = set(item.get('gold') or [])
        if not gold:
            continue
        hits = retrieve_fn(item['q'], max_k)
        keys = [hit_key(h) for h in hits]
        first_rank = None
        for rank, key in enumerate(keys, 1):
            if key in gold:
                first_rank = rank
                break
        if first_rank:
            mrr_total += 1.0 / first_rank
        for k in ks:
            top = set(keys[:k])
            inter = gold & top
            per_k[k]['recall'] += len(inter) / float(len(gold))
            per_k[k]['hit'] += 1 if inter else 0
            per_k[k]['n'] += 1
        if first_rank is None:
            missing.append({'q': item['q'], 'gold': sorted(gold), 'top1': keys[0] if keys else ''})
    n = max(1, per_k[max_k]['n'])
    result = {
        'total': per_k[max_k]['n'],
        'mrr': round(mrr_total / n, 4),
        'metrics': {},
        'missed': missing[:10],
    }
    for k in ks:
        result['metrics']['Recall@%d' % k] = round(per_k[k]['recall'] / n, 4)
        result['metrics']['Hit@%d' % k] = round(per_k[k]['hit'] / float(n), 4)
    return result


def keyword_coverage(answer_text, must_have):
    """答案对关键词的覆盖率（轻量准确率代理指标）。"""
    if not must_have:
        return None
    hits = sum(1 for w in must_have if w and w in answer_text)
    return round(hits / float(len(must_have)), 4)

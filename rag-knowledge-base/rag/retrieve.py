# -*- coding: utf-8 -*-
"""检索层：向量召回 + 关键词加权（可解释的轻量混合检索）。"""
import re

from rag import store


def _tokens(text):
    """粗粒度关键词：英文/数字按词切，中文取 2-4 字片段。"""
    words = set(re.findall(r'[A-Za-z]{2,}|[0-9]+|[A-Za-z]+[0-9]+', text))
    for span in re.findall(r'[\u4e00-\u9fa5]{2,4}', text):
        words.add(span)
    return words


def _keyword_bonus(question, text):
    """问题中的词在 chunk 中出现，给轻微加分（封顶 0.12）。"""
    q = _tokens(question)
    if not q:
        return 0.0
    hit = sum(1 for w in q if w in text)
    return min(0.12, 0.03 * hit)


def retrieve(question, top_k, chroma_dir, model_name, hybrid=True):
    """返回按分数排序的检索结果；hybrid=True 时叠加关键词加权。

    同一章节（source#heading）去重，只保留最高分的那块。
    """
    raw = store.query(question, max(top_k * 6, top_k), chroma_dir, model_name)
    if hybrid:
        for h in raw:
            h['kw_bonus'] = round(_keyword_bonus(question, h['text']), 4)
            h['score'] = round(h['score'] + h['kw_bonus'], 4)
        raw.sort(key=lambda x: -x['score'])
    seen, deduped = set(), []
    for h in raw:
        key = '%s#%s' % (h['meta'].get('source', ''), h['meta'].get('heading', ''))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
        if len(deduped) >= top_k:
            break
    return deduped

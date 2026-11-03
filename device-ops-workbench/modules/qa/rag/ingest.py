# -*- coding: utf-8 -*-
"""文档加载与切分：按 Markdown 标题分节，再按字符长度切块（带重叠）。"""
import hashlib
import os
import re


def load_docs(docs_dir):
    """递归读取 .md / .txt，返回 [(相对路径, text)]；支持 experience/ 子目录。"""
    items = []
    if not os.path.isdir(docs_dir):
        return items
    for root, dirs, files in os.walk(docs_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for name in sorted(files):
            if not name.lower().endswith(('.md', '.txt')):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, docs_dir).replace(os.sep, '/')
            try:
                with open(path, encoding='utf-8') as f:
                    items.append((rel, f.read()))
            except Exception:
                continue
    return items


def _split_sections(text):
    """按二级标题分节；没有标题的文本作为一节。"""
    lines = text.split('\n')
    sections = []
    heading = '正文'
    buf = []
    for line in lines:
        m = re.match(r'^#{2,3}\s+(.*)$', line.strip())
        if m:
            if buf and ''.join(buf).strip():
                sections.append((heading, '\n'.join(buf).strip()))
            heading = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if buf and ''.join(buf).strip():
        sections.append((heading, '\n'.join(buf).strip()))
    return sections


def _split_chunks(text, size, overlap):
    """按字符切块，尽量在句号/换行处断开，块间保留 overlap 字重叠。"""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # 在窗口后半段找句末标点，避免切断句子
            window = text[start:end]
            cut = max(window.rfind('。'), window.rfind('；'), window.rfind('\n'))
            if cut > size * 0.5:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def build_chunks(docs, size=400, overlap=50):
    """生成带元数据的 chunk 列表。

    每个 chunk: {id, text, meta: {source, heading, seq}}
    """
    overlap = max(0, min(overlap, max(1, size // 2)))  # 防 overlap>=size 造成重复块膨胀
    chunks = []
    for source, text in docs:
        for heading, body in _split_sections(text):
            if heading == '正文' and len(body) < 80:
                continue  # 仅跳过文档开头的标题/说明块（正文类短文档仍保留）
            pieces = _split_chunks(body, size, overlap)
            for i, piece in enumerate(pieces):
                raw = '%s#%s#%d#%s' % (source, heading, i, piece[:40])
                cid = hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]
                chunks.append({
                    'id': cid,
                    'text': piece,
                    'meta': {'source': source, 'heading': heading, 'seq': i},
                })
    return chunks

"""Reproducible local latent semantic indexing; no pretrained model/network."""

import hashlib
from collections import Counter
import re
import numpy as np

STOP = set(
    "the a an is are was were what which how and or of to in on for by with does do this that it described document supplied can be".split()
)
PARAMETERS = {
    "model": "local-tfidf-lsa",
    "version": "1",
    "license": "project license pending; NumPy BSD-3-Clause",
    "vocabulary_max": 12000,
    "dimensions_max": 64,
    "chunk_chars": 700,
    "overlap_chars": 100,
    "cosine_threshold": 0.18,
    "lexical_gate": True,
}


def tokens(text):
    return [
        x
        for x in re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]", text.lower())
        if x not in STOP
    ]


def chunks(doc):
    result = []
    for page in doc["pages"]:
        if page.get("status", "parsed") != "parsed":
            continue
        text = page["text"]
        for start in range(0, len(text), 600):
            fragment = text[start : start + 700]
            if not fragment.strip():
                continue
            identity = f"{doc['document_id']}:{page['page']}:{start}:{fragment}"
            result.append(
                {
                    "document_id": doc["document_id"],
                    "filename": doc["filename"],
                    "page": page["page"],
                    "text_source": page.get("source", "text_layer"),
                    "start": start,
                    "end": start + len(fragment),
                    "text": fragment,
                    "citation_id": "c-"
                    + hashlib.sha256(identity.encode()).hexdigest()[:24],
                }
            )
    return result


def retrieve(doc, query, top_k):
    items = chunks(doc)
    corpus = [tokens(c["text"]) for c in items]
    frequencies = Counter(t for row in corpus for t in row)
    vocab = sorted(sorted(frequencies, key=lambda t: (-frequencies[t], t))[:12000])
    if not vocab:
        return []
    pos = {t: i for i, t in enumerate(vocab)}
    counts = np.zeros((len(items), len(vocab)))
    q = np.zeros(len(vocab))
    for i, row in enumerate(corpus):
        for token in row:
            if token in pos:
                counts[i, pos[token]] += 1
    for token in tokens(query):
        if token in pos:
            q[pos[token]] += 1
    if not q.any():
        return []
    idf = np.log((1 + len(items)) / (1 + (counts > 0).sum(axis=0))) + 1
    matrix = np.log1p(counts) * idf
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    _, _, vt = np.linalg.svd(matrix, full_matrices=False)
    k = min(64, max(1, len(items) - 1), len(vt))
    vectors = matrix @ vt[:k].T
    query_vector = (np.log1p(q) * idf) @ vt[:k].T
    scores = (vectors @ query_vector) / (
        np.maximum(
            np.linalg.norm(vectors, axis=1) * np.linalg.norm(query_vector), 1e-12
        )
    )
    query_tokens = set(tokens(query))
    selected = [
        i
        for i, s in enumerate(scores)
        if s >= PARAMETERS["cosine_threshold"] and query_tokens.intersection(corpus[i])
    ]
    selected.sort(key=lambda i: (-scores[i], items[i]["citation_id"]))
    return [dict(items[i], score=round(float(scores[i]), 6)) for i in selected[:top_k]]

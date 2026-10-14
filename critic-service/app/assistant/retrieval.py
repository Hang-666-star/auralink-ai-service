from __future__ import annotations
import asyncio
import hashlib
import json
import httpx


def result(state, citations=None):
    return {"contractVersion": 1, "source": "RAGFLOW", "state": state, "citations": citations or []}


async def retrieve(client, request, settings, key):
    if not request.enabled:
        return result("DISABLED")
    rag = settings.ragflow
    if not rag.enabled:
        return result("NOT_CONFIGURED")
    if not request.query.strip():
        return result("EMPTY")
    async def operation():
        async with client.stream("POST", rag.baseUrl.rstrip("/") + "/api/v1/retrieval",
            headers={"Authorization": "Bearer " + key},
            json={"question": request.query, "dataset_ids": rag.datasetIds, "top_k": rag.topN, "page": 1, "page_size": rag.topN}) as response:
            if response.status_code in {401, 403}:
                return result("FORBIDDEN")
            if response.status_code != 200:
                return result("UNAVAILABLE")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 524288:
                    return result("UNAVAILABLE")
            payload = json.loads(raw)
        if payload.get("code") != 0:
            return result("FORBIDDEN" if payload.get("code") in {401, 403} else "UNAVAILABLE")
        chunks = payload.get("data", {}).get("chunks")
        if not isinstance(chunks, list):
            return result("UNAVAILABLE")
        citations = []
        for chunk in chunks[:rag.topN]:
            dataset = str(chunk.get("dataset_id", ""))
            document = str(chunk.get("document_id", ""))
            chunk_id = str(chunk.get("id", ""))
            text = chunk.get("content")
            if dataset not in rag.datasetIds or not document or not chunk_id or not isinstance(text, str):
                return result("FORBIDDEN" if dataset not in rag.datasetIds else "UNAVAILABLE")
            opaque = hashlib.sha256((dataset + ":" + document + ":" + chunk_id).encode()).hexdigest()[:24]
            citations.append({"id": "ragflow:" + opaque, "type": "RAGFLOW", "datasetId": dataset[:160], "documentId": document[:160],
                "chunkId": chunk_id[:160], "title": str(chunk.get("document_keyword", "文档片段"))[:200], "text": text[:8000],
                "version": str(chunk["document_version"])[:160] if chunk.get("document_version") else None,
                "contentSha256": hashlib.sha256(text.encode()).hexdigest()})
        return result("AVAILABLE" if citations else "EMPTY", citations)
    try:
        return await asyncio.wait_for(operation(), rag.timeoutMs / 1000)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return result("TIMEOUT")
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return result("UNAVAILABLE")

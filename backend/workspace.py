"""Single-process local workspace over existing PDF/JSON files; no credentials."""

import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from fastapi import HTTPException

OWNED = re.compile(r"^[a-f0-9]{32}(?:-result|-turn)?\.json$|^[a-f0-9]{32}\.pdf$")


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.running = set()

    @contextmanager
    def operation(self, identity):
        with self.lock:
            self.running.add(identity)
        try:
            yield
        finally:
            with self.lock:
                self.running.discard(identity)

    def save(self, identity, suffix, data):
        with self.lock:
            target = self.root / (identity + suffix)
            temp = self.root / (uuid.uuid4().hex + ".tmp")
            temp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp.replace(target)

    def snapshot(self):
        with self.lock:
            documents, results, turns, warnings = {}, {}, [], []
            for path in sorted(self.root.iterdir()):
                if (
                    not OWNED.fullmatch(path.name)
                    or path.suffix != ".json"
                    or path.is_symlink()
                ):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or not re.fullmatch(
                        r"[a-f0-9]{32}", str(data.get("document_id", ""))
                    ):
                        raise ValueError("invalid workspace record")
                    data.setdefault("created_at", path.stat().st_mtime)
                    if path.name.endswith("-turn.json"):
                        if (
                            data["status"] == "running"
                            and data["turn_id"] not in self.running
                        ):
                            data["status"] = "interrupted"
                            data["error"] = "上次请求已中断，未自动重新调用模型。"
                        turns.append(data)
                    elif path.name.endswith("-result.json"):
                        results[data["result_id"]] = data
                    elif (self.root / (data["document_id"] + ".pdf")).is_file():
                        documents[data["document_id"]] = data
                except (ValueError, KeyError, TypeError):
                    warnings.append("一份本地记录无法读取，已保留原文件。")
            # Older completed results predate turn journaling. Rebuild their chat association.
            recorded = {t.get("result_id") for t in turns}
            for result in results.values():
                if result["result_id"] not in recorded:
                    turns.append(
                        {
                            "turn_id": result["result_id"],
                            "document_id": result["document_id"],
                            "query": result["query"],
                            "status": "complete",
                            "result_id": result["result_id"],
                            "created_at": result["created_at"],
                        }
                    )
            valid_turns = []
            for turn in sorted(turns, key=lambda t: t["created_at"]):
                doc = documents.get(turn["document_id"])
                if not doc:
                    continue
                result = results.get(turn.get("result_id"))
                if result:
                    if (
                        result["document_id"] != doc["document_id"]
                        or result["source_sha256"] != doc["sha256"]
                        or any(
                            c["document_id"] != doc["document_id"]
                            for c in result["citations"]
                        )
                    ):
                        turn["status"] = "unavailable"
                        turn["error"] = "结果与源文件关联不一致，已禁止恢复和导出。"
                    else:
                        turn["result"] = result
                elif turn["status"] == "complete":
                    turn["status"] = "unavailable"
                    turn["error"] = "结果文件缺失，请重新提问。"
                valid_turns.append(turn)
            return {
                "documents": sorted(documents.values(), key=lambda d: d["created_at"]),
                "turns": valid_turns,
                "warnings": warnings,
            }

    def clear(self):
        with self.lock:
            if self.running:
                raise HTTPException(409, "仍有上传或回答正在处理，请完成后再清空。")
            paths = [
                p
                for p in self.root.iterdir()
                if OWNED.fullmatch(p.name) and p.is_file() and not p.is_symlink()
            ]
            # Checked, exact direct children only. Never recurse or follow links.
            if any(p.resolve().parent != self.root for p in paths):
                raise HTTPException(409, "存储路径异常，未清空。")
            for path in paths:
                path.unlink()
            return {"deleted_files": len(paths)}


def now():
    return time.time()

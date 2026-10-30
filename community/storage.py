import json
import os
import uuid
import time
import threading
from pathlib import Path
from typing import Optional
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows fallback

class CommunityStorage:
    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = os.environ.get("COMMUNITY_DATA_DIR", "/var/data/community")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tools_file = self.data_dir / "tools.json"
        self._lock = threading.Lock()
        self._init_file()

    def _init_file(self):
        if not self.tools_file.exists():
            self._write_atomic({"tools": [], "version": 1})

    def _read(self) -> dict:
        with self._lock:
            with open(self.tools_file, 'r', encoding='utf-8') as f:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    if fcntl:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _write_atomic(self, data: dict):
        with self._lock:
            tmp_file = self.tools_file.with_suffix('.tmp')
            with open(tmp_file, 'w', encoding='utf-8') as f:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                json.dump(data, f, ensure_ascii=False, indent=2)
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            tmp_file.replace(self.tools_file)

    def get_tools(self, page: int = 1, per_page: int = 12, tag: str = None, search: str = None) -> dict:
        data = self._read()
        tools = data.get("tools", [])

        if tag:
            tools = [t for t in tools if tag in t.get("tags", [])]

        if search:
            search_lower = search.lower()
            tools = [t for t in tools if search_lower in t.get("name", "").lower()
                     or search_lower in t.get("description", "").lower()]

        tools.sort(key=lambda t: t.get("created_at", 0), reverse=True)

        total = len(tools)
        pages = (total + per_page - 1) // per_page
        start = (page - 1) * per_page
        end = start + per_page

        return {
            "tools": tools[start:end],
            "total": total,
            "page": page,
            "pages": pages,
            "per_page": per_page
        }

    def get_tool(self, tool_id: str) -> Optional[dict]:
        data = self._read()
        for tool in data.get("tools", []):
            if tool.get("id") == tool_id:
                return tool
        return None

    def get_tags(self) -> list:
        data = self._read()
        tags = set()
        for tool in data.get("tools", []):
            for tag in tool.get("tags", []):
                tags.add(tag)
        return sorted(tags)

    def save_tool(self, tool_data: dict) -> str:
        tool_id = uuid.uuid4().hex[:12]
        now = time.time()

        tool = {
            "id": tool_id,
            "name": tool_data.get("name", "Untitled Tool"),
            "description": tool_data.get("description", ""),
            "code": tool_data.get("code", ""),
            "tags": tool_data.get("tags", []),
            "author": tool_data.get("author", "Anonymous"),
            "created_at": now,
            "updated_at": now,
            "views": 0,
            "forks": 0,
            "status": "pending_review"
        }

        data = self._read()
        data["tools"].append(tool)
        self._write_atomic(data)

        return tool_id

    def increment_views(self, tool_id: str) -> None:
        data = self._read()
        for tool in data.get("tools", []):
            if tool.get("id") == tool_id:
                tool["views"] = tool.get("views", 0) + 1
                self._write_atomic(data)
                break

    def increment_forks(self, tool_id: str) -> None:
        data = self._read()
        for tool in data.get("tools", []):
            if tool.get("id") == tool_id:
                tool["forks"] = tool.get("forks", 0) + 1
                self._write_atomic(data)
                break
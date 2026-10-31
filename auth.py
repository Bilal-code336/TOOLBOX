import os
import json
import uuid
import time
import hashlib
import hmac
import base64
import threading
from pathlib import Path
from typing import Optional, Dict, Any
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows fallback

try:
    import bcrypt
except ImportError:
    bcrypt = None

# JWT implementation without external dependencies
import secrets


class AuthStorage:
    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = os.environ.get("AUTH_DATA_DIR", os.path.join(os.path.dirname(__file__), "data", "auth"))
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_file = self.data_dir / "users.json"
        self.sessions_file = self.data_dir / "sessions.json"
        self._lock = threading.Lock()
        self._init_files()

    def _init_files(self):
        if not self.users_file.exists():
            self._write_atomic(self.users_file, {"users": [], "version": 1})
        if not self.sessions_file.exists():
            self._write_atomic(self.sessions_file, {"sessions": [], "version": 1})

    def _read(self, file: Path) -> dict:
        with self._lock:
            with open(file, 'r', encoding='utf-8') as f:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    if fcntl:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _write_atomic(self, file: Path, data: dict):
        with self._lock:
            tmp_file = file.with_suffix('.tmp')
            with open(tmp_file, 'w', encoding='utf-8') as f:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                json.dump(data, f, ensure_ascii=False, indent=2)
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            tmp_file.replace(file)

    # User management
    def get_user_by_email(self, email: str) -> Optional[dict]:
        data = self._read(self.users_file)
        for user in data.get("users", []):
            if user.get("email", "").lower() == email.lower():
                return user
        return None

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        data = self._read(self.users_file)
        for user in data.get("users", []):
            if user.get("id") == user_id:
                return user
        return None

    def create_user(self, email: str, password_hash: str, name: str) -> str:
        user_id = uuid.uuid4().hex[:12]
        now = time.time()

        user = {
            "id": user_id,
            "email": email.lower(),
            "name": name[:50],
            "password_hash": password_hash,
            "created_at": now,
            "email_verified": False,
            "tools_count": 0
        }

        data = self._read(self.users_file)
        data["users"].append(user)
        self._write_atomic(self.users_file, data)
        return user_id

    def update_user(self, user_id: str, updates: dict) -> bool:
        data = self._read(self.users_file)
        for user in data.get("users", []):
            if user.get("id") == user_id:
                user.update(updates)
                user["updated_at"] = time.time()
                self._write_atomic(self.users_file, data)
                return True
        return False

    def increment_tools_count(self, user_id: str):
        data = self._read(self.users_file)
        for user in data.get("users", []):
            if user.get("id") == user_id:
                user["tools_count"] = user.get("tools_count", 0) + 1
                self._write_atomic(self.users_file, data)
                break

    def decrement_tools_count(self, user_id: str):
        data = self._read(self.users_file)
        for user in data.get("users", []):
            if user.get("id") == user_id:
                user["tools_count"] = max(0, user.get("tools_count", 0) - 1)
                self._write_atomic(self.users_file, data)
                break

    # Session management
    def create_session(self, user_id: str, ip: str = "", user_agent: str = "") -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        expires = now + 30 * 24 * 3600  # 30 days

        session = {
            "id": session_id,
            "user_id": user_id,
            "created_at": now,
            "expires_at": expires,
            "ip": ip,
            "user_agent": user_agent
        }

        data = self._read(self.sessions_file)
        data["sessions"].append(session)
        self._write_atomic(self.sessions_file, data)
        return session_id

    def get_session(self, session_id: str) -> Optional[dict]:
        data = self._read(self.sessions_file)
        now = time.time()
        for session in data.get("sessions", []):
            if session.get("id") == session_id and session.get("expires_at", 0) > now:
                return session
        return None

    def delete_session(self, session_id: str) -> bool:
        data = self._read(self.sessions_file)
        sessions = data.get("sessions", [])
        for i, session in enumerate(sessions):
            if session.get("id") == session_id:
                sessions.pop(i)
                self._write_atomic(self.sessions_file, data)
                return True
        return False

    def delete_user_sessions(self, user_id: str):
        data = self._read(self.sessions_file)
        data["sessions"] = [s for s in data.get("sessions", []) if s.get("user_id") != user_id]
        self._write_atomic(self.sessions_file, data)

    def cleanup_expired_sessions(self):
        data = self._read(self.sessions_file)
        now = time.time()
        data["sessions"] = [s for s in data.get("sessions", []) if s.get("expires_at", 0) > now]
        self._write_atomic(self.sessions_file, data)


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    if bcrypt is None:
        # Fallback: simple hash (NOT secure, only for dev without bcrypt)
        return hashlib.sha256(password.encode()).hexdigest()
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash."""
    if bcrypt is None:
        # Fallback: simple hash verification
        return hashlib.sha256(password.encode()).hexdigest() == password_hash
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf_token(token: str, expected: str) -> bool:
    return hmac.compare_digest(token, expected)


# User session helper for Flask
class AuthManager:
    def __init__(self, storage: AuthStorage):
        self.storage = storage

    def get_current_user(self, request) -> Optional[dict]:
        """Get current user from session cookie or Authorization header."""
        # Try cookie first
        session_id = request.cookies.get("session_id")
        if not session_id:
            # Try Authorization header
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                session_id = auth[7:]

        if not session_id:
            return None

        session = self.storage.get_session(session_id)
        if not session:
            return None

        user = self.storage.get_user_by_id(session["user_id"])
        if not user:
            return None

        # Don't expose password hash
        safe_user = {k: v for k, v in user.items() if k != "password_hash"}
        return safe_user

    def require_auth(self, request) -> dict:
        """Get current user or raise 401."""
        user = self.get_current_user(request)
        if not user:
            from flask import abort
            abort(401, description="Authentication required")
        return user

    def login(self, email: str, password: str, ip: str = "", user_agent: str = "") -> Optional[dict]:
        """Authenticate user and create session."""
        user = self.storage.get_user_by_email(email)
        if not user:
            return None

        if not verify_password(password, user["password_hash"]):
            return None

        session_id = self.storage.create_session(user["id"], ip, user_agent)
        safe_user = {k: v for k, v in user.items() if k != "password_hash"}
        return {"user": safe_user, "session_id": session_id}

    def register(self, email: str, password: str, name: str, ip: str = "", user_agent: str = "") -> Optional[dict]:
        """Register new user and create session."""
        # Check if email exists
        if self.storage.get_user_by_email(email):
            return {"error": "Email ya registrado"}

        # Validate
        if len(password) < 8:
            return {"error": "La contraseña debe tener al menos 8 caracteres"}

        if not name.strip():
            return {"error": "Nombre requerido"}

        password_hash = hash_password(password)
        user_id = self.storage.create_user(email, password_hash, name.strip())
        session_id = self.storage.create_session(user_id, ip, user_agent)

        user = self.storage.get_user_by_id(user_id)
        safe_user = {k: v for k, v in user.items() if k != "password_hash"}
        return {"user": safe_user, "session_id": session_id}

    def logout(self, session_id: str) -> bool:
        return self.storage.delete_session(session_id)
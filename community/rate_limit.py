import time
import threading
from collections import defaultdict, deque
from typing import Tuple

class UploadRateLimiter:
    def __init__(self, per_hour: int = 5, per_day: int = 20):
        self.per_hour = per_hour
        self.per_day = per_day
        self._hourly = defaultdict(deque)
        self._daily = defaultdict(deque)
        self._lock = threading.Lock()

    def _clean_old(self, dq: deque, window: int):
        now = time.time()
        while dq and now - dq[0] > window:
            dq.popleft()

    def check_limit(self, ip: str) -> Tuple[bool, str]:
        with self._lock:
            now = time.time()
            self._clean_old(self._hourly[ip], 3600)
            self._clean_old(self._daily[ip], 86400)

            hourly_count = len(self._hourly[ip])
            daily_count = len(self._daily[ip])

            if hourly_count >= self.per_hour:
                return False, f"Límite de {self.per_hour} subidas por hora alcanzado. Inténtalo más tarde."

            if daily_count >= self.per_day:
                return False, f"Límite de {self.per_day} subidas por día alcanzado. Inténtalo mañana."

            return True, ""

    def record_upload(self, ip: str) -> None:
        with self._lock:
            now = time.time()
            self._hourly[ip].append(now)
            self._daily[ip].append(now)
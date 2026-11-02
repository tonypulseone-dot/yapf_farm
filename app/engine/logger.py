import logging
import threading
import time
from collections import deque
from datetime import datetime

class LiveLogBuffer:
    def __init__(self, maxlen: int = 1500):
        self.maxlen = maxlen
        self.buffer = deque(maxlen=maxlen)
        self.lock = threading.Lock()
        self.counter = 0

    def add(self, level: str, tag: str, message: str, details: str = ""):
        """
        level: 'INFO' | 'WARM' | 'PROMO' | 'SUCCESS' | 'WARN' | 'ERROR'
        tag: 'orchestrator' | 'prof_1_4150' | 'proxy' | 'captcha' etc.
        """
        with self.lock:
            self.counter += 1
            entry = {
                "id": self.counter,
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level.upper(),
                "tag": tag,
                "message": message,
                "details": details or ""
            }
            self.buffer.append(entry)
            return entry

    def get_since(self, since_id: int = 0, limit: int = 150, filter_level: str = None):
        with self.lock:
            all_items = list(self.buffer)
            if since_id > 0:
                items = [item for item in all_items if item["id"] > since_id]
            else:
                items = all_items
            
            if filter_level and filter_level not in ["ALL", ""]:
                items = [item for item in items if item["level"] == filter_level.upper()]
            
            if limit and len(items) > limit:
                items = items[-limit:]
            
            last_id = self.buffer[-1]["id"] if self.buffer else since_id
            return {
                "logs": items,
                "last_id": last_id,
                "total_count": len(self.buffer)
            }

    def clear(self):
        with self.lock:
            self.buffer.clear()
            self.counter = 0

live_logger = LiveLogBuffer()

class LiveLogHandler(logging.Handler):
    """Intercepts Python logger calls and pipes them into live_logger."""
    def emit(self, record):
        try:
            msg = record.getMessage()
            lvl = record.levelname
            name = record.name
            
            tag = name
            level = lvl
            if "warmer" in name:
                level = "WARM"
            elif "promo" in name:
                level = "PROMO"
            elif "orchestrator" in name:
                level = "INFO"
                
            if msg.startswith("[") and "]" in msg:
                end_idx = msg.find("]")
                tag = msg[1:end_idx]
                msg = msg[end_idx+1:].strip()
                
            if "completed" in msg.lower() or "success" in msg.lower():
                level = "SUCCESS"
            elif "error" in lvl.lower() or "exception" in msg.lower():
                level = "ERROR"
            elif "warning" in lvl.lower() or "warn" in lvl.lower():
                level = "WARN"

            live_logger.add(level=level, tag=tag, message=msg)
        except Exception:
            pass

# Attach handler to root or custom loggers
live_handler = LiveLogHandler()
live_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("orchestrator").addHandler(live_handler)
logging.getLogger("warmer").addHandler(live_handler)
logging.getLogger("promo").addHandler(live_handler)
logging.getLogger("captcha_engine").addHandler(live_handler)

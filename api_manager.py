# api_manager.py
# Handles storage, retrieval, and management of Google API keys.

import json
import time
from pathlib import Path

class APIManager:
    def __init__(self, filename: str = 'api_keys.json', status_filename: str = 'api_keys_status.json'):
        # Store the config file in the user's home directory for cross-platform compatibility
        # and to avoid permission errors.
        self.filepath = Path.home() / filename
        self.status_path = Path.home() / status_filename
        self.keys = []
        self.status = {"cooldowns": {}}  # key -> epoch_until
        self.load_keys()
        self.load_status()

    def load_keys(self):
        """Loads API keys from the JSON file."""
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r') as f:
                    self.keys = json.load(f)
            except json.JSONDecodeError:
                self.keys = []
        else:
            self.keys = []
        return self.keys

    def save_keys(self):
        """Saves the current list of API keys to the JSON file."""
        with open(self.filepath, 'w') as f:
            json.dump(self.keys, f, indent=2)

    def load_status(self):
        if self.status_path.exists():
            try:
                with open(self.status_path, 'r') as f:
                    self.status = json.load(f)
            except json.JSONDecodeError:
                self.status = {"cooldowns": {}}
        else:
            self.status = {"cooldowns": {}}
        # Clean expired on load
        self._cleanup_expired_cooldowns()

    def save_status(self):
        with open(self.status_path, 'w') as f:
            json.dump(self.status, f, indent=2)

    def add_key(self, key: str):
        """Adds a new key to the list if it's not already there."""
        if key and key not in self.keys:
            self.keys.append(key)
            self.save_keys()
            return True
        return False

    def delete_key(self, key: str):
        """Deletes a specific key from the list."""
        if key in self.keys:
            self.keys.remove(key)
            self.save_keys()
            return True
        return False

    def clear_all_keys(self):
        """Removes all keys."""
        self.keys = []
        self.save_keys()

    def get_keys(self) -> list:
        """Returns the list of all keys."""
        return self.keys

    def get_key(self) -> str | None:
        """Returns the first available key from the list."""
        if self.keys:
            return self.keys[0]
        return None

    # New methods for cooldown management
    def _cleanup_expired_cooldowns(self):
        now = int(time.time())
        cds = self.status.get("cooldowns", {})
        expired = [k for k, until in cds.items() if not isinstance(until, int) or until <= now]
        for k in expired:
            cds.pop(k, None)
        self.status["cooldowns"] = cds

    def set_key_cooldown(self, key: str, seconds: int = 24 * 3600):
        if not key:
            return
        now = int(time.time())
        until = now + max(0, int(seconds))
        self.status.setdefault("cooldowns", {})[key] = until
        self.save_status()

    def is_key_on_cooldown(self, key: str) -> bool:
        self._cleanup_expired_cooldowns()
        cds = self.status.get("cooldowns", {})
        until = cds.get(key)
        return bool(until and until > int(time.time()))

    def get_available_keys(self) -> list:
        """Returns keys that are not on cooldown (or cooldown expired)."""
        self._cleanup_expired_cooldowns()
        cds = self.status.get("cooldowns", {})
        now = int(time.time())
        return [k for k in self.keys if (cds.get(k, 0) <= now)]

    def get_cooldowns(self) -> dict:
        """Returns a dict of key -> epoch_until for active cooldowns (expired removed)."""
        self._cleanup_expired_cooldowns()
        return dict(self.status.get("cooldowns", {}))

    def get_cooldown_remaining(self, key: str) -> int:
        """Returns remaining cooldown seconds for a key (0 if available)."""
        self._cleanup_expired_cooldowns()
        until = self.status.get("cooldowns", {}).get(key)
        if not isinstance(until, int):
            return 0
        rem = until - int(time.time())
        return rem if rem > 0 else 0

    def get_status_list(self) -> list[tuple[str, int]]:
        """Returns list of (key, remaining_seconds). 0 means available."""
        return [(k, self.get_cooldown_remaining(k)) for k in self.keys]

# api_manager.py
# Handles storage, retrieval, and management of Google API keys.

import json
from pathlib import Path

class APIManager:
    def __init__(self, filename: str = 'api_keys.json'):
        # Store the config file in the user's home directory for cross-platform compatibility
        # and to avoid permission errors.
        self.filepath = Path.home() / filename
        self.keys = []
        self.load_keys()

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

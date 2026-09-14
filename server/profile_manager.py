#!/usr/bin/env python3
"""Profile manager — loads, validates, and hot-reloads JSON profiles.

Profiles define which APIs, models, and processing chains are active.
"""

import os
import json
import time
import threading
from datetime import datetime


DEFAULT_PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'profiles')


class ProfileManager:
    """Loads and manages processing profiles."""

    def __init__(self, profile_dir=None):
        self.profile_dir = profile_dir or DEFAULT_PROFILE_DIR
        self._profiles = {}
        self._active_name = None
        self._lock = threading.Lock()
        self._last_scan = 0
        self._scan()

    def _scan(self):
        """Load all JSON profiles from the profile directory."""
        if not os.path.isdir(self.profile_dir):
            return
        with self._lock:
            for name in os.listdir(self.profile_dir):
                if not name.endswith('.json'):
                    continue
                path = os.path.join(self.profile_dir, name)
                try:
                    with open(path, 'r') as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        continue
                    pid = data.get('name', name)
                    self._profiles[pid] = data
                except Exception as e:
                    print(f"[profile] failed to load {name}: {e}")
            # Pick active profile
            for pid, data in self._profiles.items():
                if data.get('active'):
                    self._active_name = pid
                    break
            if self._active_name is None and self._profiles:
                self._active_name = next(iter(self._profiles))
            self._last_scan = time.time()

    def reload(self):
        """Force reload all profiles from disk."""
        with self._lock:
            self._profiles.clear()
            self._active_name = None
        self._scan()

    def get_active(self):
        """Return the currently active profile dict (copy) or None."""
        with self._lock:
            if self._active_name is None:
                return None
            return dict(self._profiles.get(self._active_name, {}))

    def set_active(self, name):
        """Set the active profile by name (must match profile['name'] or filename)."""
        with self._lock:
            # Match by name or filename
            for pid, data in self._profiles.items():
                if pid == name or data.get('name') == name:
                    for d in self._profiles.values():
                        d['active'] = False
                    data['active'] = True
                    self._active_name = pid
                    return True
        return False

    def list_profiles(self):
        """Return list of {name, description, active, filename}."""
        with self._lock:
            return [
                {
                    'name': pid,
                    'description': data.get('description', ''),
                    'active': data.get('active', False),
                }
                for pid, data in self._profiles.items()
            ]

    def get(self, name):
        """Return a specific profile by name or None."""
        with self._lock:
            return dict(self._profiles.get(name, {}))

    @property
    def active_name(self):
        return self._active_name

    def get_api_config(self, api_name):
        """Get API config dict, e.g. profile['apis']['musify']."""
        p = self.get_active()
        if p is None:
            return {}
        return p.get('apis', {}).get(api_name, {})

    def get_transcription_config(self):
        p = self.get_active()
        if p is None:
            return {}
        return p.get('analysis', {}).get('transcription', {})

    def get_pitch_config(self):
        p = self.get_active()
        if p is None:
            return {}
        return p.get('analysis', {}).get('pitch', {})

    def get_chord_config(self):
        p = self.get_active()
        if p is None:
            return {}
        return p.get('analysis', {}).get('chords', {})

    def get_filter_config(self):
        p = self.get_active()
        if p is None:
            return {}
        return p.get('filters', {})

    def get_stems_config(self):
        p = self.get_active()
        if p is None:
            return {}
        return p.get('stems', {})


# Singleton
_manager = None
_manager_lock = threading.Lock()

def get_manager(profile_dir=None):
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ProfileManager(profile_dir)
        return _manager

#!/usr/bin/env python3
"""Filter chain for AMP Silver audio tools.

Holds an ordered list of filters and runs them sequentially on each chunk
of PCM audio. Thread-safe. Used by both live monitoring and batch processing.
"""

import threading
from tools.filters.base import BaseFilter


class FilterChain:
    """Ordered chain of audio filters applied sequentially.

    Usage:
        chain = FilterChain()
        chain.add(NoiseCancellationFilter(sample_rate=44100))
        chain.add(VoiceIsolationFilter(sample_rate=44100))
        output = chain.process(pcm_bytes)
    """

    def __init__(self):
        self.filters = []
        self._lock = threading.Lock()

    def add(self, filt: BaseFilter):
        """Add a filter to the end of the chain."""
        with self._lock:
            self.filters.append(filt)

    def remove(self, filt: BaseFilter):
        """Remove a filter from the chain."""
        with self._lock:
            self.filters.remove(filt)

    def clear(self):
        """Remove all filters from the chain."""
        with self._lock:
            self.filters.clear()

    def process(self, pcm_bytes: bytes) -> bytes:
        """Run PCM through all enabled filters in order.

        Disabled filters are skipped. If the chain is empty or all filters
        are disabled, returns the input unchanged.
        """
        with self._lock:
            data = pcm_bytes
            for filt in self.filters:
                if filt.enabled:
                    data = filt.process(data)
            return data

    def reset(self):
        """Reset all filters in the chain."""
        with self._lock:
            for filt in self.filters:
                filt.reset()

    def active_filters(self) -> list:
        """Return list of currently enabled filters."""
        with self._lock:
            return [f for f in self.filters if f.enabled]

    def filter_count(self) -> int:
        """Return total number of filters in the chain."""
        with self._lock:
            return len(self.filters)

    def enabled_count(self) -> int:
        """Return number of enabled filters."""
        with self._lock:
            return sum(1 for f in self.filters if f.enabled)

    def __repr__(self):
        with self._lock:
            names = [f"{f.__class__.__name__}({'on' if f.enabled else 'off'})"
                     for f in self.filters]
        return f"FilterChain([{', '.join(names)}])"

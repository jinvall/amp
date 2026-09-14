#!/usr/bin/env python3
"""Live audio processor for AMP Silver tools.

Wraps a FilterChain for the live monitoring path. Every PCM chunk from
the Android client passes through this before reaching the audio monitor
and visualizer. Whatever filters are active here get baked into saved recordings.

Thread-safe. Runs in the audio worker thread — must be fast.
"""

import threading
from tools.filters.chain import FilterChain


class LiveProcessor:
    """Live monitoring filter processor.

    Sits between the raw PCM receiver and the audio monitor. When active,
    all audio passes through the filter chain before being monitored or saved.

    Usage:
        processor = LiveProcessor(sample_rate=44100)
        processor.add_filter(NoiseCancellationFilter(sample_rate=44100, strength=0.7))
        processor.add_filter(VoiceIsolationFilter(sample_rate=44100))
        processor.set_active(True)

        # In audio worker thread:
        processed = processor.process(pcm_bytes)
    """

    def __init__(self, sample_rate: int = 44100):
        self.chain = FilterChain()
        self.sample_rate = sample_rate
        self.is_active = False
        self._lock = threading.Lock()

    def add_filter(self, filt):
        """Add a filter to the live processing chain."""
        self.chain.add(filt)

    def remove_filter(self, filt):
        """Remove a filter from the live processing chain."""
        self.chain.remove(filt)

    def clear_filters(self):
        """Remove all filters from the live processing chain."""
        self.chain.clear()

    def process(self, pcm_bytes: bytes) -> bytes:
        """Process a chunk of PCM audio through the filter chain.

        If the processor is inactive or the chain is empty, returns the
        input unchanged (zero overhead).
        """
        if not self.is_active or not pcm_bytes:
            return pcm_bytes
        return self.chain.process(pcm_bytes)

    def set_active(self, active: bool):
        """Enable or disable live processing.

        When disabled, process() returns input unchanged. When enabled,
        all audio passes through the filter chain.
        """
        with self._lock:
            self.is_active = bool(active)
            if not self.is_active:
                self.chain.reset()

    def reset(self):
        """Reset all filters in the chain."""
        self.chain.reset()

    def active_filters(self) -> list:
        """Return list of currently enabled filters."""
        return self.chain.active_filters()

    def filter_count(self) -> int:
        """Return total number of filters in the chain."""
        return self.chain.filter_count()

    def __repr__(self):
        return (f"LiveProcessor(sample_rate={self.sample_rate}, "
                f"active={self.is_active}, filters={self.chain.filter_count()})")

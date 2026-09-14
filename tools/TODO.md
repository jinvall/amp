# TODO.md — AMP Silver Audio Tools

## Phase 1: Foundation

### Base Filter Interface
- [ ] Create `tools/filters/base.py` with `BaseFilter` class
- [ ] Implement `process(pcm_bytes) -> bytes` interface
- [ ] Implement `reset()` method
- [ ] Implement `configure(**kwargs)` method
- [ ] Add thread safety with `threading.Lock`

### Filter Chain
- [ ] Create `tools/filters/chain.py` with `FilterChain` class
- [ ] Implement `add(filt)` / `remove(filt)` methods
- [ ] Implement `process(pcm_bytes)` — sequential filter application
- [ ] Implement `reset()` — reset all filters in chain
- [ ] Implement `active_filters()` — list currently active filters

### I/O Layer
- [ ] Create `tools/io/reader.py` — read WAV/FLAC segments
- [ ] Create `tools/io/writer.py` — write processed audio with metadata
- [ ] Handle mono conversion
- [ ] Handle resampling (linear interpolation, no scipy)
- [ ] Support both WAV and FLAC formats

### Package Init
- [ ] Create `tools/__init__.py` with public API exports
- [ ] Create `tools/filters/__init__.py`
- [ ] Create `tools/io/__init__.py`
- [ ] Create `tools/live/__init__.py`

### Unit Tests
- [ ] Test base filter interface
- [ ] Test filter chain ordering
- [ ] Test reader/writer round-trip
- [ ] Test with synthetic audio (sine wave, silence, noise)

## Phase 2: Core Filters

### Noise Cancellation
- [ ] Create `tools/filters/noise_cancellation.py`
- [ ] Implement spectral gating method
- [ ] Implement Wiener filtering method
- [ ] Wrap RNNoise as optional method
- [ ] Add `strength` parameter (0.0–1.0)
- [ ] Add `noise_floor_db` parameter
- [ ] Add `attenuation_db` parameter
- [ ] Add `method` selector parameter
- [ ] Unit test: remove known noise from synthetic signal
- [ ] Unit test: verify voice preservation

### Spectral Difference
- [ ] Create `tools/filters/spectral_difference.py`
- [ ] Implement noise profile estimation from initial frames
- [ ] Implement spectral subtraction
- [ ] Add temporal smoothing to reduce musical noise
- [ ] Add `noise_frames` parameter
- [ ] Add `reduction_amount` parameter (0.0–1.0)
- [ ] Add `smoothing` parameter
- [ ] Unit test: subtract known noise profile
- [ ] Unit test: verify minimal distortion on clean signal

### Voice Removal
- [ ] Create `tools/filters/voice_removal.py`
- [ ] Implement spectral gating for voice band (80 Hz – 4 kHz)
- [ ] Add center-channel inversion for stereo (if applicable)
- [ ] Add `voice_low_hz` / `voice_high_hz` parameters
- [ ] Add `attenuation_db` parameter
- [ ] Add `method` selector
- [ ] Unit test: verify voice band attenuation
- [ ] Unit test: verify non-voice frequencies preserved

### Voice Isolation
- [ ] Create `tools/filters/voice_isolation.py`
- [ ] Implement bandpass isolation for voice frequencies
- [ ] Attenuate non-voice frequencies
- [ ] Add `voice_low_hz` / `voice_high_hz` parameters
- [ ] Add `preserve_db` parameter
- [ ] Add `attenuate_db` parameter
- [ ] Unit test: verify voice band preserved
- [ ] Unit test: verify non-voice attenuated

## Phase 3: Advanced Filters

### Ambient Removal
- [ ] Create `tools/filters/ambient_removal.py`
- [ ] Implement adaptive noise profile learning
- [ ] Remove steady-state ambient noise (HVAC, fan, room tone)
- [ ] Add `adaptation_rate` parameter
- [ ] Add `reduction_db` parameter
- [ ] Add `sensitivity` parameter
- [ ] Unit test: remove steady noise
- [ ] Unit test: preserve transient sounds

### Feature Options
- [ ] Create `tools/filters/feature_options.py`
- [ ] Implement filter parameter registry
- [ ] Define valid ranges, defaults, descriptions for all filter params
- [ ] Implement RMS energy extraction
- [ ] Implement zero-crossing rate extraction
- [ ] Implement spectral centroid extraction
- [ ] Implement spectral rolloff extraction
- [ ] Implement spectral flux extraction
- [ ] Optional: MFCC extraction (if librosa available)
- [ ] Unit test: verify feature values on known signals

## Phase 4: Live Integration

### Live Processor
- [ ] Create `tools/live/live_processor.py`
- [ ] Implement `LiveProcessor` class wrapping `FilterChain`
- [ ] Implement `set_active(bool)` toggle
- [ ] Implement `add_filter` / `remove_filter`
- [ ] Implement `process(pcm_bytes)` with bypass when inactive

### Server Integration
- [ ] Modify `server/audio_receiver.py` to import `LiveProcessor`
- [ ] Attach `LiveProcessor` to `VisualizerFeed`
- [ ] Pass PCM through `LiveProcessor` before audio monitor
- [ ] Ensure saved audio reflects active filters
- [ ] Add config keys for filter states
- [ ] Persist filter config to `config.json`
- [ ] Add control messages for live filter changes

### Config Schema
- [ ] Add `tools_enabled` (bool) to config
- [ ] Add `tools_filters` (list of active filters) to config
- [ ] Add per-filter parameter keys to config
- [ ] Implement config validation and bounds checking
- [ ] Implement hot-reload of filter config

## Phase 5: Web UI

### Tools Panel
- [ ] Create `tools/web/tools_ui.html` (SRP-themed)
- [ ] Add filter enable/disable toggles
- [ ] Add per-filter parameter sliders
- [ ] Add live/batch mode toggle
- [ ] Add file selector for batch mode
- [ ] Add preview button
- [ ] Add save button
- [ ] Add before/after waveform display
- [ ] Add feature readouts panel

### UI Integration
- [ ] Add tools panel to `web/index.html`
- [ ] Wire filter toggles to WebSocket messages
- [ ] Wire parameter sliders to control messages
- [ ] Implement preview functionality
- [ ] Implement save functionality
- [ ] Add visual feedback for active filters
- [ ] Add SRP branding and theming

### Batch Processing
- [ ] Implement file selection from `audio_segments/`
- [ ] Apply filter chain to selected file
- [ ] Preview result in browser
- [ ] Save processed file to `audio_processed/` or `audio_segments/`
- [ ] Include processing metadata in saved file

## Phase 6: Polish

### Performance
- [ ] Profile filter chain CPU usage
- [ ] Optimize FFT operations
- [ ] Add frame dropping for slow filters
- [ ] Verify memory usage stays bounded
- [ ] Test with 5-minute segments

### Edge Cases
- [ ] Handle empty/silent audio
- [ ] Handle very short audio clips
- [ ] Handle filter chain with no filters
- [ ] Handle rapid filter enable/disable
- [ ] Handle config corruption gracefully

### Documentation
- [ ] Write `tools/README.md`
- [ ] Document each filter's parameters
- [ ] Document live vs batch mode
- [ ] Add usage examples
- [ ] Update `STRUCT.md` with tools directory
- [ ] Update `CHANGELOG.md`

### Integration Tests
- [ ] End-to-end: live filter → save → verify
- [ ] End-to-end: batch filter → save → verify
- [ ] Resource test: CPU/RAM under sustained load
- [ ] Latency test: verify filters don't break 300ms budget
- [ ] Stress test: all filters active simultaneously

## Key Principles

- **No silent failures** — log everything (KODE mandate)
- **No placeholders** — every function works
- **No hard-coded config** — everything is configurable
- **Chop-free audio** — smooth transitions, no clicks/pops
- **Resource-conscious** — make it count on the mini PC
- **SRP theme** — consistent branding on all UI elements

## Notes

- All filters use numpy FFT (fast, low memory, no heavy dependencies)
- Filter chain runs in audio worker thread — must be fast
- If a filter is too slow, it drops frames rather than blocking
- Saved audio includes active filters (per user requirement)
- Config is persisted to `config.json` and hot-reloaded

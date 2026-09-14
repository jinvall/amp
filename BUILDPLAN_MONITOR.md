## 9/7/26 NEWEST FILE as of 15:11 

# AMP Silver: Live Audio Monitor & SRP Theme Integration Build Plan

## Objective
Implement a robust, low-latency live audio monitor with GUI toggle and volume control, ensuring no degradation to other system functions, and integrate SRP branding for graph visualizations (adding srp colors as a graph color option)  on the web GUI.

## Current State & Challenges
- **LiveAudioMonitor (server/audio_receiver.py):**
    - Exists, uses `subprocess.Popen` with `ffplay`/`aplay`/`paplay`.
    - Lacks explicit GUI toggle, volume control, or device selection.
    - Error handling is basic (`except Exception: pass`), violating KODE mandate.
    - `feed()` method can be blocking, risking latency budget violation.
    - `_process_audio()` correctly calls monitor feed first.
    - `_monitoring_enabled` flag exists but no UI control.
- **Web GUI (web/index.html):**
    - Has waveform and spectrogram graphs with toggle buttons.
    - Uses inline CSS for basic SRP theme tokens.
    - No specific UI element for audio monitor control.
    - Spectrogram has pre-defined colormaps, but no direct SRP branding for graphs.
- **Latency Budget:** End-to-end (mic -> Silver -> analysis -> WS -> paint) <= 300 ms. Blocking operations in the audio path are critical.

## Phase 1: Core Audio Monitor Backend (server/audio_receiver.py)

### 1.1 Refactor `LiveAudioMonitor` for Robustness and Control
- **Non-blocking `feed()`**:
    - Replace direct `self._proc.stdin.write()` with a bounded `queue.Queue`.
    - Implement a dedicated `_writer_thread` that consumes from this queue and writes to `subprocess.PIPE`.
    - If the queue is full, `feed()` should non-blockingly drop the oldest frame (or the new frame if designed for lowest latency impact). Prioritize dropping new frames to maintain a current view, or oldest to keep historical context, depending on user feedback for visualizer vs. audio monitor. *Decision: Drop oldest frame to ensure fresh audio, within latency constraints.*
- **Volume Control**:
    - Implement `set_volume(level)` method (0-100%).
    - For `aplay`: use `amixer` (or similar) to control playback volume for the specific sound card/device. This will require `subprocess.run` calls.
    - For `ffplay`/`paplay`: investigate if they accept volume parameters on stdin or as command-line args. `ffplay` has `-volume` parameter.
    - Persist volume setting in `config.json`.
- **Device Selection**:
    - Implement `list_audio_devices()` to return available playback devices (e.g., parsing `aplay -l` or `/proc/asound/cards`).
    - Add `set_audio_device(device_id)` to select the output device.
    - Update `_choose_command()` to use the selected device.
    - Persist device selection in `config.json`.
- **Improved Error Handling**:
    - Replace `except Exception: pass` with explicit logging (print to stdout/stderr, which `start_receiver.sh` redirects to `logs/amp.log`).
    - Handle `BrokenPipeError` for `stdin.write()` gracefully by restarting the playback process if needed.
    - Add a `status()` method that returns current state (enabled, device, volume, errors).
- **Graceful Shutdown**: Ensure `_writer_thread` terminates cleanly in `stop()`.

### 1.2 Integrate Monitor Control into `VisualizerFeed`
- **WS Message Handling**:
    - Extend `VisualizerFeed.handler()` to process new WebSocket messages for volume (`type: 'monitor_volume', level: <0-100>`) and device selection (`type: 'monitor_device', device_id: <string>`).
    - Route these to the `audio_monitor` instance.
- **Initial State**: Send current monitor state (enabled, volume, device, available devices) in the `config` hello message to new WebSocket clients.

### 1.3 Add HTTP Endpoints for Fallback/Status
- **`HealthHandler` extensions (server/amp.py)**:
    - `GET /health`: Add monitor status (`monitor_enabled`, `monitor_volume`, `monitor_device`, `monitor_errors`).
    - `POST /monitor/toggle`: Accept `enabled: bool` to toggle the monitor.
    - `POST /monitor/volume`: Accept `level: int` to set volume.
    - `POST /monitor/device`: Accept `device_id: string` to set device.
    - These endpoints provide redundancy and allow non-WebSocket clients to control the monitor.

## Phase 2: Web GUI Integration (web/index.html)

### 2.1 Add Monitor Toggle Button
- **HTML**: Add a `<button id="toggleMonitorBtn">Monitor: On</button>` in the header toolbar, next to `toggleWaveBtn`.
- **CSS**: Apply existing SRP button styles. Use a class like `off` for disabled state.
- **JavaScript**:
    - Get `toggleMonitorBtn` DOM element.
    - Add event listener to `click`:
        - Toggle local `monitorEnabled` state.
        - Send `ws.send(JSON.stringify({ type: 'monitor', enabled: nextState }))`.
        - Update button text and class.
    - In `ws.onmessage` config handler: set initial button state based on `msg.monitoring`.

### 2.2 Add Volume Slider
- **HTML**: Add an `<input type="range">` slider for volume (0-100) next to the monitor button.
    - `<label>Volume <input id="monitorVolume" type="range" min="0" max="100" value="50" /></label>`
- **CSS**: Style with SRP input/range styles.
- **JavaScript**:
    - Get `monitorVolume` DOM element.
    - Add `input` event listener:
        - Send `ws.send(JSON.stringify({ type: 'monitor_volume', level: parseInt(slider.value, 10) }))`.
        - Update volume display.
    - In `ws.onmessage` config handler: set initial slider value based on `msg.monitor_volume`.

### 2.3 Add Device Selector (Optional, for advanced control)
- **HTML**: Add a `<select>` dropdown for audio devices.
    - `<label>Output <select id="monitorDevice"></select></label>`
- **JavaScript**:
    - Populate dropdown options from `msg.monitor_devices` in `ws.onmessage` config.
    - Add `change` event listener: send `ws.send(JSON.stringify({ type: 'monitor_device', device_id: select.value }))`.
    - Set initial selected device from `msg.monitor_device`.

## Phase 3: SRP Theme Color Variant for Graphs

### 3.1 Define SRP Colormap
- **`web/index.html` (JS `buildColormap` function)**:
    - Add a new `name === 'srp'` case to `buildColormap`.
    - Design a gradient using SRP brand colors: `var(--color-bg)`, `var(--color-secondary)`, `var(--color-accent)`, `var(--color-primary)`.
    - Example progression: `srp-bg` (darkest low magnitude) -> `srp-secondary` (mid-low) -> `srp-accent` (mid-high) -> `srp-primary` (brightest high magnitude).
    - Ensure smooth transitions.

### 3.2 Add SRP Option to Colormap Selector
- **HTML (Spectrogram panel `select id="colormap"`)**:
    - Add `<option value="srp">srp</option>`.
- **JavaScript**: The existing event listener will handle selecting the new colormap.

### 3.3 Apply SRP Colors to Waveform and Meters
- **Waveform**:
    - Line color: `var(--color-primary)` (green).
    - Background: `var(--color-surface)`.
    - Grid lines: `var(--color-border)`.
- **Meter Strips (`waveMeter`, `freqMeter`)**:
    - Use `var(--color-primary)` for peak indicators.
    - Use `var(--color-accent)` for RMS indicators.
    - Background and borders should match `var(--color-surface)` and `var(--color-border)`.
- **Refactor CSS/JS**: Instead of hardcoding colors, use the CSS variables defined in `:root` and `srp-theme-tokens.css` where possible. The inline styles in `web/index.html` are *already* using `var(--color-...)` which is good. Ensure graph drawing uses these variables.

## Phase 4: Testing & Verification

### 4.1 Unit/Integration Tests
- Server-side:
    - Test `LiveAudioMonitor` queueing and non-blocking behavior.
    - Test volume control (`amixer` calls, if applicable).
    - Test device selection.
    - Test error handling (e.g., `ffplay` process dies).
    - Test HTTP/WS endpoints for monitor control.
- Client-side:
    - Verify GUI toggle button functionality.
    - Verify volume slider interaction.
    - Verify device selector updates and sends correct messages.
    - Verify graph SRP colormap rendering.

### 4.2 End-to-End Testing
- Full system startup: `start_receiver.sh`
- Android client streaming audio.
- Web UI connection to WebSocket.
- Toggle monitor on/off, change volume, verify audio output on Silver.
- Observe latency meter to ensure budget is met.
- Stress test with high audio load to confirm no degradation.
- Verify SRP theme is applied correctly to graphs.

## VS Code Agent Build Plan Considerations
- **Workspace Structure**: Keep `~/amp` as the root. `server/` for backend, `web/` for frontend.
- **Tasks**:
    - `Python: Current File` for running `server/amp.py`
    - `npm start` (if a frontend build step is introduced, currently it's just HTML/JS)
    - Debug configurations for Python backend.
- **Extensions**: Python, Pylance, Prettier, ESLint (if TS/React are introduced).
- **Git**: Ensure proper `.gitignore` (especially for `venv` and build artifacts).

## Latency Budget Adherence
- **Critical Path**: `AudioReceiver._handle_client()` -> `_process_audio()` -> `monitor.feed()` -> `viz_feed.feed_pcm()`.
- **`LiveAudioMonitor`**: The queue-based approach with a separate writer thread ensures `monitor.feed()` is non-blocking, directly addressing the latency requirement. Dropping old frames from the queue if full prevents buildup.
- **`VisualizerFeed`**: Already uses a queue (`_enqueue`) for WebSocket frames, ensuring `feed_pcm()` is non-blocking.

## KODE.md & STRUCT.md Update
- Document new `LiveAudioMonitor` methods (volume, device).
- Update `STRUCT.md` with new HTTP endpoints and WebSocket messages.
- Document the decision process for non-blocking monitor feed.

---

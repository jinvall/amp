/* AMP Editor — tabbed file editor logic */

(function() {
  'use strict';

  const TAB_FILTERS = 'filters';
  const TAB_TRANSCRIBE = 'transcribe';
  const TAB_STEMS = 'stems';
  const TAB_MIDI = 'midi';
  const TAB_PROFILE = 'profile';

  let currentTab = TAB_FILTERS;
  let loadedFile = null;
  let audioEl = null;

  // ── DOM refs ─────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  // ── Tab switching ────────────────────────────────────────────────────
  function switchTab(name) {
    document.querySelectorAll('.editor-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === name);
    });
    document.querySelectorAll('.editor-tab-content').forEach(c => {
      c.classList.toggle('active', c.id === 'tab-' + name);
    });
    currentTab = name;
  }

  // ── File loading ─────────────────────────────────────────────────────
  function loadFile(file) {
    if (!file) return;
    loadedFile = file;
    $('editorFileName').textContent = file.name;
    const sizeMB = (file.size / 1024 / 1024).toFixed(2);
    $('editorFileMeta').textContent = `${sizeMB} MB`;

    // Create audio element for playback preview
    if (audioEl) { audioEl.pause(); }
    audioEl = new Audio();
    audioEl.src = URL.createObjectURL(file);
    audioEl.addEventListener('loadedmetadata', () => {
      const dur = audioEl.duration;
      const mins = Math.floor(dur / 60);
      const secs = Math.floor(dur % 60);
      $('editorTime').textContent = `0:00 / ${mins}:${secs.toString().padStart(2, '0')}`;
    });
    audioEl.addEventListener('timeupdate', () => {
      const cur = audioEl.currentTime;
      const dur = audioEl.duration || 0;
      const cMin = Math.floor(cur / 60);
      const cSec = Math.floor(cur % 60);
      const dMin = Math.floor(dur / 60);
      const dSec = Math.floor(dur % 60);
      $('editorTime').textContent = `${cMin}:${cSec.toString().padStart(2, '0')} / ${dMin}:${dSec.toString().padStart(2, '0')}`;
    });
  }

  // ── Transport controls ──────────────────────────────────────────────
  function playAudio() {
    if (audioEl) audioEl.play();
  }
  function pauseAudio() {
    if (audioEl) audioEl.pause();
  }
  function stopAudio() {
    if (audioEl) { audioEl.pause(); audioEl.currentTime = 0; }
  }

  // ── Filters ──────────────────────────────────────────────────────────
  function getSelectedFilters() {
    const selected = [];
    document.querySelectorAll('.filter-item').forEach(item => {
      const cb = item.querySelector('input[type="checkbox"]');
      if (cb && cb.checked) {
        selected.push(item.dataset.filter);
      }
    });
    return selected;
  }

  async function applyFilters() {
    if (!loadedFile) { alert('Load a file first'); return; }
    const filters = getSelectedFilters();
    if (filters.length === 0) { alert('Select at least one filter'); return; }

    $('editorFilterStatus').textContent = 'Processing...';
    $('editorFilterStatus').className = 'editor-status';

    try {
      const base = window.location.origin;
      const form = new FormData();
      form.append('file', loadedFile);
      form.append('filters', JSON.stringify(filters));

      const res = await fetch(base + '/editor/filters', {
        method: 'POST',
        body: form,
      });
      const data = await res.json();
      if (data.error) {
        $('editorFilterStatus').textContent = data.error;
        $('editorFilterStatus').className = 'editor-status error';
      } else {
        $('editorFilterStatus').textContent = `Saved: ${data.output_path} (${data.duration}s)`;
        $('editorFilterStatus').className = 'editor-status ok';
      }
    } catch (e) {
      $('editorFilterStatus').textContent = e.message;
      $('editorFilterStatus').className = 'editor-status error';
    }
  }

  // ── Transcription ────────────────────────────────────────────────────
  async function transcribeFile() {
    if (!loadedFile) { alert('Load a file first'); return; }
    const model = $('transcribeModel').value;
    const lang = $('transcribeLang').value;

    $('editorTranscribeStatus').textContent = 'Transcribing (may take a while)...';
    $('editorTranscribeStatus').className = 'editor-status';
    $('transcribeResult').innerHTML = '<div class="editor-result-empty">Processing...</div>';

    try {
      const base = window.location.origin;
      const form = new FormData();
      form.append('file', loadedFile);
      form.append('model', model);
      form.append('language', lang);

      const res = await fetch(base + '/editor/transcribe', {
        method: 'POST',
        body: form,
      });
      const data = await res.json();

      if (data.error) {
        $('editorTranscribeStatus').textContent = data.error;
        $('editorTranscribeStatus').className = 'editor-status error';
        $('transcribeResult').innerHTML = `<div class="editor-result-empty">Error: ${data.error}</div>`;
        return;
      }

      renderTranscript(data);
      $('editorTranscribeStatus').textContent = `Done (${data.backend} / ${data.language})`;
      $('editorTranscribeStatus').className = 'editor-status ok';
    } catch (e) {
      $('editorTranscribeStatus').textContent = e.message;
      $('editorTranscribeStatus').className = 'editor-status error';
    }
  }

  function renderTranscript(data) {
    const container = $('transcribeResult');
    if (!data.segments || data.segments.length === 0) {
      container.innerHTML = `<div class="editor-result-empty">${data.text || 'No text detected'}</div>`;
      return;
    }
    let html = '';
    for (const seg of data.segments) {
      const startMin = Math.floor(seg.start / 60);
      const startSec = (seg.start % 60).toFixed(1);
      html += `<div class="transcript-segment">
        <span class="transcript-time">${startMin}:${startSec.toString().padStart(4, '0')}</span>
        <span class="transcript-text">${seg.text}</span>
        <span class="transcript-confidence">{(seg.confidence * 100).toFixed(0)}%</span>
      </div>`;
    }
    container.innerHTML = html;
  }

  // ── Stems ────────────────────────────────────────────────────────────
  async function extractStems() {
    if (!loadedFile) { alert('Load a file first'); return; }
    const backend = $('stemBackend').value;
    $('editorStemsStatus').textContent = 'Extracting (slow on CPU)...';
    $('editorStemsStatus').className = 'editor-status';
    $('stemsResult').innerHTML = '<div class="editor-result-empty">Processing...</div>';

    try {
      const base = window.location.origin;
      const form = new FormData();
      form.append('file', loadedFile);
      form.append('backend', backend);

      const res = await fetch(base + '/editor/stems', {
        method: 'POST',
        body: form,
      });
      const data = await res.json();

      if (data.error) {
        $('editorStemsStatus').textContent = data.error;
        $('editorStemsStatus').className = 'editor-status error';
        $('stemsResult').innerHTML = `<div class="editor-result-empty">Error: ${data.error}</div>`;
        return;
      }

      renderStems(data);
      $('editorStemsStatus').textContent = `Done: ${Object.keys(data.stems).length} stems`;
      $('editorStemsStatus').className = 'editor-status ok';
    } catch (e) {
      $('editorStemsStatus').textContent = e.message;
      $('editorStemsStatus').className = 'editor-status error';
    }
  }

  function renderStems(data) {
    const container = $('stemsResult');
    if (!data.stems) { container.innerHTML = '<div class="editor-result-empty">No stems</div>'; return; }
    let html = '';
    const stemOrder = ['vocals', 'bass', 'drums', 'guitar', 'other', 'noise'];
    for (const name of stemOrder) {
      const fname = data.stems[name];
      if (!fname) continue;
      html += `<div class="stem-item">
        <span class="stem-name ${name}">${name}</span>
        <button class="stem-play-btn" data-stem="${fname}">▶ Play</button>
        <a class="stem-download-btn" href="/audio_stems/${fname}" download>⬇</a>
      </div>`;
    }
    container.innerHTML = html || '<div class="editor-result-empty">No stems produced</div>';

    // Wire play buttons
    container.querySelectorAll('.stem-play-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const stemPath = btn.dataset.stem;
        if (audioEl) { audioEl.pause(); }
        audioEl = new Audio('/audio_stems/' + stemPath);
        audioEl.play();
      });
    });
  }

  // ── MIDI ─────────────────────────────────────────────────────────────
  async function enableMidi() {
    const port = $('midiPort').value;
    const channel = parseInt($('midiChannel').value);
    const source = $('midiSource').value;

    $('editorMidiStatus').textContent = 'Enabling...';
    $('editorMidiStatus').className = 'editor-status';

    try {
      const base = window.location.origin;
      const res = await fetch(base + '/editor/midi', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ port, channel, source }),
      });
      const data = await res.json();
      $('editorMidiStatus').textContent = data.status || data.error || 'MIDI enabled';
      $('editorMidiStatus').className = data.error ? 'editor-status error' : 'editor-status ok';
    } catch (e) {
      $('editorMidiStatus').textContent = e.message;
      $('editorMidiStatus').className = 'editor-status error';
    }
  }

  async function testMidiNote() {
    try {
      const base = window.location.origin;
      await fetch(base + '/editor/midi/test', { method: 'POST' });
      $('editorMidiStatus').textContent = 'Sent test note (C4)';
      $('editorMidiStatus').className = 'editor-status ok';
    } catch (e) {
      $('editorMidiStatus').textContent = e.message;
      $('editorMidiStatus').className = 'editor-status error';
    }
  }

  // ── Profile ──────────────────────────────────────────────────────────
  async function loadProfiles() {
    try {
      const base = window.location.origin;
      const res = await fetch(base + '/editor/profiles');
      const data = await res.json();

      const sel = $('profileSelect');
      sel.innerHTML = '';
      for (const p of data.profiles) {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name + (p.active ? ' (active)' : '');
        if (p.active) opt.selected = true;
        sel.appendChild(opt);
      }
      if (data.profiles.length > 0) {
        $('profileDescription').textContent = data.profiles.find(p => p.active)?.description || '';
        $('profileJson').textContent = JSON.stringify(data.profiles.find(p => p.active) || {}, null, 2);
      }
      $('profileStatus').textContent = `${data.profiles.length} profiles loaded`;
      $('profileStatus').className = 'editor-status ok';
    } catch (e) {
      $('profileStatus').textContent = e.message;
      $('profileStatus').className = 'editor-status error';
    }
  }

  async function activateProfile() {
    const name = $('profileSelect').value;
    try {
      const base = window.location.origin;
      const res = await fetch(base + '/editor/profiles/activate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      $('profileStatus').textContent = data.status || data.error;
      $('profileStatus').className = data.error ? 'editor-status error' : 'editor-status ok';
      loadProfiles();
    } catch (e) {
      $('profileStatus').textContent = e.message;
      $('profileStatus').className = 'editor-status error';
    }
  }

  // ── Help overlay ─────────────────────────────────────────────────────
  function toggleHelp() {
    const overlay = $('editorHelpOverlay');
    overlay.style.display = overlay.style.display === 'none' ? 'flex' : 'none';
  }

  // ── Keyboard shortcuts ──────────────────────────────────────────────
  function handleKeyboard(e) {
    if (e.ctrlKey && e.key === 'o') { e.preventDefault(); $('editorLoadBtn')?.click(); }
    if (e.ctrlKey && e.key === 's') { e.preventDefault(); $('editorSaveBtn')?.click(); }
    if (e.ctrlKey && e.key >= '1' && e.key <= '5') {
      e.preventDefault();
      const tabs = [TAB_FILTERS, TAB_TRANSCRIBE, TAB_STEMS, TAB_MIDI, TAB_PROFILE];
      switchTab(tabs[parseInt(e.key) - 1]);
    }
    if (e.key === '?') { toggleHelp(); }
    if (e.key === ' ' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (audioEl) audioEl.paused ? playAudio() : pauseAudio();
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────
  function init() {
    // Tab bar clicks
    document.querySelectorAll('.editor-tab').forEach(tab => {
      tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    // Header buttons
    $('editorLoadBtn').addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.wav,.flac,audio/*';
      input.addEventListener('change', () => loadFile(input.files[0]));
      input.click();
    });
    $('editorSaveBtn').addEventListener('click', () => {
      if (loadedFile) alert('Processed files are saved to audio_segments/processed/');
      else alert('No file loaded');
    });
    $('editorHelpBtn').addEventListener('click', toggleHelp);

    // Transport
    $('editorPlayBtn').addEventListener('click', playAudio);
    $('editorPauseBtn').addEventListener('click', pauseAudio);
    $('editorStopBtn').addEventListener('click', stopAudio);

    // Tab-specific buttons
    $('editorApplyFiltersBtn').addEventListener('click', applyFilters);
    $('editorPreviewFilterBtn').addEventListener('click', () => alert('Preview: chain: ' + getSelectedFilters().join(', ')));
    $('editorTranscribeBtn').addEventListener('click', transcribeFile);
    $('editorExtractStemsBtn').addEventListener('click', extractStems);
    $('editorMidiEnableBtn').addEventListener('click', enableMidi);
    $('editorMidiTestBtn').addEventListener('click', testMidiNote);

    // Profile
    $('profileReloadBtn').addEventListener('click', loadProfiles);
    $('profileActivateBtn').addEventListener('click', activateProfile);

    // Help overlay
    $('editorHelpClose').addEventListener('click', toggleHelp);

    // Keyboard
    document.addEventListener('keydown', handleKeyboard);

    // Drag and drop on the whole editor panel
    const panel = $('editorPanel');
    if (panel) {
      panel.addEventListener('dragover', e => { e.preventDefault(); panel.style.borderColor = 'var(--color-accent)'; });
      panel.addEventListener('dragleave', () => { panel.style.borderColor = ''; });
      panel.addEventListener('drop', e => {
        e.preventDefault();
        panel.style.borderColor = '';
        if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]);
      });
    }

    // Load profiles on init
    loadProfiles();
  }

  // Prevent double-init
  if (window.__editorInitialized) return;
  window.__editorInitialized = true;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import audio_receiver


def test_monitoring_toggle_disables_feed_and_reports_state():
    feed = audio_receiver.VisualizerFeed(
        host='127.0.0.1',
        port=0,
        analyzer=audio_receiver.VisualizerAnalyzer(sample_rate=44100),
    )

    assert feed.graph_states()['monitoring'] is True
    feed.set_monitoring_enabled(False)
    assert feed.graph_states()['monitoring'] is False

    feed.feed_pcm(b'\x00' * 4096)
    assert len(feed._pcm_ring) == 0


def test_live_monitor_outputs_pcm_when_enabled():
    feed = audio_receiver.VisualizerFeed(
        host='127.0.0.1',
        port=0,
        analyzer=audio_receiver.VisualizerAnalyzer(sample_rate=44100),
    )

    assert feed.audio_monitor.is_enabled is True
    feed.set_monitoring_enabled(False)
    assert feed.audio_monitor.is_enabled is False
    feed.audio_monitor.feed(b'\x00\x00\x00\x00')
    assert feed.audio_monitor.total_written == 0

    feed.set_monitoring_enabled(True)
    assert feed.audio_monitor.is_enabled is True
    feed.audio_monitor.feed(b'\x00\x00\x00\x00')
    if feed.audio_monitor._cmd is not None:
        assert feed.audio_monitor.total_written >= 4
    feed.audio_monitor.stop()


def test_receiver_touches_monitor_before_visualizer_processing():
    receiver = audio_receiver.AudioReceiver(host='127.0.0.1', port=0, control_port=0)

    class FakeAudioMonitor:
        def __init__(self):
            self.events = []

        def feed(self, pcm_bytes):
            self.events.append(('monitor', len(pcm_bytes)))

    class FakeVisualizer:
        def __init__(self):
            self.audio_monitor = FakeAudioMonitor()
            self.events = []

        def feed_pcm(self, pcm_bytes):
            self.events.append(('feed_pcm', len(pcm_bytes)))

    fake_feed = FakeVisualizer()
    receiver.viz_feed = fake_feed

    receiver._process_audio(b'\x00\x00\x00\x00')

    assert fake_feed.audio_monitor.events == [('monitor', 4)]
    assert fake_feed.events == [('feed_pcm', 4)]


def test_live_monitor_retries_partial_writes_to_avoid_chop():
    monitor = audio_receiver.LiveAudioMonitor(sample_rate=44100)

    class FakeStdin:
        def __init__(self):
            self.chunks = []

        def write(self, data):
            chunk = bytes(data[:3])
            self.chunks.append(chunk)
            return len(chunk)

        def flush(self):
            pass

        def close(self):
            pass

    class FakeProc:
        def __init__(self):
            self.stdin = FakeStdin()

    monitor._cmd = ['fake-monitor']
    monitor._proc = FakeProc()
    monitor.is_enabled = True

    monitor.feed(b'\x00\x01\x02\x03\x04\x05')

    assert monitor.total_written == 6
    assert sum(len(chunk) for chunk in monitor._proc.stdin.chunks) == 6


def test_live_monitor_prefers_default_alsa_device_for_deploys():
    monitor = audio_receiver.LiveAudioMonitor(sample_rate=44100)
    cmd = monitor._choose_command()
    assert cmd is not None
    assert 'default' in cmd or 'ffplay' in cmd
    assert 'hw:0,0' not in cmd


def test_live_monitor_prefers_low_latency_streaming_when_ffplay_exists():
    monitor = audio_receiver.LiveAudioMonitor(sample_rate=44100)
    cmd = monitor._choose_command()
    if cmd and cmd[0] == 'ffplay':
        joined = ' '.join(cmd)
        assert 'nobuffer' in joined or 'low_delay' in joined


def test_receiver_preserves_pcm_that_arrives_after_config_newline():
    receiver = audio_receiver.AudioReceiver(host='127.0.0.1', port=0, control_port=0)

    class FakeSocket:
        def __init__(self):
            self.payload = b'{"amplification": 2}\n\x00\x01\x02\x03'
            self.index = 0

        def recv(self, n):
            if self.index >= len(self.payload):
                return b''
            data = self.payload[self.index:self.index + n]
            self.index += len(data)
            return data

    config, leftover = receiver._read_config(FakeSocket())

    assert config == {"amplification": 2}
    assert leftover == b'\x00\x01\x02\x03'


def test_main_ui_has_monitor_toggle_control():
    index_path = os.path.join(ROOT, 'web', 'index.html')
    with open(index_path, 'r', encoding='utf-8') as fh:
        html = fh.read()

    assert 'toggleMonitoring' in html
    assert "type: 'monitor'" in html or '"type": "monitor"' in html
    assert 'Monitoring:' in html

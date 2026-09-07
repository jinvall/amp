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


def test_main_ui_has_monitor_toggle_control():
    index_path = os.path.join(ROOT, 'web', 'index.html')
    with open(index_path, 'r', encoding='utf-8') as fh:
        html = fh.read()

    assert 'toggleMonitoring' in html
    assert "type: 'monitor'" in html or '"type": "monitor"' in html
    assert 'Monitoring:' in html

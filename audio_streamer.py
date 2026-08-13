#!/usr/bin/env python3
import socket
import time
import threading
import sys
import os
import select

def _load_local_config():
    """Load local config from local-config.properties if present."""
    config_path = os.path.join(os.path.dirname(__file__), 'local-config.properties')
    defaults = {}
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    defaults[key.strip()] = value.strip()
    return defaults


def create_audio_streamer():
    """Create a simple audio streamer that can run on Android"""
    local_config = _load_local_config()
    
    class AudioStreamer:
        def __init__(self):
            self.is_streaming = False
            self.amplification = 1.0
            self.socket = None
            
        def connect_to_server(self, server_ip, server_port):
            """Connect to the audio server"""
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.connect((server_ip, server_port))
                print(f"Connected to {server_ip}:{server_port}")
                return True
            except Exception as e:
                print(f"Connection failed: {e}")
                return False
                
        def start_streaming(self, server_ip=None, server_port=8080, amplification=1.0):
            """Start streaming audio"""
            if server_ip is None:
                server_ip = local_config.get('server.ip', '127.0.0.1')
            if str(server_port) == '8080' and 'server.port' in local_config:
                server_port = int(local_config['server.port'])
            if self.is_streaming:
                print("Already streaming")
                return False
                
            if not self.connect_to_server(server_ip, server_port):
                return False
                
            self.is_streaming = True
            self.amplification = amplification
            
            # Start streaming in background thread
            self.stream_thread = threading.Thread(target=self._stream_loop)
            self.stream_thread.daemon = True
            self.stream_thread.start()
            
            print(f"Started streaming to {server_ip}:{server_port} with {int(amplification*100)}% amplification")
            return True
            
        def stop_streaming(self):
            """Stop streaming"""
            self.is_streaming = False
            if self.socket:
                self.socket.close()
                self.socket = None
            print("Stopped streaming")
            
        def _stream_loop(self):
            """Main streaming loop"""
            bytes_sent = 0
            
            try:
                while self.is_streaming and self.socket:
                    # Generate simulated audio data (replace with real audio capture)
                    # This simulates 44.1kHz, 16-bit mono audio
                    audio_data = self._generate_audio_chunk()
                    
                    # Apply amplification
                    amplified_data = self._amplify_audio(audio_data)
                    
                    # Send to server
                    try:
                        self.socket.send(amplified_data)
                        bytes_sent += len(amplified_data)
                        print(f"Sent {bytes_sent} bytes", end='\r')
                    except:
                        break
                        
                    # Simulate real-time streaming (44.1kHz = ~90ms per 4KB chunk)
                    time.sleep(0.09)
                    
            except Exception as e:
                print(f"\nStreaming error: {e}")
            
            self.stop_streaming()
            
        def _generate_audio_chunk(self):
            """Generate a chunk of simulated audio data"""
            # Generate 4KB of audio data (simulating 44.1kHz, 16-bit mono)
            # This would be replaced with actual microphone capture
            chunk_size = 4096
            audio_chunk = bytearray()
            
            # Generate a simple sine wave
            for i in range(0, chunk_size, 2):
                # Simple sine wave at 440Hz (A4)
                sample = int(32767 * 0.3 * (i / chunk_size))  # Simple ramp
                
                # Convert to 16-bit little-endian
                audio_chunk.append(sample & 0xFF)
                audio_chunk.append((sample >> 8) & 0xFF)
                
            return bytes(audio_chunk)
            
        def _amplify_audio(self, audio_data):
            """Apply amplification to audio data"""
            if self.amplification <= 1.0:
                return audio_data
                
            amplified = bytearray()
            
            # Process as 16-bit samples
            for i in range(0, len(audio_data) - 1, 2):
                # Convert two bytes to 16-bit sample
                sample = (audio_data[i + 1] << 8) | audio_data[i]
                if sample >= 32768:  # Handle negative values
                    sample = sample - 65536
                
                # Apply amplification
                amplified_sample = int(sample * self.amplification)
                
                # Clamp to 16-bit range
                if amplified_sample > 32767:
                    amplified_sample = 32767
                elif amplified_sample < -32768:
                    amplified_sample = -32768
                
                # Convert back to bytes
                if amplified_sample < 0:
                    amplified_sample += 65536
                
                amplified.append(amplified_sample & 0xFF)
                amplified.append((amplified_sample >> 8) & 0xFF)
                
            return bytes(amplified)
    
    return AudioStreamer()

def main():
    """Command-line interface"""
    import argparse
    
    local_config = _load_local_config()
    
    parser = argparse.ArgumentParser(description='Audio Streamer for Android')
    parser.add_argument('--server', default=local_config.get('server.ip', '127.0.0.1'), help='Server IP address')
    parser.add_argument('--port', type=int, default=int(local_config.get('server.port', 8080)), help='Server port')
    parser.add_argument('--amplification', type=float, default=1.5, help='Amplification factor (1.0 = 100 percent)')
    parser.add_argument('--duration', type=int, default=0, help='Stream duration in seconds (0 = infinite)')
    
    args = parser.parse_args()
    
    print("Audio Amplifier Streamer")
    print("=" * 40)
    print(f"Server: {args.server}:{args.port}")
    print(f"Amplification: {int(args.amplification * 100)}%")
    print("=" * 40)
    
    streamer = create_audio_streamer()
    
    if streamer.start_streaming(args.server, args.port, args.amplification):
        try:
            if args.duration > 0:
                print(f"Streaming for {args.duration} seconds...")
                time.sleep(args.duration)
                streamer.stop_streaming()
            else:
                print("Streaming indefinitely. Press Ctrl+C to stop.")
                while streamer.is_streaming:
                    time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
            streamer.stop_streaming()
    
    print("Streamer stopped")

if __name__ == '__main__':
    main()

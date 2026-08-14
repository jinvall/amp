#!/usr/bin/env python3
import socket
import threading
import time
import sys
import argparse
import os

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

def amplify_audio(data, amplification_factor):
    """Simple audio amplification"""
    if amplification_factor <= 1.0:
        return data
    
    amplified = bytearray()
    for byte in data:
        amplified_byte = min(255, int(byte * amplification_factor))
        amplified.append(amplified_byte)
    
    return bytes(amplified)

def stream_audio(server_ip, server_port, amplification):
    """Stream audio to server"""
    try:
        print(f"Connecting to {server_ip}:{server_port} with {int(amplification*100)}% amplification")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((server_ip, server_port))
        
        print("Connected! Streaming audio... Press Ctrl+C to stop")
        
        # Simulate audio streaming
        bytes_sent = 0
        try:
            while True:
                # Generate fake audio data (replace with real audio capture)
                fake_audio = b'\x00' * 1024
                
                # Amplify and send
                amplified_audio = amplify_audio(fake_audio, amplification)
                sock.send(amplified_audio)
                bytes_sent += len(amplified_audio)
                
                print(f"Sent {bytes_sent} bytes", end='\r')
                time.sleep(0.01)
                
        except KeyboardInterrupt:
            print("\nStopping stream...")
        
        sock.close()
        print("Stream stopped")
        
    except Exception as e:
        print(f"Error: {e}")

def main():
    local_config = _load_local_config()
    parser = argparse.ArgumentParser(description='Audio Streamer')
    parser.add_argument('--server', default=local_config.get('server.ip', '127.0.0.1'), help='Server IP address')
    parser.add_argument('--port', type=int, default=int(local_config.get('server.port', 8080)), help='Server port')
    parser.add_argument('--amplification', type=float, default=1.0, help='Amplification factor (1.0 = 100%)')
    
    args = parser.parse_args()
    
    print("Audio Amplifier Streamer")
    print("=" * 30)
    print(f"Server: {args.server}:{args.port}")
    print(f"Amplification: {int(args.amplification * 100)}%")
    print("=" * 30)
    
    stream_audio(args.server, args.port, args.amplification)

if __name__ == '__main__':
    main()

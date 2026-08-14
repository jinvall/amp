#!/system/bin/sh
# Audio Amplifier Streamer for Android
# Usage: ./start_stream.sh [amplification_percent]

# Load local config if present
CONFIG_FILE="local-config.properties"
if [ -f "$CONFIG_FILE" ]; then
    SERVER=$(grep '^server\.ip=' "$CONFIG_FILE" | cut -d'=' -f2)
    PORT=$(grep '^server\.port=' "$CONFIG_FILE" | cut -d'=' -f2)
fi
SERVER="${SERVER:-127.0.0.1}"
PORT="${PORT:-8080}"
AMPLIFICATION="${1:-150}"  # Default to 150% amplification

echo "Starting Audio Amplifier Streamer"
echo "Server: $SERVER:$PORT"
echo "Amplification: ${AMPLIFICATION}%"
echo ""

# Check if Python is available
if command -v python3 >/dev/null 2>&1; then
    python3 audio_streamer.py --server "$SERVER" --port "$PORT" --amplification "$(echo "$AMPLIFICATION/100" | bc -l)"
elif command -v python >/dev/null 2>&1; then
    python audio_streamer.py --server "$SERVER" --port "$PORT" --amplification "$(echo "$AMPLIFICATION/100" | bc -l)"
else
    echo "Error: Python not found. Please install Python to run the audio streamer."
    echo "You can install Python via Termux or similar Android terminal apps."
fi

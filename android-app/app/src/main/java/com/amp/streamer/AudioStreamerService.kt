package com.amp.streamer

import android.app.*
import android.content.Intent
import android.media.*
import android.os.Binder
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt
import kotlin.math.log10
import org.json.JSONObject
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

class AudioStreamerService : Service() {

    companion object {
        const val EXTRA_SERVER_IP = "server_ip"
        const val EXTRA_SERVER_PORT = "server_port"
        const val EXTRA_AMPLIFICATION = "amplification"
        const val EXTRA_BREATHING_SENSITIVITY = "breathing_sensitivity"
        const val EXTRA_BREATHING_COOLDOWN = "breathing_cooldown"
        const val EXTRA_SEGMENT_DURATION = "segment_duration"
        const val EXTRA_NOISE_GATE = "noise_gate"
        const val NOTIFICATION_ID = 1
        const val CHANNEL_ID = "audio_streamer_channel"
    }

    inner class StreamerBinder : Binder() {
        fun getService(): AudioStreamerService = this@AudioStreamerService
    }

    private val binder = StreamerBinder()

    private var isStreaming = false
    val isCurrentlyStreaming: Boolean
        get() = isStreaming
    var bytesSent: Long = 0
        private set

    private var serverIp: String = ""
    private var serverPort: Int = 8080
    private var amplification: Float = 1.0f
    private var breathingSensitivity: Double = 100.0
    private var breathingCooldown: Double = 1.0
    private var segmentDurationSec: Int = 5
    private var noiseGate: Int = 0

    private var audioRecord: AudioRecord? = null
    private var streamThread: Thread? = null
    private var socket: java.net.Socket? = null
    private var connectThread: Thread? = null

    @Volatile
    var lastRmsIn: Double = 0.0
        private set
    @Volatile
    var lastRmsOut: Double = 0.0
        private set
    @Volatile
    var lastRmsDb: Double = -60.0
        private set

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        intent?.let {
            serverIp = it.getStringExtra(EXTRA_SERVER_IP) ?: serverIp
            serverPort = it.getIntExtra(EXTRA_SERVER_PORT, serverPort)
            amplification = it.getFloatExtra(EXTRA_AMPLIFICATION, amplification)
            breathingSensitivity = it.getDoubleExtra(EXTRA_BREATHING_SENSITIVITY, breathingSensitivity)
            breathingCooldown = it.getDoubleExtra(EXTRA_BREATHING_COOLDOWN, breathingCooldown)
            segmentDurationSec = it.getIntExtra(EXTRA_SEGMENT_DURATION, segmentDurationSec)
            noiseGate = it.getIntExtra(EXTRA_NOISE_GATE, noiseGate)
        }
        startForeground(NOTIFICATION_ID, buildNotification())
        startStreaming()
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        stopStreaming()
    }

    private fun buildNotification(): Notification {
        val stopIntent = Intent(this, AudioStreamerService::class.java).apply {
            action = "STOP"
        }
        val stopPendingIntent = PendingIntent.getService(
            this, 0, stopIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(getString(R.string.notification_text))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .addAction(android.R.drawable.ic_media_pause, "Stop", stopPendingIntent)
            .setOngoing(true)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_title),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = getString(R.string.notification_text)
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    fun startStreaming() {
        if (isStreaming) return

        val sampleRate = 44100
        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val audioFormat = AudioFormat.ENCODING_PCM_16BIT

        val minBufSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
        val audioRecordBufferSize = max(minBufSize, 65536)

        // Try multiple audio sources to find one that works
        val sources = arrayOf(
            MediaRecorder.AudioSource.MIC,
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            MediaRecorder.AudioSource.CAMCORDER,
            MediaRecorder.AudioSource.DEFAULT,
            MediaRecorder.AudioSource.UNPROCESSED
        )

        audioRecord = null
        for (source in sources) {
            try {
                android.util.Log.d("AudioStreamer", "Trying audio source: $source")
                val testRecord = AudioRecord(source, sampleRate, channelConfig, audioFormat, audioRecordBufferSize)
                if (testRecord.state == AudioRecord.STATE_INITIALIZED) {
                    android.util.Log.d("AudioStreamer", "Audio source $source initialized successfully")
                    testRecord.release()
                    audioRecord = AudioRecord(source, sampleRate, channelConfig, audioFormat, audioRecordBufferSize)
                    break
                } else {
                    android.util.Log.w("AudioStreamer", "Audio source $source failed to initialize")
                    testRecord.release()
                }
            } catch (e: Exception) {
                android.util.Log.e("AudioStreamer", "Audio source $source error", e)
            }
        }

        if (audioRecord == null || audioRecord!!.state != AudioRecord.STATE_INITIALIZED) {
            android.util.Log.e("AudioStreamer", "No audio source available")
            stopSelf()
            return
        }

        connectThread = Thread {
            try {
                android.util.Log.d("AudioStreamer", "Connecting to $serverIp:$serverPort...")
                val newSocket = java.net.Socket()
                newSocket.connect(java.net.InetSocketAddress(serverIp, serverPort), 5000)
                newSocket.tcpNoDelay = true
                newSocket.sendBufferSize = 131072
                socket = newSocket
                android.util.Log.d("AudioStreamer", "Connected to $serverIp:$serverPort")
                sendConfig(newSocket)
                startRecordingLoop(audioRecordBufferSize)
            } catch (e: Exception) {
                android.util.Log.e("AudioStreamer", "Connection failed", e)
                e.printStackTrace()
                stopSelf()
            }
        }.also { it.start() }
    }

    private fun sendConfig(socket: java.net.Socket) {
        try {
            val config = JSONObject()
            config.put("amplification", amplification)
            config.put("breathing_sensitivity", breathingSensitivity)
            config.put("breathing_cooldown", breathingCooldown)
            config.put("segment_duration_min", segmentDurationSec)
            val line = config.toString() + "\n"
            val out = socket.getOutputStream()
            out.write(line.toByteArray(Charsets.UTF_8))
            out.flush()
            android.util.Log.d("AudioStreamer", "Sent config: $line")
        } catch (e: Exception) {
            android.util.Log.e("AudioStreamer", "Failed to send config", e)
        }
    }

    private fun startRecordingLoop(bufferSize: Int) {
        isStreaming = true
        bytesSent = 0
        audioRecord?.startRecording()
        android.util.Log.d("AudioStreamer", "Recording started")

        val queue = ArrayBlockingQueue<ByteArray>(50)
        val amplifiedBuffer = ByteArray(bufferSize)

        val producer = Thread {
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_AUDIO)
            val buffer = ByteArray(bufferSize)
            while (isStreaming && audioRecord != null) {
                val read = audioRecord!!.read(buffer, 0, buffer.size)
                if (read > 0) {
                    val chunk = buffer.copyOf(read)
                    if (!queue.offer(chunk)) {
                        queue.poll()
                        queue.offer(chunk)
                    }
                } else if (read < 0) {
                    android.util.Log.e("AudioStreamer", "AudioRecord read error: $read")
                }
            }
        }.also { it.start() }

        streamThread = Thread {
            val socketOutputStream = socket?.getOutputStream()
            val bufferedOut = socketOutputStream?.let { java.io.BufferedOutputStream(it, 65536) }
            var chunks = 0
            var totalBytes = 0
            var lastDiagnosticChunks = 0
            var lastDiagnosticBytes = 0
            while (isStreaming || !queue.isEmpty()) {
                val chunk = queue.poll(100, TimeUnit.MILLISECONDS)
                if (chunk == null) continue
                val amplifiedLen = amplify(chunk, chunk.size, amplification, amplifiedBuffer)
                bufferedOut?.write(amplifiedBuffer, 0, amplifiedLen)
                bytesSent += amplifiedLen
                totalBytes += amplifiedLen
                chunks++
                lastRmsIn = computeRms(chunk, chunk.size)
                lastRmsOut = computeRms(amplifiedBuffer, amplifiedLen)
                lastRmsDb = if (lastRmsOut > 0) 20 * log10(lastRmsOut / 32768.0) else -60.0
                if (chunks % 200 == 0) {
                    val deltaChunks = chunks - lastDiagnosticChunks
                    val deltaBytes = totalBytes - lastDiagnosticBytes
                    android.util.Log.d("AudioStreamer", "Chunk=${chunk.size}, amplified=$amplifiedLen, "
                          + "sent=$deltaBytes bytes in $deltaChunks chunks")
                    lastDiagnosticChunks = chunks
                    lastDiagnosticBytes = totalBytes
                }
            }
            try {
                bufferedOut?.flush()
            } catch (e: Exception) {
                android.util.Log.e("AudioStreamer", "Flush error", e)
            }
            android.util.Log.d("AudioStreamer", "Streaming loop ended. Total: $totalBytes bytes in $chunks chunks")
        }.also { it.start() }
    }

    fun stopStreaming() {
        isStreaming = false
        streamThread?.interrupt()
        streamThread = null

        connectThread?.interrupt()
        connectThread = null

        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (e: Exception) {
            e.printStackTrace()
        }
        audioRecord = null

        try {
            socket?.close()
        } catch (e: Exception) {
            e.printStackTrace()
        }
        socket = null

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun amplify(data: ByteArray, length: Int, factor: Float, out: ByteArray = ByteArray(length)): Int {
        if (factor <= 1.0f) {
            data.copyInto(out, endIndex = length)
            return length
        }

        var i = 0
        while (i < length - 1) {
            val low = data[i].toInt() and 0xFF
            val high = data[i + 1].toInt()
            var sample = (high shl 8) or low
            if (sample >= 32768) sample -= 65536

            var amplifiedSample = (sample * factor).roundToInt()
            amplifiedSample = max(-32768, min(32767, amplifiedSample))
            if (amplifiedSample < 0) amplifiedSample += 65536

            out[i] = (amplifiedSample and 0xFF).toByte()
            out[i + 1] = ((amplifiedSample shr 8) and 0xFF).toByte()
            i += 2
        }
        if (length % 2 != 0) {
            out[length - 1] = data[length - 1]
        }
        return length
    }

    private fun computeRms(data: ByteArray, length: Int): Double {
        var sumSq = 0.0
        var count = 0
        var i = 0
        while (i < length - 1) {
            val low = data[i].toInt() and 0xFF
            val high = data[i + 1].toInt()
            var sample = (high shl 8) or low
            if (sample >= 32768) sample -= 65536
            val f = sample.toDouble()
            sumSq += f * f
            count++
            i += 2
        }
        return if (count > 0) sqrt(sumSq / count) else 0.0
    }
}

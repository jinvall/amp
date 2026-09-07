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
        const val EXTRA_AMPLIFICATION_CEILING = "amplification_ceiling"
        const val EXTRA_PRE_GAIN = "pre_gain"
        const val EXTRA_EQ_BANDS = "eq_bands"
        const val EXTRA_BREATHING_SENSITIVITY = "breathing_sensitivity"
        const val EXTRA_BREATHING_COOLDOWN = "breathing_cooldown"
        const val EXTRA_SEGMENT_DURATION = "segment_duration"
        const val EXTRA_NOISE_GATE = "noise_gate"
        const val EXTRA_AUDIO_SOURCE = "audio_source"
        const val NOTIFICATION_ID = 1
        const val CHANNEL_ID = "audio_streamer_channel"
        const val CONTROL_PORT = 8091

        // Audio source choices: id -> display name. The user can override the
        // auto-selection priority by picking a specific source here.
        // BLUETOOTH_SCO = 6 (raw value; constant may not resolve on all SDK configs)
        val BLUETOOTH_SCO = 6
        // WIRED_HEADSET (3.5mm jack) and USB are API 33 (Android 13) constants.
        // We reference the raw integer values so the app still compiles on lower
        // API levels where the named constant doesn't exist. If the device is
        // below API 33 the AudioRecord constructor will throw and we skip to the
        // next source in the auto-priority list.
        val WIRED_HEADSET = 24   // MediaRecorder.AudioSource.WIRED_HEADSET (API 33+)
        val USB = 25             // MediaRecorder.AudioSource.USB (API 33+)
        val VOICE_COMMUNICATION = 7  // MediaRecorder.AudioSource.VOICE_COMMUNICATION
        val AUDIO_SOURCES = mapOf(
            -1 to "Auto (Recommended)",
            MediaRecorder.AudioSource.UNPROCESSED to "Unprocessed (Raw)",
            MediaRecorder.AudioSource.VOICE_RECOGNITION to "Voice Recognition",
            MediaRecorder.AudioSource.CAMCORDER to "Camcorder",
            MediaRecorder.AudioSource.MIC to "Default Mic",
            BLUETOOTH_SCO to "Bluetooth Mic (SCO)",
            WIRED_HEADSET to "3.5mm Jack / Headset Mic",
            USB to "USB Microphone",
            VOICE_COMMUNICATION to "Voice Communication",
            MediaRecorder.AudioSource.DEFAULT to "System Default",
        )
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
    private var serverPort: Int = 8090
    private var amplification: Float = 1.0f
    private var amplificationCeiling: Float = 100.0f
    private var preGain: Float = 1.0f
    private val eqBands: FloatArray = floatArrayOf(0f, 0f, 0f, 0f, 0f)
    private var breathingSensitivity: Double = 100.0
    private var breathingCooldown: Double = 1.0
        private var segmentDurationSec: Int = 5
        private var noiseGate: Int = 0
        private var audioSource: Int = -1  // -1 = auto, otherwise a specific AudioSource id

    private var audioRecord: AudioRecord? = null
    private var streamThread: Thread? = null
    private var producerThread: Thread? = null
    private var socket: java.net.Socket? = null
    private var connectThread: Thread? = null
    private var bluetoothScoActive = false  // true while we forced BT SCO routing

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
            amplificationCeiling = it.getFloatExtra(EXTRA_AMPLIFICATION_CEILING, amplificationCeiling)
            preGain = it.getFloatExtra(EXTRA_PRE_GAIN, preGain)
            it.getFloatArrayExtra(EXTRA_EQ_BANDS)?.let { bands ->
                if (bands.size >= 5) {
                    for (i in 0 until 5) {
                        eqBands[i] = bands[i]
                    }
                    updateEqFilters()
                }
            }
            breathingSensitivity = it.getDoubleExtra(EXTRA_BREATHING_SENSITIVITY, breathingSensitivity)
            breathingCooldown = it.getDoubleExtra(EXTRA_BREATHING_COOLDOWN, breathingCooldown)
            segmentDurationSec = it.getIntExtra(EXTRA_SEGMENT_DURATION, segmentDurationSec)
            noiseGate = it.getIntExtra(EXTRA_NOISE_GATE, noiseGate)
            audioSource = it.getIntExtra(EXTRA_AUDIO_SOURCE, audioSource)
        }
        // Clamp current amplification to the selected ceiling
        amplification = min(amplification, amplificationCeiling)
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
        // Cancel any pending auto-reconnect; we are (re)starting deliberately now.
        reconnectRunnable?.let { reconnectHandler.removeCallbacks(it) }
        reconnectRunnable = null

        val sampleRate = 44100
        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val audioFormat = AudioFormat.ENCODING_PCM_16BIT

        val minBufSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
        val audioRecordBufferSize = max(minBufSize, 65536)

        // Audio source selection: if the user picked a specific source, try it
        // first; otherwise fall back to the auto-priority list. Auto mode tries
        // raw/less-processed sources first for better quiet-signal pickup.
        // Wired headset and USB mic are tried in the auto list so a connected
        // external device is picked up automatically without manual selection.
        val autoSources = arrayOf(
            MediaRecorder.AudioSource.UNPROCESSED,
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            WIRED_HEADSET,
            USB,
            VOICE_COMMUNICATION,
            BLUETOOTH_SCO,
            MediaRecorder.AudioSource.CAMCORDER,
            MediaRecorder.AudioSource.MIC,
            MediaRecorder.AudioSource.DEFAULT
        )

        // Build the try order: user-selected source first (if not auto), then auto list.
        val sources: List<Int> = if (audioSource >= 0) {
            listOf(audioSource) + autoSources.filter { it != audioSource }
        } else {
            autoSources.toList()
        }

        audioRecord = null
        var selectedSourceName = "none"
        for (source in sources) {
            // Bluetooth SCO and wired headset mics (earbuds, headsets, 3.5mm jack)
            // typically only support 8 or 16 kHz. Try the standard 44.1 kHz first;
            // if that fails, fall back to 16 kHz then 8 kHz.
            val sampleRates = if (source == BLUETOOTH_SCO || source == WIRED_HEADSET)
                intArrayOf(44100, 16000, 8000) else intArrayOf(44100)
            for (sr in sampleRates) {
                try {
                    android.util.Log.d("AudioStreamer", "Trying audio source: $source @ ${sr}Hz")
                    val testRecord = AudioRecord(source, sr, channelConfig, audioFormat, audioRecordBufferSize)
                    if (testRecord.state == AudioRecord.STATE_INITIALIZED) {
                        android.util.Log.d("AudioStreamer", "Audio source $source initialized @ ${sr}Hz")
                        testRecord.release()
                        audioRecord = AudioRecord(source, sr, channelConfig, audioFormat, audioRecordBufferSize)
                        selectedSourceName = AUDIO_SOURCES[source] ?: source.toString()
                        break
                    } else {
                        android.util.Log.w("AudioStreamer", "Audio source $source failed @ ${sr}Hz")
                        testRecord.release()
                    }
                } catch (e: Exception) {
                    android.util.Log.e("AudioStreamer", "Audio source $source error @ ${sr}Hz", e)
                }
            }
            if (audioRecord != null) break
        }

        if (audioRecord == null || audioRecord!!.state != AudioRecord.STATE_INITIALIZED) {
            android.util.Log.e("AudioStreamer", "No audio source available")
            stopSelf()
            return
        }
        android.util.Log.i("AudioStreamer", "Selected audio source: $selectedSourceName")

        // Bluetooth SCO requires explicit audio routing. Just creating an
        // AudioRecord with BLUETOOTH_SCO source is not enough — Android keeps
        // routing through the phone mic until we request the SCO connection.
        if (audioSource == BLUETOOTH_SCO) {
            startBluetoothSco()
        }

        // Try to disable automatic gain control and noise suppression
        // so we get the rawest possible signal for software amplification.
        disableAudioEffects(audioRecord!!.audioSessionId)

        // Retry-with-backoff connect. The receiver (Silver) may not be up yet when the
        // app starts, so we keep trying instead of killing the foreground service on the
        // first failure. The visualizer/WebSocket port is independent and never gates this.
        val maxAttempts = 30
        val backoffMs = 2000L
        connectThread = Thread {
            var attempt = 0
            while (attempt < maxAttempts && !isStreaming) {
                attempt++
                try {
                    android.util.Log.d("AudioStreamer", "Connecting to $serverIp:$serverPort (attempt $attempt/$maxAttempts)...")
                    val newSocket = java.net.Socket()
                    newSocket.connect(java.net.InetSocketAddress(serverIp, serverPort), 5000)
                    newSocket.tcpNoDelay = true
                    newSocket.sendBufferSize = 131072
                    socket = newSocket
                    android.util.Log.d("AudioStreamer", "Connected to $serverIp:$serverPort")
                    sendConfig(newSocket)
                    startRecordingLoop(audioRecordBufferSize)
                    return@Thread
                } catch (e: Exception) {
                    android.util.Log.w("AudioStreamer", "Connect attempt $attempt failed: ${e.message}")
                    if (attempt >= maxAttempts) {
                        android.util.Log.e("AudioStreamer", "Giving up after $maxAttempts attempts", e)
                        stopSelf()
                        return@Thread
                    }
                    try {
                        Thread.sleep(backoffMs)
                    } catch (_: InterruptedException) {
                        return@Thread
                    }
                }
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

    private fun disableAudioEffects(audioSessionId: Int) {
        try {
            val agc = android.media.audiofx.AutomaticGainControl.create(audioSessionId)
            if (agc != null) {
                agc.enabled = false
                android.util.Log.d("AudioStreamer", "Disabled AutomaticGainControl")
                agc.release()
            } else {
                android.util.Log.d("AudioStreamer", "AutomaticGainControl not available")
            }
        } catch (e: Exception) {
            android.util.Log.w("AudioStreamer", "Could not disable AGC", e)
        }

        try {
            val ns = android.media.audiofx.NoiseSuppressor.create(audioSessionId)
            if (ns != null) {
                ns.enabled = false
                android.util.Log.d("AudioStreamer", "Disabled NoiseSuppressor")
                ns.release()
            } else {
                android.util.Log.d("AudioStreamer", "NoiseSuppressor not available")
            }
        } catch (e: Exception) {
            android.util.Log.w("AudioStreamer", "Could not disable NoiseSuppressor", e)
        }
    }

    private fun startRecordingLoop(bufferSize: Int) {
        isStreaming = true
        bytesSent = 0
        audioRecord?.startRecording()
        android.util.Log.d("AudioStreamer", "Recording started")

        val queue = ArrayBlockingQueue<ByteArray>(50)
        val amplifiedBuffer = ByteArray(bufferSize)

        Thread {
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_AUDIO)
            val buffer = ByteArray(bufferSize)
            try {
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
                        onStreamFailed()
                        return@Thread
                    }
                }
            } catch (e: Exception) {
                // AudioRecord/read failure (e.g. device unplugged). Tear down and
                // reconnect rather than crash the foreground service.
                android.util.Log.e("AudioStreamer", "Producer error", e)
                onStreamFailed()
                return@Thread
            }
        }.also { producerThread = it; it.start() }

        streamThread = Thread {
            val socketOutputStream = socket?.getOutputStream()
            val bufferedOut = socketOutputStream?.let { java.io.BufferedOutputStream(it, 65536) }
            var chunks = 0
            var totalBytes = 0
            var lastDiagnosticChunks = 0
            var lastDiagnosticBytes = 0
            try {
                while (isStreaming || !queue.isEmpty()) {
                    val chunk = queue.poll(100, TimeUnit.MILLISECONDS)
                    if (chunk == null) continue
                    val processedLen = processAudio(chunk, chunk.size, amplifiedBuffer)
                    bufferedOut?.write(amplifiedBuffer, 0, processedLen)
                    bytesSent += processedLen
                    totalBytes += processedLen
                    chunks++
                    lastRmsIn = computeRms(chunk, chunk.size)
                    lastRmsOut = computeRms(amplifiedBuffer, processedLen)
                    lastRmsDb = if (lastRmsOut > 0) 20 * log10(lastRmsOut / 32768.0) else -60.0
                    if (chunks % 200 == 0) {
                        val deltaChunks = chunks - lastDiagnosticChunks
                        val deltaBytes = totalBytes - lastDiagnosticBytes
                        android.util.Log.d("AudioStreamer", "Chunk=${chunk.size}, processed=$processedLen, "
                              + "sent=$deltaBytes bytes in $deltaChunks chunks")
                        lastDiagnosticChunks = chunks
                        lastDiagnosticBytes = totalBytes
                    }
                }
            } catch (e: java.io.IOException) {
                android.util.Log.e("AudioStreamer", "Stream write error (broken pipe?)", e)
                onStreamFailed()
                return@Thread
            } catch (e: Exception) {
                android.util.Log.e("AudioStreamer", "Stream unexpected error", e)
                onStreamFailed()
                return@Thread
            } finally {
                try {
                    bufferedOut?.flush()
                } catch (e: Exception) {
                    android.util.Log.e("AudioStreamer", "Flush error", e)
                }
                android.util.Log.d("AudioStreamer", "Streaming loop ended. Total: $totalBytes bytes in $chunks chunks")
            }
        }.also { it.start() }
    }

    fun stopStreaming() {
        isStreaming = false
        // Cancel any pending reconnect so a stopped service stays stopped.
        reconnectRunnable?.let { reconnectHandler.removeCallbacks(it) }
        reconnectRunnable = null
        streamThread?.interrupt()
        streamThread = null

        connectThread?.interrupt()
        connectThread = null

        cleanupRecording()

        try {
            socket?.close()
        } catch (e: Exception) {
            e.printStackTrace()
        }
        socket = null

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /**
     * Full teardown of the capture/encode pipeline resources. Safe to call on a
     * stream error (before reconnect) and on user stop. Idempotent.
     */
    private fun cleanupRecording() {
        stopBluetoothSco()
        producerThread?.let { try { it.interrupt() } catch (_: Exception) {} }
        producerThread = null
        try { audioRecord?.stop() } catch (_: Exception) {}
        try { audioRecord?.release() } catch (_: Exception) {}
        audioRecord = null
    }

    /**
     * Force audio routing through a connected Bluetooth SCO device (earbuds,
     * headset). Without this, AudioRecord with BLUETOOTH_SCO source still
     * captures from the phone's built-in mic. Requires BLUETOOTH_CONNECT
     * permission on Android 12+ (handled by the manifest).
     */
    private fun startBluetoothSco() {
        val am = getSystemService(AudioManager::class.java) ?: return
        try {
            am.mode = AudioManager.MODE_IN_COMMUNICATION
            am.isBluetoothScoOn = true
            am.startBluetoothSco()
            bluetoothScoActive = true
            android.util.Log.i("AudioStreamer", "Bluetooth SCO routing started")
        } catch (e: Exception) {
            android.util.Log.e("AudioStreamer", "Failed to start Bluetooth SCO", e)
        }
    }

    private fun stopBluetoothSco() {
        if (!bluetoothScoActive) return
        val am = getSystemService(AudioManager::class.java) ?: return
        try {
            am.stopBluetoothSco()
            am.isBluetoothScoOn = false
            am.mode = AudioManager.MODE_NORMAL
            android.util.Log.i("AudioStreamer", "Bluetooth SCO routing stopped")
        } catch (e: Exception) {
            android.util.Log.e("AudioStreamer", "Failed to stop Bluetooth SCO", e)
        }
        bluetoothScoActive = false
    }

    /**
     * Single failure path: stop the pipeline, then schedule exactly one reconnect.
     * Idempotent — calling it twice won't spawn two reconnect timers.
     */
    private fun onStreamFailed() {
        isStreaming = false
        cleanupRecording()
        scheduleReconnect()
    }

    private val reconnectHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private var reconnectRunnable: Runnable? = null

    private fun scheduleReconnect() {
        reconnectRunnable?.let { reconnectHandler.removeCallbacks(it) }
        reconnectRunnable = Runnable {
            android.util.Log.d("AudioStreamer", "Attempting reconnect after stream error...")
            startStreaming()
        }
        // Collapse rapid failures into one reconnect attempt shortly after the last.
        reconnectHandler.postDelayed(reconnectRunnable!!, 1000)
    }

    /**
     * Apply parameter changes live while streaming, without restarting the service.
     * Called from the UI via the bound binder. Safe to call repeatedly.
     */
    fun updateLiveParams(
        amplification: Float,
        amplificationCeiling: Float,
        preGain: Float,
        eqBands: FloatArray,
        breathingSensitivity: Double,
        breathingCooldown: Double,
        segmentDurationSec: Int
    ) {
        if (eqBands.size >= 5) {
            for (i in 0 until 5) {
                this.eqBands[i] = eqBands[i]
            }
            updateEqFilters()
        }
        this.amplification = min(amplification, amplificationCeiling)
        this.amplificationCeiling = amplificationCeiling
        this.preGain = preGain
        this.breathingSensitivity = breathingSensitivity
        this.breathingCooldown = breathingCooldown
        this.segmentDurationSec = segmentDurationSec
        android.util.Log.d("AudioStreamer", "Live params updated: amp=${this.amplification} preGain=$preGain")
    }

    /**
     * Push server-side parameters (breathing + segment) to the receiver's control port.
     * Best-effort and debounced; never throws or disrupts the audio stream.
     */
    private var controlPushRunnable: Runnable? = null
    private val controlHandler = android.os.Handler(android.os.Looper.getMainLooper())

    fun pushServerConfig(breathingSensitivity: Double, breathingCooldown: Double, segmentDurationMin: Int) {
        controlPushRunnable?.let { controlHandler.removeCallbacks(it) }
        controlPushRunnable = Runnable {
            sendControlConfig(breathingSensitivity, breathingCooldown, segmentDurationMin)
        }
        // Debounce: collapse rapid slider moves into a single push shortly after the last change.
        controlHandler.postDelayed(controlPushRunnable!!, 400)
    }

    private fun sendControlConfig(sensitivity: Double, cooldown: Double, segmentMin: Int) {
        val ip = serverIp
        if (ip.isEmpty()) return
        try {
            val cfg = JSONObject()
            cfg.put("breathing_sensitivity", sensitivity)
            cfg.put("breathing_cooldown", cooldown)
            cfg.put("segment_duration_min", segmentMin)
            val line = cfg.toString() + "\n"
            Thread {
                try {
                    val s = java.net.Socket()
                    s.connect(java.net.InetSocketAddress(ip, CONTROL_PORT), 3000)
                    s.getOutputStream().write(line.toByteArray(Charsets.UTF_8))
                    s.getOutputStream().flush()
                    s.close()
                    android.util.Log.d("AudioStreamer", "Pushed live config to control port: $line")
                } catch (e: Exception) {
                    // Non-fatal: live push is best-effort; local processing is unaffected.
                    android.util.Log.w("AudioStreamer", "Control push failed (ignored): ${e.message}")
                }
            }.start()
        } catch (e: Exception) {
            android.util.Log.w("AudioStreamer", "Control push setup failed (ignored): ${e.message}")
        }
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

            val amplified = (sample * factor).toDouble()
            // Soft clip with tanh to avoid harsh digital clipping.
            val clipped = if (kotlin.math.abs(amplified) > 32767.0) {
                (kotlin.math.tanh(amplified / 32767.0) * 32767.0).roundToInt()
            } else {
                amplified.roundToInt()
            }
            var clamped = max(-32768, min(32767, clipped))
            if (clamped < 0) clamped += 65536

            out[i] = (clamped and 0xFF).toByte()
            out[i + 1] = ((clamped shr 8) and 0xFF).toByte()
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

    private class BiquadFilter {
        var b0 = 1.0
        var b1 = 0.0
        var b2 = 0.0
        var a1 = 0.0
        var a2 = 0.0
        var x1 = 0.0
        var x2 = 0.0
        var y1 = 0.0
        var y2 = 0.0

        fun setPeaking(f0: Double, Q: Double, gainDb: Double, sampleRate: Double) {
            val w0 = 2.0 * Math.PI * f0 / sampleRate
            val alpha = Math.sin(w0) / (2.0 * Q)
            val A = Math.pow(10.0, gainDb / 40.0)
            b0 = 1.0 + alpha * A
            b1 = -2.0 * Math.cos(w0)
            b2 = 1.0 - alpha * A
            val a0 = 1.0 + alpha / A
            a1 = -2.0 * Math.cos(w0)
            a2 = 1.0 - alpha / A
            b0 /= a0
            b1 /= a0
            b2 /= a0
            a1 /= a0
            a2 /= a0
        }

        fun process(sample: Double): Double {
            val out = b0 * sample + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2 = x1
            x1 = sample
            y2 = y1
            y1 = out
            return out
        }

        fun reset() {
            x1 = 0.0
            x2 = 0.0
            y1 = 0.0
            y2 = 0.0
        }
    }

    private val eqFilters = Array(5) { BiquadFilter() }
    private val eqFrequencies = doubleArrayOf(60.0, 230.0, 910.0, 4000.0, 14000.0)
    private val eqQ = 1.5
    private val eqSampleRate = 44100.0

    init {
        updateEqFilters()
    }

    private fun updateEqFilters() {
        for (i in eqFilters.indices) {
            eqFilters[i].setPeaking(eqFrequencies[i], eqQ, eqBands[i].toDouble(), eqSampleRate)
        }
    }

    private fun processAudio(input: ByteArray, length: Int, out: ByteArray): Int {
        val preGain = this.preGain.toDouble()
        val amplification = this.amplification.toDouble()
        var i = 0
        var outIdx = 0
        while (i < length - 1) {
            val low = input[i].toInt() and 0xFF
            val high = input[i + 1].toInt()
            var sample = (high shl 8) or low
            if (sample >= 32768) sample -= 65536
            var fSample = sample.toDouble()
            fSample *= preGain
            for (filter in eqFilters) {
                fSample = filter.process(fSample)
            }
            fSample *= amplification
            // Soft clip: tanh-based limiting to [-32767, 32767]. Hard clipping at
            // integer limits causes harsh distortion; tanh gives a smooth
            // saturation curve that stays within 16-bit range without crunch.
            val clipped = if (kotlin.math.abs(fSample) > 32767.0) {
                (kotlin.math.tanh(fSample / 32767.0) * 32767.0).roundToInt()
            } else {
                fSample.roundToInt()
            }
            var clamped = max(-32768, min(32767, clipped))
            if (clamped < 0) clamped += 65536
            out[outIdx] = (clamped and 0xFF).toByte()
            out[outIdx + 1] = ((clamped shr 8) and 0xFF).toByte()
            outIdx += 2
            i += 2
        }
        if (length % 2 != 0) {
            out[length - 1] = input[length - 1]
        }
        return length
    }
}

package com.amp.streamer

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.text.TextUtils
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.amp.streamer.databinding.ActivityMainBinding
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var streamerService: AudioStreamerService? = null
    private var bound = false
    private val rmsHandler = Handler(Looper.getMainLooper())
    private var rmsRunnable: Runnable? = null

    private val REQUEST_PERMISSIONS = 1001

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(className: ComponentName, service: IBinder) {
            val binder = service as AudioStreamerService.StreamerBinder
            streamerService = binder.getService()
            bound = true
            updateUiState()
        }

        override fun onServiceDisconnected(className: ComponentName) {
            bound = false
            streamerService = null
            updateUiState()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.sbAmplification.progress = 100
        binding.tvAmplificationValue.text = amplificationFromProgress(binding.sbAmplification.progress).toString()

        binding.sbBreathingSensitivity.progress = 80
        binding.tvBreathingSensitivityValue.text = sensitivityFromProgress(binding.sbBreathingSensitivity.progress).toString()

        binding.sbCooldown.progress = 20
        binding.tvCooldownValue.text = cooldownFromProgress(binding.sbCooldown.progress).toString()

        binding.sbNoiseGate.progress = 10
        binding.tvNoiseGateValue.text = noiseGateFromProgress(binding.sbNoiseGate.progress).toString()

        binding.sbSegmentDuration.progress = 50
        binding.tvSegmentDurationValue.text = segmentDurationFromProgress(binding.sbSegmentDuration.progress).toString()

        binding.sbAmplification.setOnSeekBarChangeListener(seekListener { progress ->
            binding.tvAmplificationValue.text = amplificationFromProgress(progress).toString()
        })

        binding.sbBreathingSensitivity.setOnSeekBarChangeListener(seekListener { progress ->
            binding.tvBreathingSensitivityValue.text = sensitivityFromProgress(progress).toString()
        })

        binding.sbCooldown.setOnSeekBarChangeListener(seekListener { progress ->
            binding.tvCooldownValue.text = cooldownFromProgress(progress).toString()
        })

        binding.sbNoiseGate.setOnSeekBarChangeListener(seekListener { progress ->
            binding.tvNoiseGateValue.text = noiseGateFromProgress(progress).toString()
        })

        binding.sbSegmentDuration.setOnSeekBarChangeListener(seekListener { progress ->
            binding.tvSegmentDurationValue.text = segmentDurationFromProgress(progress).toString()
        })

        binding.btnStartStop.setOnClickListener {
            if (bound && streamerService?.isCurrentlyStreaming == true) {
                stopStreaming()
            } else {
                startStreaming()
            }
        }
    }

    private fun seekListener(onChange: (Int) -> Unit): android.widget.SeekBar.OnSeekBarChangeListener =
        object : android.widget.SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: android.widget.SeekBar?, progress: Int, fromUser: Boolean) {
                if (fromUser) onChange(progress)
            }

            override fun onStartTrackingTouch(seekBar: android.widget.SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: android.widget.SeekBar?) {}
        }

    override fun onStart() {
        super.onStart()
        Intent(this, AudioStreamerService::class.java).also { intent ->
            bindService(intent, connection, Context.BIND_AUTO_CREATE)
        }
        startRmsUpdates()
    }

    override fun onStop() {
        super.onStop()
        stopRmsUpdates()
        if (bound) {
            unbindService(connection)
            bound = false
        }
    }

    private fun startRmsUpdates() {
        stopRmsUpdates()
        rmsRunnable = object : Runnable {
            override fun run() {
                updateRmsDisplay()
                rmsHandler.postDelayed(this, 250)
            }
        }
        rmsHandler.post(rmsRunnable!!)
    }

    private fun stopRmsUpdates() {
        rmsRunnable?.let { rmsHandler.removeCallbacks(it) }
        rmsRunnable = null
    }

    private fun updateRmsDisplay() {
        val rmsIn = streamerService?.lastRmsIn ?: 0.0
        val rmsOut = streamerService?.lastRmsOut ?: 0.0
        val rmsDb = streamerService?.lastRmsDb ?: -60.0
        binding.tvRmsIn.text = "In: ${"%.0f".format(rmsIn)}"
        binding.tvRmsOut.text = "Out: ${"%.0f".format(rmsOut)}"
        binding.tvRmsDb.text = "dB: ${"%.1f".format(rmsDb)}"

        val bytesSent = if (bound && streamerService?.isCurrentlyStreaming == true) {
            streamerService?.bytesSent ?: 0
        } else {
            0
        }
        binding.tvBytes.text = "Bytes sent: $bytesSent"
    }

    private fun startStreaming() {
        if (!hasPermissions()) {
            requestPermissions()
            return
        }

        val ip = binding.etServerIp.text.toString().trim()
        val portStr = binding.etServerPort.text.toString().trim()

        if (TextUtils.isEmpty(ip) || TextUtils.isEmpty(portStr)) {
            Toast.makeText(this, "Please fill all fields", Toast.LENGTH_SHORT).show()
            return
        }

        val port = portStr.toIntOrNull()
        if (port == null || port !in 1..65535) {
            Toast.makeText(this, "Invalid port", Toast.LENGTH_SHORT).show()
            return
        }

        val amplification = amplificationFromProgress(binding.sbAmplification.progress)
        val sensitivity = sensitivityFromProgress(binding.sbBreathingSensitivity.progress)
        val cooldown = cooldownFromProgress(binding.sbCooldown.progress)
        val segmentDuration = segmentDurationFromProgress(binding.sbSegmentDuration.progress)
        val noiseGate = noiseGateFromProgress(binding.sbNoiseGate.progress)

        val intent = Intent(this, AudioStreamerService::class.java).apply {
            putExtra(AudioStreamerService.EXTRA_SERVER_IP, ip)
            putExtra(AudioStreamerService.EXTRA_SERVER_PORT, port)
            putExtra(AudioStreamerService.EXTRA_AMPLIFICATION, amplification)
            putExtra(AudioStreamerService.EXTRA_BREATHING_SENSITIVITY, sensitivity)
            putExtra(AudioStreamerService.EXTRA_BREATHING_COOLDOWN, cooldown)
            putExtra(AudioStreamerService.EXTRA_SEGMENT_DURATION, segmentDuration)
            putExtra(AudioStreamerService.EXTRA_NOISE_GATE, noiseGate)
        }
        startForegroundService(intent)
        bindService(intent, connection, Context.BIND_AUTO_CREATE)
    }

    private fun stopStreaming() {
        streamerService?.stopStreaming()
        updateUiState()
    }

    private fun hasPermissions(): Boolean {
        val perms = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            perms.add(Manifest.permission.RECORD_AUDIO)
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                perms.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        return perms.isEmpty()
    }

    private fun requestPermissions() {
        val perms = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            perms.add(Manifest.permission.RECORD_AUDIO)
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                perms.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        ActivityCompat.requestPermissions(this, perms.toTypedArray(), REQUEST_PERMISSIONS)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_PERMISSIONS) {
            if (grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
                startStreaming()
            } else {
                Toast.makeText(this, "Permissions required for streaming", Toast.LENGTH_LONG).show()
            }
        }
    }

    fun updateUiState() {
        val streaming = bound && streamerService?.isCurrentlyStreaming == true
        binding.btnStartStop.text = if (streaming) getString(R.string.stop) else getString(R.string.start)
        binding.tvStatus.text = if (streaming) getString(R.string.streaming) else getString(R.string.idle)

        val bytesSent = if (bound && streamerService?.isCurrentlyStreaming == true) {
            streamerService?.bytesSent ?: 0
        } else {
            0
        }
        binding.tvBytes.text = "Bytes sent: $bytesSent"

        val rmsIn = streamerService?.lastRmsIn ?: 0.0
        val rmsOut = streamerService?.lastRmsOut ?: 0.0
        val rmsDb = streamerService?.lastRmsDb ?: -60.0
        binding.tvRmsIn.text = "In: ${"%.0f".format(rmsIn)}"
        binding.tvRmsOut.text = "Out: ${"%.0f".format(rmsOut)}"
        binding.tvRmsDb.text = "dB: ${"%.1f".format(rmsDb)}"
    }

    override fun onResume() {
        super.onResume()
        updateUiState()
    }

    companion object {
        private const val MAX_SENSITIVITY = 100.0
        private const val MAX_COOLDOWN = 10.0
        private const val MIN_SEGMENT_DURATION_MIN = 1
        private const val MAX_SEGMENT_DURATION_MIN = 10

        fun amplificationFromProgress(progress: Int): Float = 1.0f + (progress / 100.0f) * 99.0f
        fun sensitivityFromProgress(progress: Int): Double = (progress / 100.0) * MAX_SENSITIVITY
        fun cooldownFromProgress(progress: Int): Double = (progress / 100.0) * MAX_COOLDOWN
        fun segmentDurationFromProgress(progress: Int): Int = MIN_SEGMENT_DURATION_MIN + (progress / 100.0f * (MAX_SEGMENT_DURATION_MIN - MIN_SEGMENT_DURATION_MIN)).roundToInt()
        fun noiseGateFromProgress(progress: Int): Int = (progress / 100.0f * 2000).roundToInt()
    }
}

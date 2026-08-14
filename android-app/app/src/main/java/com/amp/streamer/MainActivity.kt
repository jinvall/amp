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

        // Restore persisted slider positions (defaults if none saved).
        val prefs = getPreferences(Context.MODE_PRIVATE)
        binding.sbAmplification.progress = prefs.getInt(KEY_AMP, 100)
        binding.sbGainCeiling.progress = prefs.getInt(KEY_CEILING, 50)
        binding.sbPreGain.progress = prefs.getInt(KEY_PREGain, 0)
        binding.sbEq1.progress = prefs.getInt(KEY_EQ1, 50)
        binding.sbEq2.progress = prefs.getInt(KEY_EQ2, 50)
        binding.sbEq3.progress = prefs.getInt(KEY_EQ3, 50)
        binding.sbEq4.progress = prefs.getInt(KEY_EQ4, 50)
        binding.sbEq5.progress = prefs.getInt(KEY_EQ5, 50)
        binding.sbBreathingSensitivity.progress = prefs.getInt(KEY_SENS, 80)
        binding.sbCooldown.progress = prefs.getInt(KEY_COOLDOWN, 20)
        binding.sbNoiseGate.progress = prefs.getInt(KEY_NOISEGATE, 10)
        binding.sbSegmentDuration.progress = prefs.getInt(KEY_SEGMENT, 50)

        refreshAllLabels()
        wireSeekBar(binding.sbAmplification) { amplificationFromProgress(it) }
        wireSeekBar(binding.sbGainCeiling) { gainCeilingFromProgress(it) }
        wireSeekBar(binding.sbPreGain) { preGainFromProgress(it) }
        wireSeekBar(binding.sbEq1) { eqBandFromProgress(it) }
        wireSeekBar(binding.sbEq2) { eqBandFromProgress(it) }
        wireSeekBar(binding.sbEq3) { eqBandFromProgress(it) }
        wireSeekBar(binding.sbEq4) { eqBandFromProgress(it) }
        wireSeekBar(binding.sbEq5) { eqBandFromProgress(it) }
        wireSeekBar(binding.sbBreathingSensitivity) { sensitivityFromProgress(it) }
        wireSeekBar(binding.sbCooldown) { cooldownFromProgress(it) }
        wireSeekBar(binding.sbNoiseGate) { noiseGateFromProgress(it) }
        wireSeekBar(binding.sbSegmentDuration) { segmentDurationFromProgress(it) }

        binding.btnStartStop.setOnClickListener {
            if (bound && streamerService?.isCurrentlyStreaming == true) {
                stopStreaming()
            } else {
                startStreaming()
            }
        }
    }

    private fun refreshAllLabels() {
        binding.tvAmplificationValue.text = amplificationFromProgress(binding.sbAmplification.progress).toString()
        binding.tvGainCeilingValue.text = gainCeilingFromProgress(binding.sbGainCeiling.progress).toString()
        binding.tvPreGainValue.text = preGainFromProgress(binding.sbPreGain.progress).toString()
        binding.tvEq1Value.text = eqBandFromProgress(binding.sbEq1.progress).toString()
        binding.tvEq2Value.text = eqBandFromProgress(binding.sbEq2.progress).toString()
        binding.tvEq3Value.text = eqBandFromProgress(binding.sbEq3.progress).toString()
        binding.tvEq4Value.text = eqBandFromProgress(binding.sbEq4.progress).toString()
        binding.tvEq5Value.text = eqBandFromProgress(binding.sbEq5.progress).toString()
        binding.tvBreathingSensitivityValue.text = sensitivityFromProgress(binding.sbBreathingSensitivity.progress).toString()
        binding.tvCooldownValue.text = cooldownFromProgress(binding.sbCooldown.progress).toString()
        binding.tvNoiseGateValue.text = noiseGateFromProgress(binding.sbNoiseGate.progress).toString()
        binding.tvSegmentDurationValue.text = segmentDurationFromProgress(binding.sbSegmentDuration.progress).toString()
    }

    private fun wireSeekBar(seekBar: android.widget.SeekBar, label: (Int) -> Any) {
        val labelView: TextView? = when (seekBar.id) {
            R.id.sbAmplification -> binding.tvAmplificationValue
            R.id.sbGainCeiling -> binding.tvGainCeilingValue
            R.id.sbPreGain -> binding.tvPreGainValue
            R.id.sbEq1 -> binding.tvEq1Value
            R.id.sbEq2 -> binding.tvEq2Value
            R.id.sbEq3 -> binding.tvEq3Value
            R.id.sbEq4 -> binding.tvEq4Value
            R.id.sbEq5 -> binding.tvEq5Value
            R.id.sbBreathingSensitivity -> binding.tvBreathingSensitivityValue
            R.id.sbCooldown -> binding.tvCooldownValue
            R.id.sbNoiseGate -> binding.tvNoiseGateValue
            R.id.sbSegmentDuration -> binding.tvSegmentDurationValue
            else -> null
        }
        seekBar.setOnSeekBarChangeListener(object : android.widget.SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: android.widget.SeekBar?, progress: Int, fromUser: Boolean) {
                labelView?.text = label(progress).toString()
            }
            override fun onStartTrackingTouch(sb: android.widget.SeekBar?) {}
            override fun onStopTrackingTouch(sb: android.widget.SeekBar?) {
                saveProgress()
                pushLiveIfStreaming()
            }
        })
    }

    private fun saveProgress() {
        val prefs = getPreferences(Context.MODE_PRIVATE)
        prefs.edit().apply {
            putInt(KEY_AMP, binding.sbAmplification.progress)
            putInt(KEY_CEILING, binding.sbGainCeiling.progress)
            putInt(KEY_PREGain, binding.sbPreGain.progress)
            putInt(KEY_EQ1, binding.sbEq1.progress)
            putInt(KEY_EQ2, binding.sbEq2.progress)
            putInt(KEY_EQ3, binding.sbEq3.progress)
            putInt(KEY_EQ4, binding.sbEq4.progress)
            putInt(KEY_EQ5, binding.sbEq5.progress)
            putInt(KEY_SENS, binding.sbBreathingSensitivity.progress)
            putInt(KEY_COOLDOWN, binding.sbCooldown.progress)
            putInt(KEY_NOISEGATE, binding.sbNoiseGate.progress)
            putInt(KEY_SEGMENT, binding.sbSegmentDuration.progress)
            apply()
        }
    }

    private fun currentEqBands(): FloatArray = floatArrayOf(
        eqBandFromProgress(binding.sbEq1.progress),
        eqBandFromProgress(binding.sbEq2.progress),
        eqBandFromProgress(binding.sbEq3.progress),
        eqBandFromProgress(binding.sbEq4.progress),
        eqBandFromProgress(binding.sbEq5.progress)
    )

    private fun pushLiveIfStreaming() {
        if (bound && streamerService?.isCurrentlyStreaming == true) {
            val amp = amplificationFromProgress(binding.sbAmplification.progress)
            val ceiling = gainCeilingFromProgress(binding.sbGainCeiling.progress)
            val preGain = preGainFromProgress(binding.sbPreGain.progress)
            val eq = currentEqBands()
            val sens = sensitivityFromProgress(binding.sbBreathingSensitivity.progress)
            val cooldown = cooldownFromProgress(binding.sbCooldown.progress)
            val segmentSec = segmentDurationFromProgress(binding.sbSegmentDuration.progress)
            streamerService?.updateLiveParams(amp, ceiling, preGain, eq, sens, cooldown, segmentSec)
            streamerService?.pushServerConfig(sens, cooldown, segmentSec / 60)
        }
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
        val gainCeiling = gainCeilingFromProgress(binding.sbGainCeiling.progress)
        val preGain = preGainFromProgress(binding.sbPreGain.progress)
        val eqBands = floatArrayOf(
            eqBandFromProgress(binding.sbEq1.progress),
            eqBandFromProgress(binding.sbEq2.progress),
            eqBandFromProgress(binding.sbEq3.progress),
            eqBandFromProgress(binding.sbEq4.progress),
            eqBandFromProgress(binding.sbEq5.progress)
        )
        val sensitivity = sensitivityFromProgress(binding.sbBreathingSensitivity.progress)
        val cooldown = cooldownFromProgress(binding.sbCooldown.progress)
        val segmentDuration = segmentDurationFromProgress(binding.sbSegmentDuration.progress)
        val noiseGate = noiseGateFromProgress(binding.sbNoiseGate.progress)

        val intent = Intent(this, AudioStreamerService::class.java).apply {
            putExtra(AudioStreamerService.EXTRA_SERVER_IP, ip)
            putExtra(AudioStreamerService.EXTRA_SERVER_PORT, port)
            putExtra(AudioStreamerService.EXTRA_AMPLIFICATION, amplification)
            putExtra(AudioStreamerService.EXTRA_AMPLIFICATION_CEILING, gainCeiling)
            putExtra(AudioStreamerService.EXTRA_PRE_GAIN, preGain)
            putExtra(AudioStreamerService.EXTRA_EQ_BANDS, eqBands)
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
        // SharedPreferences keys (store raw 0-100 progress so converters stay source of truth)
        private const val KEY_AMP = "amp"
        private const val KEY_CEILING = "ceiling"
        private const val KEY_PREGain = "preGain"
        private const val KEY_EQ1 = "eq1"
        private const val KEY_EQ2 = "eq2"
        private const val KEY_EQ3 = "eq3"
        private const val KEY_EQ4 = "eq4"
        private const val KEY_EQ5 = "eq5"
        private const val KEY_SENS = "sens"
        private const val KEY_COOLDOWN = "cooldown"
        private const val KEY_NOISEGATE = "noisegate"
        private const val KEY_SEGMENT = "segment"

        private const val MAX_SENSITIVITY = 100.0
        private const val MAX_COOLDOWN = 10.0
        private const val MIN_SEGMENT_DURATION_MIN = 1
        private const val MAX_SEGMENT_DURATION_MIN = 10
        private const val MIN_GAIN_CEILING = 100.0f
        private const val MAX_GAIN_CEILING = 1000.0f
        private const val MIN_PRE_GAIN = 1.0f
        private const val MAX_PRE_GAIN = 10.0f
        private const val MIN_EQ_DB = -12.0
        private const val MAX_EQ_DB = 12.0

        fun amplificationFromProgress(progress: Int): Float = 1.0f + (progress / 100.0f) * 99.0f
        fun sensitivityFromProgress(progress: Int): Double = (progress / 100.0) * MAX_SENSITIVITY
        fun cooldownFromProgress(progress: Int): Double = (progress / 100.0) * MAX_COOLDOWN
        fun segmentDurationFromProgress(progress: Int): Int = (MIN_SEGMENT_DURATION_MIN + (progress / 100.0) * (MAX_SEGMENT_DURATION_MIN - MIN_SEGMENT_DURATION_MIN)).toInt()
        fun noiseGateFromProgress(progress: Int): Int = progress * 10
        fun gainCeilingFromProgress(progress: Int): Float = MIN_GAIN_CEILING + (progress / 100.0f) * (MAX_GAIN_CEILING - MIN_GAIN_CEILING)
        fun preGainFromProgress(progress: Int): Float = MIN_PRE_GAIN + (progress / 100.0f) * (MAX_PRE_GAIN - MIN_PRE_GAIN)
        fun eqBandFromProgress(progress: Int): Float = (MIN_EQ_DB + (progress / 100.0f) * (MAX_EQ_DB - MIN_EQ_DB)).toFloat()
    }
}

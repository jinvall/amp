#!/usr/bin/env python3
"""Pluggable stem extraction backends.

Backends
--------
* ``DemucsBackend`` — real ML source separation via Facebook Demucs.
  Produces distinct instrument stems: vocals, guitar (the "other" stem),
  bass, drums, plus a derived ``noise`` residual. Requires the optional
  ``demucs`` package (CPU-capable, ~1-2GB model download on first use).
  This is the preferred backend when installed.

* ``FrequencyBandBackend`` — instant, CPU-only, no ML.
  Splits audio into coarse frequency bands (low, mid, high, residual).
  Works immediately and demonstrates the full pipeline. Used as a fallback
  when Demucs is not installed.
"""

import os
import struct
import subprocess
import wave

import numpy as np

import audio_receiver as ar


class StemBackend:
    """Interface for stem extraction backends."""

    @staticmethod
    def extract(source_path, output_dir):
        raise NotImplementedError

    @staticmethod
    def _load_wav_mono(wav_path, target_sr=None):
        """Load a WAV as mono float64 at ``target_sr`` (resampled if needed)."""
        with wave.open(wav_path, "rb") as w:
            sr = w.getframerate()
            n = w.getnframes()
            nch = w.getnchannels()
            raw = w.readframes(n)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        if nch > 1:
            samples = samples.reshape(-1, nch).mean(axis=1)
        if target_sr is not None and sr != target_sr and n > 0:
            # Lightweight linear resample (no scipy dependency).
            x_old = np.linspace(0, 1, len(samples), endpoint=False)
            x_new = np.linspace(0, 1, int(round(len(samples) * target_sr / sr)), endpoint=False)
            samples = np.interp(x_new, x_old, samples)
        return samples

    @staticmethod
    def _write_wav_mono(wav_path, samples, sr):
        s = np.clip(samples, -32768, 32767).astype(np.int16)
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(s.tobytes())


class DemucsBackend(StemBackend):
    """Real instrument separation via Facebook Demucs.

    Uses the ``htdemucs_ft`` model which outputs four stems: vocals, drums,
    bass, other. We map them to the app's analysis bands:

    * ``vocals`` — lead + harmony vocals
    * ``guitar`` — the "other" stem (guitar, keys, synth, etc.)
    * ``bass``   — bass guitar / sub
    * ``drums``  — percussion / kit
    * ``noise``  — derived residual = source - (vocals+guitar+bass+drums).
                   Useful for noise-gate / breathing analysis.

    Requires the optional ``demucs`` package (CPU-capable). Falls back to
    subprocess CLI which is version-stable across Demucs 4.x.
    """

    # Demucs model -> the stem files it produces.
    MODEL = "htdemucs_ft"
    # Map Demucs stem name -> our output stem name.
    STEM_MAP = {
        "vocals": "vocals",
        "other": "guitar",
        "bass": "bass",
        "drums": "drums",
    }
    # Extra derived stems (computed, not produced by Demucs).
    DERIVED = ["noise"]

    @classmethod
    def available(cls):
        try:
            import importlib.util  # noqa: F401
            # Prefer an importable package; CLI is checked at runtime.
            return importlib.util.find_spec("demucs") is not None or cls._demucs_cli() is not None
        except Exception:
            return False

    @staticmethod
    def _demucs_cli():
        for exe in ("demucs", "python3 -m demucs"):
            try:
                if exe.startswith("python"):
                    r = subprocess.run(exe.split() + ["--help"], capture_output=True, timeout=15)
                else:
                    r = subprocess.run([exe, "--help"], capture_output=True, timeout=15)
                if r.returncode == 0:
                    return exe
            except Exception:
                continue
        return None

    @classmethod
    def extract(cls, source_path, output_dir):
        cli = cls._demucs_cli()
        if cli is None:
            raise RuntimeError("demucs not installed (pip install demucs)")

        # Demucs writes to <tmp>/<MODEL>/<track>/<stem>.wav
        import tempfile
        tmp = tempfile.mkdtemp(prefix="demucs_")
        track = os.path.splitext(os.path.basename(source_path))[0]

        cmd = (cli.split() if " " in cli else [cli]) + [
            "-n", cls.MODEL, "-o", tmp, "--two-stems" if False else source_path
        ]
        # Simplest robust invocation: separate all 4 stems.
        cmd = (cli.split() if " " in cli else [cli]) + ["-n", cls.MODEL, "-o", tmp, source_path]
        print(f"[demucs] running: {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("demucs failed: " + (r.stderr or r.stdout)[-500:])

        model_dir = os.path.join(tmp, cls.MODEL, track)
        if not os.path.isdir(model_dir):
            # Some versions nest an extra folder; search defensively.
            for root, dirs, files in os.walk(tmp):
                if any(f in cls.STEM_MAP for f in files):
                    model_dir = root
                    break

        # Load source once for the residual / target SR.
        if source_path.lower().endswith('.flac'):
            import soundfile as sf
            info = sf.info(source_path)
            sr = info.samplerate
        else:
            with wave.open(source_path, "rb") as w:
                sr = w.getframerate()
        src_mono = cls._load_wav_mono(source_path, target_sr=sr)

        stems_written = {}
        summed = np.zeros_like(src_mono)
        for demucs_name, out_name in cls.STEM_MAP.items():
            stem_path = os.path.join(model_dir, demucs_name + ".wav")
            if not os.path.exists(stem_path):
                print(f"[demucs] missing expected stem {demucs_name}; skipping")
                continue
            audio = cls._load_wav_mono(stem_path, target_sr=sr)
            # Align lengths to source.
            if len(audio) > len(src_mono):
                audio = audio[:len(src_mono)]
            elif len(audio) < len(src_mono):
                audio = np.pad(audio, (0, len(src_mono) - len(audio)))
            out_path = os.path.join(output_dir, out_name + ".wav")
            cls._write_wav_mono(out_path, audio, sr)
            stems_written[out_name] = os.path.basename(out_path)
            summed += audio

        # Derived noise residual.
        if "vocals" in stems_written:
            noise = src_mono - summed
            out_path = os.path.join(output_dir, "noise.wav")
            cls._write_wav_mono(out_path, noise, sr)
            stems_written["noise"] = os.path.basename(out_path)

        # Clean up the temp Demucs output tree (models are cached elsewhere).
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

        if not stems_written:
            raise RuntimeError("demucs produced no stems")
        return stems_written


class FrequencyBandBackend(StemBackend):
    """Instant, RAM-free split into the requested analysis bands via FFT.

    This is a frequency-routed approximation (NOT true source separation) so it
    runs on the CPU-only mini PC without loading ML models. It routes energy
    into the bands the user asked for:

    * ``bass``   — 20 Hz – 250 Hz        (bass guitar, kick fundamentals)
    * ``vocals`` — 250 Hz – 4 kHz        (vocal fundamentals + formants)
    * ``guitar`` — 80 Hz – 1.2 kHz       (guitar body/fundamentals; overlaps
                                          bass at the low end by design so the
                                          instrument's body isn't lost)
    * ``drums``  — 4 kHz – Nyquist       (cymbals, hats, snare crack)
    * ``noise``  — residual = source - sum(bands). Coarse artifact/breath/
                                          room-noise channel for the noise-gate.

    Bands overlap (guitar/bass, vocals/drums edges) so no musical energy is
    silently dropped. All stems are 16-bit mono WAV at the source rate.
    """

    BANDS = [
        ("bass",   20.0,   250.0),
        ("vocals", 250.0, 4000.0),
        ("guitar",  80.0, 1200.0),
        ("drums", 4000.0,   None),
    ]

    @classmethod
    def extract(cls, source_path, output_dir):
        with wave.open(source_path, "rb") as src:
            sr = src.getframerate()
            nframes = src.getnframes()
            nch = src.getnchannels()
            sw = src.getsampwidth()
            raw = src.readframes(nframes)

        if nch > 1:
            # Downmix to mono by averaging channels
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
            samples = samples.reshape(-1, nch).mean(axis=1)
        else:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)

        n = len(samples)
        # Hann window FFT for better band separation
        window = np.hanning(n)
        windowed = samples * window
        spectrum = np.fft.rfft(windowed)
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)

        # Zero out everything outside each band, inverse FFT, overlap-add.
        reconstructed = np.zeros(n, dtype=np.float64)
        band_spectra = {}

        for name, f_lo, f_hi in cls.BANDS:
            mask = np.ones(len(spectrum), dtype=bool)
            mask &= freqs >= f_lo
            if f_hi is not None:
                mask &= freqs <= f_hi
            band_spec = np.zeros_like(spectrum)
            band_spec[mask] = spectrum[mask]
            band_spectra[name] = band_spec
            reconstructed += np.fft.irfft(band_spec, n=n)

        # Residual = original - sum(bands); a coarse noise/artifact channel.
        residual = samples - reconstructed

        # Write stems
        stems_written = {}
        all_bands = cls.BANDS + [("noise", None, None)]
        for name, _, _ in all_bands:
            if name == "noise":
                band_samples = residual
            else:
                band_samples = np.fft.irfft(band_spectra[name], n=n)
            # Re-normalize after windowing
            band_samples = band_samples / (np.mean(window) + 1e-9)
            band_samples = np.clip(band_samples, -32768, 32767).astype(np.int16)
            out_path = os.path.join(output_dir, f"{name}.wav")
            with wave.open(out_path, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(sr)
                out.writeframes(band_samples.tobytes())
            stems_written[name] = os.path.basename(out_path)

        return stems_written

#!/usr/bin/env python3
"""Real stem extraction backend using audio-separator.

Requires the ``audio-separator`` package and its model files. CPU-only is
supported via ONNX Runtime; GPU will be used automatically if available.
"""

import os
import sys
import gc
import glob
import threading
import logging

# Ensure the audio-separator venv site-packages is importable when this module
# is loaded from the main server venv.
_AS_VENV = os.path.join(os.path.dirname(__file__), '..', 'audio-separator-env', 'lib', 'python3.12', 'site-packages')
if os.path.isdir(_AS_VENV) and _AS_VENV not in sys.path:
    sys.path.insert(0, _AS_VENV)

from audio_separator.separator import Separator


class AudioSeparatorBackend:
    """Extract stems using the audio-separator package.

    Uses a single high-quality model by default. The preset determines which
    output stem is returned as the "primary" stem.
    """

    PRESETS = {
        'instrumental_clean': {
            'description': 'Cleanest instrumentals with minimal vocal bleed',
            'model': 'UVR-MDX-NET-Inst_HQ_4.onnx',
            'primary_stem': 'instrumental',
        },
        'instrumental_full': {
            'description': 'Maximum instrument preservation',
            'model': 'UVR-MDX-NET-Inst_HQ_3.onnx',
            'primary_stem': 'instrumental',
        },
        'vocal_clean': {
            'description': 'Minimal instrument bleed in vocals',
            'model': 'UVR_MDXNET_Main.onnx',
            'primary_stem': 'vocals',
        },
        'vocal_full': {
            'description': 'Maximum vocal capture including harmonies',
            'model': 'UVR-MDX-NET-Voc_FT.onnx',
            'primary_stem': 'vocals',
        },
        'karaoke': {
            'description': 'Lead vocal removal',
            'model': 'UVR_MDXNET_KARA.onnx',
            'primary_stem': 'instrumental',
        },
    }
    DEFAULT_PRESET = 'instrumental_clean'
    DEFAULT_MODEL = 'model_bs_roformer_ep_317_sdr_12.9755.ckpt'

    @classmethod
    def extract(cls, source_path, output_dir, preset=None, progress_callback=None):
        """Run audio-separator on ``source_path`` and write stems to ``output_dir``.

        Returns dict of stem_name -> filename, or None on failure.
        """
        if preset is None:
            preset = cls.DEFAULT_PRESET
        preset_cfg = cls.PRESETS.get(preset, cls.PRESETS[cls.DEFAULT_PRESET])

        os.makedirs(output_dir, exist_ok=True)

        base = os.path.splitext(os.path.basename(source_path))[0]
        model_filename = preset_cfg['model']

        def _cb(progress):
            if progress_callback is not None:
                try:
                    progress_callback(progress)
                except Exception as e:
                    print(f"[audio-separator] progress_callback error: {e}")

        separator = None
        try:
            print(f"[audio-separator] creating Separator for {source_path} "
                  f"preset={preset} model={model_filename}")
            separator = Separator(
                output_dir=output_dir,
                output_format='WAV',
                log_level=logging.ERROR,
            )
            # Ensure model files are present
            print(f"[audio-separator] downloading model {model_filename}")
            separator.download_model_and_data(model_filename)
            print(f"[audio-separator] loading model {model_filename}")
            separator.load_model(model_filename)
            print(f"[audio-separator] separating {source_path}")
            result = separator.separate(source_path)
            print(f"[audio-separator] separation complete result={result}")
        except Exception as e:
            import traceback
            print(f"[audio-separator] separation failed: {e}")
            traceback.print_exc()
            return None
        finally:
            # Aggressively release the model + ONNX/Torch session so RAM is freed
            # between extractions. audio-separator keeps the loaded model alive in
            # the instance; we drop our reference and reclaim memory immediately so
            # the next segment (or the rest of the system) isn't starved. The
            # attribute layout varies across audio-separator versions, so we scrub
            # any attribute that looks like a model/session/tensor holder.
            if separator is not None:
                for _attr in ('model_instance', 'model', 'ort_session', 'session',
                              'separator_model', 'model_path'):
                    _val = getattr(separator, _attr, None)
                    if _val is None:
                        continue
                    # Drill one level down for nested session handles.
                    for _sub in ('session', 'ort_session', 'model', 'inference_session'):
                        _inner = getattr(_val, _sub, None)
                        if _inner is not None:
                            try:
                                delattr(_val, _sub)
                            except Exception:
                                pass
                    try:
                        delattr(separator, _attr)
                    except Exception:
                        pass
                del separator
            # Clear any cached module-level model handles if present.
            try:
                _mod = sys.modules.get('audio_separator')
                if _mod is not None:
                    for _cache_attr in ('_model_cache', 'model_cache', '_session_cache'):
                        if hasattr(_mod, _cache_attr):
                            try:
                                setattr(_mod, _cache_attr, {})
                            except Exception:
                                pass
            except Exception:
                pass
            # Reclaim freed memory back to the OS / GPU.
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            print(f"[audio-separator] model unloaded, RAM reclaimed")

        # Collect outputs
        stems = {}
        for f in os.listdir(output_dir):
            if f.startswith(base) and f.endswith('.wav') and f != base + '.wav':
                stem_name = f[len(base) + 1:-4]  # strip prefix and .wav
                if not stem_name:
                    stem_name = 'output'
                stems[stem_name] = f

        if not stems:
            fallback = os.path.join(output_dir, base + '.wav')
            if os.path.exists(fallback):
                stem_name = preset_cfg.get('primary_stem', 'output')
                stems[stem_name] = base + '.wav'

        return stems if stems else None

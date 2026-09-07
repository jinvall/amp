#!/usr/bin/env python3
"""Multi-instrument stem extraction backend using Spleeter.

Provides 2-stem, 4-stem, and 5-stem separation with low memory footprint.
Model sizes: 40MB (2-stem), 100MB (4-stem), 130MB (5-stem)
Memory usage: 300-800MB during inference
"""

import os
import shutil
import logging

# Configure logging for Spleeter
logging.getLogger('spleeter').setLevel(logging.WARNING)

class SpleeterMultiBackend:
    """Extract multiple instrument stems using Spleeter."""
    
    PRESETS = {
        'vocals_instrumental': {
            'description': 'Vocals vs. instrumental accompaniment',
            'spleeter_model': 'spleeter:2stems',
            'stems': ['vocals', 'accompaniment'],
            'expected_memory_mb': 300,
        },
        'four_stems': {
            'description': 'Vocals, drums, bass, and other instruments',
            'spleeter_model': 'spleeter:4stems',
            'stems': ['vocals', 'drums', 'bass', 'other'],
            'expected_memory_mb': 500,
        },
        'five_stems': {
            'description': 'Vocals, drums, bass, piano, and other instruments',
            'spleeter_model': 'spleeter:5stems',
            'stems': ['vocals', 'drums', 'bass', 'piano', 'other'],
            'expected_memory_mb': 800,
        }
    }
    
    DEFAULT_PRESET = 'four_stems'
    
    @classmethod
    def extract(cls, source_path, output_dir, preset=None, progress_callback=None):
        """Run Spleeter on source_path and write stems to output_dir.
        
        Returns dict of stem_name -> filename, or None on failure.
        """
        try:
            from spleeter.separator import Separator
        except ImportError:
            print("[spleeter] ERROR: Spleeter not installed. Run: pip install spleeter")
            return None
        
        if preset is None:
            preset = cls.DEFAULT_PRESET
        
        preset_cfg = cls.PRESETS.get(preset, cls.PRESETS[cls.DEFAULT_PRESET])
        
        os.makedirs(output_dir, exist_ok=True)
        
        base = os.path.splitext(os.path.basename(source_path))[0]
        spleeter_model = preset_cfg['spleeter_model']
        expected_stems = preset_cfg['stems']
        
        print(f"[spleeter] starting separation for {source_path}")
        print(f"[spleeter] preset={preset}, model={spleeter_model}")
        print(f"[spleeter] expected stems: {expected_stems}")
        
        try:
            # Initialize separator
            separator = Separator(spleeter_model)
            
            # Run separation
            separator.separate_to_file(
                source_path,
                output_dir,
                filename_format='{instrument}.{codec}',
                codec='wav'
            )
            
            # Spleeter creates subdirectory structure
            # output_dir/base/vocals.wav, output_dir/base/drums.wav, etc.
            spleeter_output_dir = os.path.join(output_dir, base)
            
            # Collect and organize stems
            stems = {}
            if os.path.exists(spleeter_output_dir):
                for stem in expected_stems:
                    stem_file = os.path.join(spleeter_output_dir, f"{stem}.wav")
                    if os.path.exists(stem_file):
                        # Move to main output directory
                        dest_file = os.path.join(output_dir, f"{stem}.wav")
                        shutil.move(stem_file, dest_file)
                        stems[stem] = f"{stem}.wav"
                
                # Clean up empty directory
                try:
                    shutil.rmtree(spleeter_output_dir)
                except:
                    pass
            
            # Handle case where Spleeter outputs differently
            if not stems:
                # Try direct file scan
                for f in os.listdir(output_dir):
                    if f.endswith('.wav'):
                        stem_name = f.replace('.wav', '')
                        stems[stem_name] = f
            
            print(f"[spleeter] separation complete. Found stems: {list(stems.keys())}")
            return stems
            
        except Exception as e:
            import traceback
            print(f"[spleeter] separation failed: {e}")
            traceback.print_exc()
            return None
    
    @classmethod
    def get_preset_info(cls, preset=None):
        """Get information about a specific preset or all presets."""
        if preset:
            return cls.PRESETS.get(preset, {})
        return cls.PRESETS
    
    @classmethod
    def estimate_memory_usage(cls, preset=None):
        """Estimate memory usage for a given preset."""
        if preset is None:
            preset = cls.DEFAULT_PRESET
        preset_cfg = cls.PRESETS.get(preset, cls.PRESETS[cls.DEFAULT_PRESET])
        return preset_cfg.get('expected_memory_mb', 500)


# Quick test function
def test_spleeter():
    """Quick test to verify Spleeter is working."""
    try:
        from spleeter.separator import Separator
        print("✓ Spleeter is installed and importable")
        print("Available models:")
        print("  - spleeter:2stems (vocals/instrumental, ~40MB)")
        print("  - spleeter:4stems (vocals/drums/bass/other, ~100MB)")
        print("  - spleeter:5stems (vocals/drums/bass/piano/other, ~130MB)")
        return True
    except ImportError:
        print("✗ Spleeter not installed. Install with: pip install spleeter")
        return False


if __name__ == "__main__":
    test_spleeter()

# Multi-Instrument Audio Separation Analysis for AMP

## The REAL Requirement: Semantic Separation

You need to separate audio into **semantically meaningful stems**:
- **Vocals** (lead, harmonies, backing)
- **Drums** (kick, snare, cymbals, percussion)
- **Bass** (bass guitar, synth bass, low-frequency instruments)
- **Instruments** (guitar, piano, strings, brass, etc.)
- **Harmonies** (vocal harmonies, backing vocals)

## Current Capabilities vs. Requirements

| Separation Type | Current Backend | Quality | What It Actually Does |
|----------------|----------------|---------|---------------------|
| **Frequency-Based** | `frequency_band` | Basic | Splits by frequency: low/mid/high/residual |
| **Vocal/Instrumental** | `audio_separator` | Excellent | 2-stem: vocals vs. everything else |
| **Multi-Instrument** | ❌ **Missing** | ❌ **Needed** | 4-5 stems: vocals, drums, bass, other, piano |

## Multi-Instrument Separation Options

### **1. UVR-MDX-NET Family (Your Current ONNX)**
- **Models**: Single-purpose (vocal vs. instrumental)
- **Quality**: Excellent but only 2-stem separation
- **Size**: 3GB each, OOM risk when combining multiple models
- **Multi-instrument capability**: ❌ No (need separate models)

### **2. Spleeter (Deezer) - 4/5 Stems**
- **Stems**: `vocals`, `drums`, `bass`, `other` (4-stem)  
- **Stems**: `vocals`, `drums`, `bass`, `piano`, `other` (5-stem)
- **Size**: 100-130MB
- **Quality**: Good (industry standard)
- **Multi-instrument**: ✅ Yes (native 4/5 stems)

### **3. Demucs v4 (Facebook Research)**
- **Stems**: `drums`, `bass`, `vocals`, `other`
- **Size**: 400MB
- **Quality**: Very Good
- **Multi-instrument**: ✅ Yes (4 stems by default)

### **4. Open-Unmix (Multi-Target)**
- **Stems**: Separate models for each: `vocals`, `drums`, `bass`, `other`
- **Size**: 50MB each (200MB total)
- **Quality**: Good (research standard)
- **Multi-instrument**: ✅ Yes (composable)

## Memory Usage Analysis for Multi-Instrument Separation

| Approach | Total RAM | Models | Inference RAM | Setup |
|----------|-----------|--------|---------------|--------|
| **UVR Models (stacked)** | 8-10GB | 4x 2GB ONNX | 2GB per run | Multiple model loads |
| **Spleeter 5-stem** | 500-800MB | Single 130MB | 300-500MB | One model, multiple outputs |
| **Demucs 4-stem** | 1-1.5GB | Single 400MB | 600-800MB | One model, multiple outputs |
| **Open-Unmix** | 300-500MB | 4x 50MB | 100-200MB | Load only needed models |

## The Crucial Insight: Semantic vs. Frequency Separation

```python
# What you HAVE (frequency band)
low_freq.wav    # bass + kick drums + low instruments
mid_freq.wav    # vocals + guitars + piano
high_freq.wav   # cymbals + hi-hat + vocal sibilance
residual.wav    # noise + artifacts

# What you NEED (semantic)
vocals.wav      # lead + harmonies
drums.wav       # kick, snare, cymbals, percussion  
bass.wav        # bass guitar, synth bass
guitar.wav      # electric/acoustic guitars
piano.wav       # piano, keyboards
other.wav       # strings, brass, etc.
```

## Recommended Implementation Path

### **Phase 1: Add Spleeter 5-stem (130MB)**

```python
# spleeter_5stem_backend.py
from spleeter.separator import Separator

class Spleeter5StemBackend:
    """Extract 5 stems using Spleeter."""
    
    STEMS = ['vocals', 'drums', 'bass', 'piano', 'other']
    
    @classmethod
    def extract(cls, source_path, output_dir):
        separator = Separator('spleeter:5stems')
        separator.separate_to_file(source_path, output_dir)
        return {stem: f"{stem}.wav" for stem in cls.STEMS}
```

### **Phase 2: Smart Model Selection**

```python
# Intelligent backend selection based on requirements
def select_backend(separation_need, available_memory):
    if separation_need == "basic_frequency":
        return "frequency_band"
    elif separation_need == "vocal_only":
        return "audio_separator"  # Only if memory > 4GB
    elif separation_need == "multi_instrument":
        if available_memory > 1000:  # 1GB
            return "spleeter_5stem"
        else:
            return "frequency_band"  # Fallback
```

### **Phase 3: Hybrid Approach for Quality + Speed**

```python
# Two-pass separation for better results
def hybrid_separation(source_path, output_dir):
    # Pass 1: Extract vocals with high-quality model
    vocal_model = Separator('spleeter:2stems')
    vocal_model.separate_to_file(source_path, output_dir + '_vocal')
    
    # Pass 2: Extract instruments from instrumental track
    instrumental = load_instrumental(output_dir + '_vocal/accompaniment.wav')
    instrument_model = Separator('spleeter:4stems')
    instrument_model.separate_to_file(instrumental, output_dir + '_instruments')
    
    return combine_results(...)
```

## Implementation Priority

1. **Spleeter 4-stem** (100MB) - Immediate solution
2. **Memory monitoring** - Prevent OOM
3. **Model caching** - Reuse loaded models
4. **GPU acceleration** - If available
5. **Quantized models** - Reduce size further

## Sample Configuration for AMP

```json
{
  "stem_backend": "spleeter_4stem",
  "available_memory_mb": 2048,
  "max_stems": 4,
  "stems": ["vocals", "drums", "bass", "other"],
  "quality_preset": "balanced",  // balanced, quality, speed
  "use_gpu": false,
  "cache_models": true
}
```

## The Bottom Line

**You're absolutely right** - basic frequency separation doesn't give you semantic instrument tracking. But the good news is:

✅ **You CAN get multi-instrument separation**
✅ **You DON'T need 3GB models**  
✅ **You WON'T get OOM crashes** with proper lightweight models

**Recommended path:** Start with Spleeter 4-stem (100MB) which gives you vocals, drums, bass, and other instruments. This provides semantic separation at a tiny fraction of the memory cost.


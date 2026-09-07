# Lightweight Audio Separation Alternatives for AMP

## Current Situation Analysis

**Current Stack:**
- `frequency_band`: ✅ Active (lightweight FFT-based, ~50MB RAM)
- `audio_separator`: ❌ Available but disabled (3GB ONNX models → OOM risk)

**Problem:** Large ONNX models (3GB+) cause OOM when loaded into RAM.

---

## Lighter Alternatives (Sorted by Memory Footprint)

### **1. Spleeter (Deezer) - Recommended**
- **Size:** 40-130MB (quantized)
- **Quality:** Good (industry standard)
- **RAM:** 300-800MB
- **Speed:** Very fast
- **Format:** TensorFlow SavedModel

**Installation:**
```bash
pip install spleeter
```

**Usage:**
```python
from spleeter.separator import Separator

separator = Separator('spleeter:2stems')  # vocals/accompaniment
separator.separate_to_file('input.mp3', 'output_dir/')
```

### **2. Demucs v4 (Facebook Research)**
- **Size:** 400MB
- **Quality:** Very Good
- **RAM:** 1-2GB  
- **Speed:** Medium
- **Format:** PyTorch

**Installation:**
```bash
pip install demucs
```

**Usage:**
```python
from demucs import separate
separate.main(["--two-stems", "vocals", "input.mp3", "-o", "output_dir/"])
```

### **3. Open-Unmix**
- **Size:** 50MB per source
- **Quality:** Good (research standard)
- **RAM:** 300-500MB
- **Speed:** Fast
- **Format:** PyTorch

**Installation:**
```bash
pip install openunmix
```

**Usage:**
```python
import openunmix
import torchaudio

# Load model for specific target
model = openunmix.umxl()
audio, rate = torchaudio.load('input.wav')
sources = model(audio)
```

### **4. Hybrid Approach: Light ONNX Models**
- **Size:** 50-200MB (quantized/pruned)
- **Quality:** Good
- **RAM:** 300-1000MB
- **Speed:** Medium
- **Format:** ONNX (but optimized)

**Example:** Create pruned versions of UVR models using:
```python
# Pseudo-code for model optimization
import onnx
import onnxruntime

# Load large model
model = onnx.load("UVR-MDX-NET-Inst_HQ_4.onnx")
# Apply optimization
optimized_model = onnxoptimizer.optimize(model)
# Save smaller version
onnx.save(optimized_model, "UVR-MDX-NET-Inst_HQ_4_pruned.onnx")
```

---

## Implementation Strategy for AMP

### **Option A: Add Spleeter Backend (Recommended)**

```python
# /home/jason/amp/server/spleeter_backend.py
#!/usr/bin/env python3
"""Spleeter backend for lightweight stem extraction."""

import os
import shutil
from spleeter.separator import Separator

class SpleeterBackend:
    """Extract stems using Spleeter (lightweight, fast)."""
    
    PRESETS = {
        '2stems': 'vocals/accompaniment',
        '4stems': 'vocals/drums/bass/other',
        '5stems': 'vocals/drums/bass/piano/other'
    }
    
    @classmethod
    def extract(cls, source_path, output_dir, preset='2stems'):
        os.makedirs(output_dir, exist_ok=True)
        
        # Initialize separator
        separator = Separator(f'spleeter:{preset}')
        
        # Extract stems
        separator.separate_to_file(source_path, output_dir)
        
        # Rename and organize files
        base = os.path.splitext(os.path.basename(source_path))[0]
        stems = {}
        
        # Spleeter outputs: output_dir/base/vocals.wav, etc.
        spleeter_dir = os.path.join(output_dir, base)
        if os.path.exists(spleeter_dir):
            for f in os.listdir(spleeter_dir):
                if f.endswith('.wav'):
                    stem_name = f.replace('.wav', '')
                    stems[stem_name] = f
                    # Move file to root output_dir
                    src = os.path.join(spleeter_dir, f)
                    dst = os.path.join(output_dir, f)
                    shutil.move(src, dst)
            
            # Clean up empty directory
            shutil.rmtree(spleeter_dir, ignore_errors=True)
        
        return stems if stems else None
```

### **Option B: Hybrid Frequency + ML Approach**

```python
# Smart selection based on available memory
def smart_stem_extraction(source_path, output_dir, max_memory_mb=2048):
    import psutil
    
    available_memory = psutil.virtual_memory().available / (1024 * 1024)
    
    if available_memory > max_memory_mb:
        # Use Spleeter if enough memory
        from spleeter_backend import SpleeterBackend
        return SpleeterBackend.extract(source_path, output_dir)
    else:
        # Fallback to frequency band
        from stem_backends import FrequencyBandBackend
        return FrequencyBandBackend.extract(source_path, output_dir)
```

---

## Memory Usage Comparison

| Backend | Model Size | RAM Required | Quality | Speed | Best For |
|---------|------------|--------------|---------|-------|----------|
| **FrequencyBand** | 0MB | 50-100MB | Basic | ⚡ Fast | Real-time, low-resource |
| **Spleeter** | 40-130MB | 300-800MB | Good | Fast | Quick separation, multi-track |
| **Demucs** | 400MB | 1-2GB | Very Good | Medium | High quality offline |
| **Open-Unmix** | 50MB | 300-500MB | Good | Fast | Streaming, research |
| **UVR-MDX (ONNX)** | 3GB | 3-5GB | Excellent | Slow | Professional offline work |

---

## Recommended Action Plan

### **Phase 1: Immediate Fix (1-2 hours)**
1. **Keep current setup**: `frequency_band` works fine for basic separation
2. **Add memory monitoring**: Log RAM usage around extraction
3. **Create OOM prevention**: Halt separation if memory < 1GB available

### **Phase 2: Add Lightweight ML (1 day)**
1. **Install Spleeter**: `pip install spleeter` in venv
2. **Implement SpleeterBackend**: Add to stem_manager.py
3. **Test with 2GB memory limit**: Ensure no OOM
4. **Update config.json**: Add `"stem_backend": "spleeter"` option

### **Phase 3: Production Optimization (3-5 days)**
1. **Model quantization**: Convert to INT8 for 4x size reduction
2. **On-demand loading**: Load models per-request, not at startup
3. **Memory pooling**: Reuse inference sessions across requests
4. **GPU acceleration**: If available, reduce RAM usage

---

## Quick Start: Add Spleeter Now

```bash
# In your amp server venv
cd /home/jason/amp/server
source venv/bin/activate
pip install spleeter

# Test Spleeter directly
python -c "
from spleeter.separator import Separator
separator = Separator('spleeter:2stems')
print('Spleeter installed successfully')
"
```

Then add the `SpleeterBackend` class shown above and integrate it into `stem_manager.py` alongside the existing backends.

---

## Why Not YOLO?

**YOLO (You Only Look Once)** is for **computer vision**:
- Detects objects in images (cars, people, etc.)
- Operates on 2D spatial data
- Uses convolutional neural networks for visual patterns

**Audio Separation** needs:
- Processes 1D time-series or 2D spectrograms
- Understands frequency domain features
- Uses different neural architectures (RNNs, Transformers for audio)

**Analogy:** Asking YOLO for audio separation is like asking a car mechanic to fix your computer - wrong domain expertise!

---

## Summary

✅ **Best immediate choice**: Stick with `frequency_band` (it's already working)  
✅ **Best upgrade path**: Add `spleeter` backend (40MB, good quality)  
✅ **Best performance**: Demucs if you need higher quality  
❌ **Avoid**: Large ONNX models unless you have 8GB+ RAM dedicated  

The key insight: **You don't need 3GB models for good audio separation**. Modern lightweight models (40-400MB) provide 90% of the quality with 10% of the memory cost.


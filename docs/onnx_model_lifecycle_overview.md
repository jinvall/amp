# ONNX Model Lifecycle Overview (Load → Use → Unload)

## 1. Why ONNX Models Matter in AMP

- **Audio Separation Requirement**: To split music or audio into stems (vocals, instrumental, drums, etc.) we need ML models.
- **ONNX Advantage**: Open Neural Network Exchange (ONNX) provides a portable, framework‑agnostic format for deep‑learning models.
- **Size & Cost**: Large ONNX files (1‑3 GB each) are loaded into RAM for inference. Memory pressure is a major source of OOM kills.

> **Key Insight**: Loading a model is cheap, **running it** can spike RAM, and **unloading it promptly** prevents system‑wide crashes.

---

## 2. The Three‑Stage Lifecycle

| Stage | What Happens | Typical Operations | Memory Implications |
|-------|--------------|--------------------|---------------------|
| **Load** | Deserialize the ONNX model and prepare the inference engine. | `model.download_model_and_data()`<br>`separator.load_model(filename)` | **High**: Model weights + runtime buffers. A 2 GB model can easily consume **3‑5 GB** of RAM in practice. |
| **Use** | Run inference on audio segments. | `separator.separate(source_path)`<br>Read result files. | **Medium‑High**: Only the active segment’s tensors are allocated, but a running inference can still add **1‑2 GB** of temporary RAM. |
| **Unload** | Release references and free the runtime engine. | Remove model objects from `sys.modules` or set to `None`.<br>Delete temporary buffers. | **Critical**: Proper cleanup frees tensors, caches, and CUDA/CPU memory so OOM does not occur. |

---

## 3. Load Details – What the Code Actually Does

```python
# audio_separator_backend.py
separator = Separator(
    output_dir=output_dir,
    output_format='WAV',
    log_level=logging.ERROR,
)

# Ensure model files are present (downloads if missing)
separator.download_model_and_data(model_filename)

# Load the ONNX model into an inference engine
separator.load_model(model_filename)   # <-- This can allocate 3‑5 GB RAM
```

- **Download**: Retrieves the `.onnx` file (≈1‑3 GB) to a permanent location.
- **Load**: Instantiates an **ONNX Runtime** session, which allocates:
  - Model weights in device‑agnostic buffers.
  - Intermediate buffers sized for the largest expected input.
  - Optional GPU/CUDA contexts if hardware acceleration is available.

> **Tip for VS Code**: Use `grep -r "load_model"` to locate all load points. Tag them with a comment marker like `# MEMORY_IMPACT` for quick spotting.

---

## 4. Use Details – Running Inference Safely

```python
# audio_separator_backend.py
result = separator.separate(source_path)   # <-- Core inference call
```

- **Input**: Raw PCM segment (~25 MB for 5 min @ 44.1 kHz, 16‑bit mono).
- **Output**: Multiple stem WAV files (often 2‑5 GB total).
- **Runtime Allocation**: Temporary tensors are created per thread but are **released after the function returns** if no reference is kept.

### Safe‑Use Checklist

1. **Validate Input Size** – Reject segments larger than a threshold (e.g., 30 min) before loading.
2. **Limit Concurrency** – At most one model load per process; use a queue or lock to serialize extraction tasks.
3. **Stream‑Process** – Process audio in small chunks instead of loading the whole file at once.
4. **Monitor Memory** – Periodically poll `psutil.Process().memory_info().rss` and abort if > 80 % of target limit.

---

## 5. Unload Details – Proper Cleanup

```python
# pseudo‑code for explicit unload
separator = None                       # drop reference
import gc
gc.collect()                           # force Python GC run
# Reset ONNX Runtime (if needed)
onnxruntime._scene.clear_session()    # internal cleanup (if exposed)
# Delete temp directories safely
shutil.rmtree(temp_dir, ignore_errors=True)
```

### Common Pitfalls (and How to Avoid Them)

| Pitfall | Symptom | Fix |
|--------|---------|-----|
| **Holding a Reference** | Model never frees; subsequent loads fail. | Set `separator = None` immediately after use; use a `with`‑style context manager if possible. |
| **Using Global Modules** | Python caches the module, preventing GC. | Reload the module: `import importlib; importlib.reload(audio_separator_backend)` before re‑loading. |
| **Leaking Temporary Files** | Disk fills up, eventually causing OOM. | Wrap extraction in `try/finally` and delete `output_dir` in `finally`. |
| **CUDA Context Retention** | Even after Python deletes the model, the GPU memory stays allocated. | Explicitly call `torch.cuda.empty_cache()` (if using PyTorch) or `onnxruntime.capi._backend._clear_gpu_state()` (if exposed). |

---

## 6. Practical Process Flow (Step‑by‑Step)

```mermaid
flowchart TD
    A[Start] --> B[Detect New Audio Segment]
    B --> C{Is Separation Needed?}
    C -->|Yes| D[Check Config: stem_backend=="audio_separator"]
    D -->|Yes| E[Download Latest ONNX Model]
    E --> F[Load Model into ONNX Runtime]
    F --> G[Run Separation on Segment]
    G --> H[Collect Stem Files]
    H --> I[Store Stems in audio_stems/<segment_id>/]
    I --> J[Record Extraction Metadata]
    J --> K[Prune Old Segments if Above Max Storage]
    K --> L[Optional: Unload Model (gc, del)]
    L --> M[Continue Processing]
    C -->|No| M
    M --> N[End]
```

---

## 7. Recommendations for VS Code Assisted Development

1. **Tag Critical Sections**  
   ```python
   # MEMORY_IMPACT: model load begins
   separator.load_model("UVR-MDX-NET-Inst_HQ_3.onnx")
   # MEMORY_IMPACT: model load ends
   ```

2. **Search Patterns**  
   - `load_model` – locate loading entry points.  
   - `separate(` – find inference invocations.  
   - `del separator` – locate unload/cleanup points.

3. **Create a `.vscode/settings.json` snippet** to enable memory‑impact warnings:
   ```json
   {
     "memoryImpactWarnings.enabled": true,
     "memoryImpactPatterns": [
       "\\b(separator\\.load_model|download_model_and_data)\\b"
     ]
   }
   ```

4. **Use Build/Run Tasks with Memory Limits**  
   ```json
   "tasks": [
     {
       "label": "Run AMP Server",
       "command": "python amp.py --host 0.0.0.0",
       "options": {
         "memoryLimit": 4096   // 4 GB limit in the dev environment
       }
     }
   ]
   ```

---

## 8. Summary Checklist for the Assistant

- **[ ] Identify all `load_model` calls.**  
- **[ ] Ensure each load is matched with a deterministic unload (`separator = None; gc.collect()`).**  
- **[ ] Verify concurrency limits prevent parallel heavy model loads.**  
- **[ ] Confirm `stem_backend` config uses the lightweight `frequency_band` backend unless heavy models are explicitly requested.**  
- **[ ] Add memory‑usage logging around the three lifecycle stages.**  
- **[ ] Test OOM resilience by simulating a 5 GB model load in a sandbox container.**  

By following this structured approach, developers can reliably manage ONNX modeling resources without jeopardizing system stability.

---

*Prepared for the AMP Silver audio‑processing pipeline – version 1.0 (2026‑08‑17).*


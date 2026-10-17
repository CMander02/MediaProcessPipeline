"""Finite acoustic comparison; reports disagreement, never inferred accuracy.

uv run python scripts/evaluate_speaker_clustering.py AUDIO --count N --output DIR
Each recording stays in one development split. Existing archives are read only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reuse", action="store_true", help="Compare the saved embeddings without running the audio models again")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    import numpy as np
    from sklearn.metrics import adjusted_rand_score
    from app.core import settings
    from app.services.recognition.diarization import DiarizationService
    from app.services.recognition.speaker_clustering import merge_to_known_count, assignment_confidence
    from pyannote.audio.pipelines.clustering import AgglomerativeClustering

    args.output.mkdir(parents=True, exist_ok=True)
    rt = settings.get_runtime_settings()
    settings._runtime_settings = rt.model_copy(update={
        "data_root": str(args.output / "runtime"), "diarization_clustering_strategy": "native",
        "diarization_clustering_threshold": None,
    })
    service = DiarizationService()
    started = time.perf_counter()
    try:
        if args.reuse:
            previous = json.loads((args.output / "comparison.json").read_text(encoding="utf-8"))
            if previous["audio"] != str(args.audio.resolve()):
                raise ValueError("Saved embeddings belong to a different recording")
            parameters, windows = previous["parameters"], previous["windows"]
            metric = previous.get("metric", "cosine")
            vectors = np.load(args.output / "embeddings.npy", allow_pickle=False)
        else:
            pipeline = service._ensure_pipeline()
            clustering = pipeline.clustering
            parameters = clustering.parameters(instantiated=True)
            metric = clustering.metric
            audio = service._load_audio(args.audio)
            duration = audio["waveform"].shape[-1] / audio["sample_rate"]
            captured = []
            original = clustering.cluster
            def capture(self, embeddings, *positional, **kwargs):
                captured.append(embeddings.copy())
                return original(embeddings, *positional, **kwargs)
            object.__setattr__(clustering, "cluster", types.MethodType(capture, clustering))
            windows = []
            for fraction in (0.1, 0.5, 0.85):
                start = max(0, min(duration - 60, duration * fraction))
                end = min(duration, start + 60)
                windows.append({"start": start, "end": end})
                rate = audio["sample_rate"]
                pipeline({"waveform": audio["waveform"][:, int(start * rate):int(end * rate)], "sample_rate": rate})
                print(f"Extracted window {start:.1f}–{end:.1f}s", flush=True)
            vectors = np.concatenate(captured)
            np.save(args.output / "embeddings.npy", vectors)
        def cluster(threshold, count=None):
            model = AgglomerativeClustering(metric=metric).instantiate({**parameters, "threshold": threshold})
            return model.cluster(vectors.copy(), count or 1, count or len(vectors), count)
        threshold = float(parameters["threshold"])
        baseline = cluster(threshold)
        native = cluster(threshold, args.count)
        candidates = {"native_fixed": native}
        for delta in (-0.04, 0, 0.04):
            candidates[f"unknown_threshold_{threshold + delta:.6f}"] = cluster(threshold + delta)
        for strategy in ("largest", "diverse", "kmeans++"):
            seeds = baseline if len(np.unique(baseline)) >= args.count else native
            candidates[strategy] = merge_to_known_count(vectors, seeds, args.count, strategy=strategy)
        rows = []
        for name, labels in candidates.items():
            confidence = assignment_confidence(vectors, labels)
            rows.append({"candidate": name, "count": len(np.unique(labels)),
                         "embedding_counts": np.unique(labels, return_counts=True)[1].tolist(),
                         "agreement_with_native_ari": float(adjusted_rand_score(native, labels)),
                         "uncertain_fraction_by_margin": {str(m): float(np.mean(confidence["margin"] < m)) for m in (0.02, 0.05, 0.1)}})
        result = {"audio": str(args.audio.resolve()), "split": "development", "windows": windows,
                  "parameters": parameters, "metric": metric, "requested_count": args.count, "embeddings": len(vectors),
                  "initial_centers": "threshold clusters when sufficient, otherwise native fixed-count clusters",
                  "reused_embeddings": args.reuse,
                  "elapsed_seconds": round(time.perf_counter() - started, 2), "candidates": rows,
                  "accuracy": None, "missed_speech": None, "false_speech": None, "short_voice_retention": None,
                  "limitation": "No human time-aligned labels. ARI is candidate agreement, not accuracy. No production parameter changes."}
        (args.output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        service.release()


if __name__ == "__main__":
    main()

"""
shared_face_engine/__init__.py

Single face engine (model loading, detection, embedding, quality scoring,
and now matching) shared by Trainer Desktop, Local Node, and the backend
(app.py). All three consumers must import only from this public API —
never reach into submodules directly — so model version, detection
thresholds, quality scoring, and match-decision logic can never drift
between training and recognition, wherever recognition happens to run.
"""

from __future__ import annotations
from shared_face_engine.spoof import detect_spoofing
from shared_face_engine.quality import (
    FaceQualityResult,
    assess_face_quality,
    is_good_enrollment_face,
    is_good_recognition_face,
)
from shared_face_engine.matching import (
    DEFAULT_MATCH_THRESHOLD,
    best_match,
    best_match_multi,
    closest_candidate,
    closest_candidate_multi,
    compare_embeddings,
    compute_aggregate_embedding,
    verify_against_vectors,
)

# model_loader.py imports insightface at module scope, and embedding.py
# imports model_loader — so together they're the only reason importing
# shared_face_engine at all would require insightface/onnxruntime to be
# installed. quality/spoof/matching only need cv2/numpy and stay eager
# above. Deployments that never run recognition (e.g. Railway's
# support-dashboard backend, which only needs compute_aggregate_embedding)
# no longer install insightface, so these two names are resolved lazily
# via PEP 562 module __getattr__ instead of imported eagerly here.
_LAZY_ATTRS = {
    "MODEL_NAME": "shared_face_engine.model_loader",
    "get_face_model": "shared_face_engine.model_loader",
    "is_gpu_enabled": "shared_face_engine.model_loader",
    "stage_models": "shared_face_engine.model_loader",
    "unload_model": "shared_face_engine.model_loader",
    "detect_and_extract": "shared_face_engine.embedding",
    "process_video": "shared_face_engine.embedding",
}


def __getattr__(name):
    module_path = _LAZY_ATTRS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value  # cache so repeat access skips __getattr__/import
    return value

__all__ = [
    "MODEL_NAME",
    "get_face_model",
    "is_gpu_enabled",
    "stage_models",
    "unload_model",
    "detect_and_extract",
    "process_video",
    "FaceQualityResult",
    "assess_face_quality",
    "is_good_enrollment_face",
    "is_good_recognition_face",
    "DEFAULT_MATCH_THRESHOLD",
    "best_match",
    "best_match_multi",
    "closest_candidate",
    "closest_candidate_multi",
    "compare_embeddings",
    "compute_aggregate_embedding",
    "verify_against_vectors",
    "detect_spoofing"
]
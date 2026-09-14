"""
local_node/enrollment.py

Local, offline equivalent of the cloud backend's face_processor.py
(process_enrollment_video + compute_aggregate_embedding). This is the piece
that genuinely does not exist on the node today: embeddings currently only
arrive here via a zip package built and exported by the cloud pipeline
(see package_import.py). For a fully offline install there is no cloud to
build that package, so enrollment has to run on the node itself.

Deliberately reuses recognition_engine.detect_and_extract — the exact
function the live-recognition path already calls on every camera frame —
rather than re-implementing detection. This is the same InsightFace model
instance, already warmed by NodeService.start() (see recognition_engine.
warmup()), so enrollment and live recognition can never silently drift onto
different model versions on the same machine.

Quality/spoof checks (assess_face_quality, detect_spoofing in the cloud's
face_processor.py) are NOT ported here — flagging as an open item, see
enroll_from_video's docstring below.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from local_node.recognition_engine import FaceEngineUnavailableError, detect_and_extract

logger = logging.getLogger(__name__)

MIN_USABLE_FRAMES = 8
MAX_SAMPLED_FRAMES = 60
MIN_DETECTION_CONFIDENCE = 0.5


class EnrollmentError(RuntimeError):
    pass


def _sample_frames(video_path: Path, max_frames: int = MAX_SAMPLED_FRAMES) -> list[np.ndarray]:
    """Evenly-spaced frame sampling across the clip, same strategy as the
    cloud's extract_frames_from_video — grabbing every frame from a 15s
    clip is wasted work when consecutive frames are near-duplicates."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise EnrollmentError(f"Could not open enrollment video: {video_path}")

    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            raise EnrollmentError("Enrollment video has no readable frames")

        step = max(1, total // max_frames)
        frames: list[np.ndarray] = []
        for frame_index in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if ok and frame is not None:
                frames.append(frame)
            if len(frames) >= max_frames:
                break
        return frames
    finally:
        cap.release()


def compute_aggregate_embedding(embeddings: list[np.ndarray]) -> np.ndarray:
    """L2-normalized mean of per-frame embeddings — matches the cloud's
    compute_aggregate_embedding exactly, so a person enrolled locally and
    a person enrolled via the cloud produce comparable embeddings at the
    same cosine-similarity threshold. Do not change this math without also
    updating the cloud version; they must stay bit-for-bit equivalent or
    the match threshold means different things in each mode."""
    stacked = np.stack(embeddings, axis=0)
    mean = stacked.mean(axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm > 0 else mean


def enroll_from_video(video_path: Path) -> dict:
    """Runs the full local enrollment pipeline on one uploaded video.

    Returns {"embeddings": list[list[float]], "frame_count": int,
    "usable_frame_count": int, "model_version": str}. Caller (the route
    handler) is responsible for calling local_db.upsert_person_embeddings
    with the result — this function has no DB dependency by design, so it
    stays independently testable against a video file.

    Raises EnrollmentError if too few usable frames were found (bad
    lighting, face not visible, etc.) — the route handler should surface
    this as a 400 with guidance to re-record, not a 500.

    OPEN ITEM: the cloud pipeline additionally runs assess_face_quality and
    detect_spoofing per frame (face_processor.py) before accepting a frame
    into the aggregate. This local version currently accepts any frame
    InsightFace returns a detection for. For a client relying entirely on
    local enrollment (no cloud fallback), porting those two checks here
    before go-live is worth prioritizing — happy to do that as the next
    piece once this shape is confirmed.
    """
    frames = _sample_frames(video_path)
    if not frames:
        raise EnrollmentError("No frames could be read from the enrollment video")

    per_frame_embeddings: list[np.ndarray] = []
    for frame in frames:
        try:
            detections = detect_and_extract(frame)
        except FaceEngineUnavailableError as exc:
            raise EnrollmentError(f"Face engine unavailable during enrollment: {exc}") from exc

        # Exactly one face expected per enrollment frame — a frame with 0
        # or 2+ faces is ambiguous (who is being enrolled?) and skipped
        # rather than guessed at.
        if len(detections) != 1:
            continue
        detection = detections[0]
        if detection.get("conf", 0.0) < MIN_DETECTION_CONFIDENCE:
            continue
        per_frame_embeddings.append(np.asarray(detection["embedding"], dtype=np.float32))

    if len(per_frame_embeddings) < MIN_USABLE_FRAMES:
        raise EnrollmentError(
            f"Only {len(per_frame_embeddings)} usable frames found "
            f"(need at least {MIN_USABLE_FRAMES}). Re-record with better "
            f"lighting and only one person in frame."
        )

    aggregate = compute_aggregate_embedding(per_frame_embeddings)
    return {
        "embeddings": [aggregate.tolist()],
        "frame_count": len(frames),
        "usable_frame_count": len(per_frame_embeddings),
        "model_version": "insightface-buffalo_l",
    }

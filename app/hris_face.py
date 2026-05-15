"""
GPA-ERP HRIS — Face verification engine (H2)

Uses deepface (FaceNet/ArcFace) for server-side face identity verification.
Client-side face-api.js handles liveness detection; this module does identity matching.

Usage:
    from app.hris_face import register_face, verify_face

    # Register (HR admin action, stored on Employee.face_embedding)
    embedding = register_face(image_bytes)          # returns list[float]

    # Verify clock-in selfie
    verified, confidence = verify_face(stored_embedding, selfie_bytes)
"""
from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# deepface is an optional heavy dependency — gracefully degrade if not installed
try:
    import numpy as np
    from deepface import DeepFace
    _DEEPFACE_AVAILABLE = True
except ImportError:
    _DEEPFACE_AVAILABLE = False
    logger.warning("deepface not installed — face verification disabled. Run: pip install deepface")


# Cosine similarity threshold for identity match (0.80 = 80% similar)
_THRESHOLD = 0.80
_MODEL_NAME = "Facenet"    # fast + accurate; alternatives: "ArcFace", "VGG-Face"


def _bytes_to_array(image_bytes: bytes):
    """Convert raw image bytes to a numpy array for deepface."""
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return np.array(img)


def register_face(image_bytes: bytes) -> list[float]:
    """
    Extract a face embedding from an image.
    Returns a list of floats (128-dim for FaceNet) to store in Employee.face_embedding.
    Raises ValueError if no face detected.
    """
    if not _DEEPFACE_AVAILABLE:
        raise RuntimeError("deepface not installed. Run: pip install deepface")

    img_array = _bytes_to_array(image_bytes)
    try:
        result = DeepFace.represent(
            img_path   = img_array,
            model_name = _MODEL_NAME,
            enforce_detection = True,
            detector_backend  = "opencv",
        )
        if not result:
            raise ValueError("No face detected in the image")
        # DeepFace.represent returns list of dicts; take first face
        embedding: list[float] = result[0]["embedding"]
        return embedding
    except Exception as e:
        raise ValueError(f"Face registration failed: {e}") from e


def verify_face(
    stored_embedding: list[float],
    selfie_bytes:     bytes,
) -> tuple[bool, float]:
    """
    Compare a stored embedding against a new selfie.
    Returns (verified: bool, confidence: float 0.0–1.0).
    """
    if not _DEEPFACE_AVAILABLE:
        logger.warning("deepface unavailable — skipping face verification")
        return False, 0.0

    if not stored_embedding:
        return False, 0.0

    try:
        import numpy as np

        selfie_array = _bytes_to_array(selfie_bytes)
        result = DeepFace.represent(
            img_path   = selfie_array,
            model_name = _MODEL_NAME,
            enforce_detection = True,
            detector_backend  = "opencv",
        )
        if not result:
            return False, 0.0

        selfie_embedding = np.array(result[0]["embedding"])
        stored           = np.array(stored_embedding)

        # Cosine similarity
        dot      = float(np.dot(stored, selfie_embedding))
        norm_a   = float(np.linalg.norm(stored))
        norm_b   = float(np.linalg.norm(selfie_embedding))
        if norm_a == 0 or norm_b == 0:
            return False, 0.0

        confidence = dot / (norm_a * norm_b)
        confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]
        verified   = confidence >= _THRESHOLD
        return verified, round(confidence, 3)

    except Exception as e:
        logger.warning(f"Face verification error: {e}")
        return False, 0.0

"""Local DINOv2 image embeddings. No downloads, credentials or cloud inference.

Weights are the ONNX Community conversion of Meta's Apache-2.0 DINOv2-S/14.
The model is a visual retrieval backbone, not a same-SKU classifier. Cosines and
embeddings must not be described as a probability that two goods are identical.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import threading
import warnings
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

try:
    import onnxruntime as ort
except ImportError:
    ort = None

MODEL_ID = "dinov2-small-fp32-08c606e-preprocess-v2"
MODEL_DIMENSION = 384
MODEL_SHA256 = "6266c3cd72db6953cecdcbfeab9422a9f783d96f1a4e296ba70ffbac43b54a18"
MODEL_FILENAME = "model.onnx"
_lock = threading.RLock()
_session = None
_selected_path: Path | None = None
_last_error: str | None = None
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)


def _default_path() -> Path:
    configured = os.environ.get("MIDOO_MODEL_DIR")
    if configured:
        base = Path(configured)
    else:
        if getattr(sys, "frozen", False):
            bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "models"
            if (bundled / "dinov2-small" / MODEL_FILENAME).is_file():
                return _resolve_path(bundled)
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parent
        base = base / "models"
    return _resolve_path(base)


def _resolve_path(path: Path | str) -> Path:
    base = Path(path)
    if base.suffix.lower() == ".onnx":
        return base.resolve()
    if (base / MODEL_FILENAME).is_file():
        return (base / MODEL_FILENAME).resolve()
    return (base / "dinov2-small" / MODEL_FILENAME).resolve()


def availability() -> dict:
    path = _selected_path or _default_path()
    return {
        "available": ort is not None and path.is_file() and _last_error is None,
        "loaded": _session is not None,
        "model": "DINOv2 Small · 本地视觉语义模型",
        "model_id": MODEL_ID,
        "dimension": MODEL_DIMENSION,
        "path": str(path),
        "runtime": "ONNX Runtime CPU" if ort is not None else "未安装 ONNX Runtime",
        "error": _last_error,
    }


def init_model(directory: Path | str | None = None) -> dict:
    """Load known external weights once. Fails explicitly; never downloads them."""
    global _session, _selected_path, _last_error
    path = _resolve_path(directory) if directory is not None else (_selected_path or _default_path())
    with _lock:
        if _session is not None and path == _selected_path:
            return availability()
        _session = None
        _selected_path = path
        _last_error = None
        try:
            if ort is None:
                raise RuntimeError("尚未安装本地视觉模型运行组件 ONNX Runtime。")
            if not path.is_file():
                raise RuntimeError("本地视觉模型文件缺失，请恢复软件旁的 models 文件夹。")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while data := handle.read(1024 * 1024):
                    digest.update(data)
            if digest.hexdigest() != MODEL_SHA256:
                raise RuntimeError("本地视觉模型校验失败，请使用随软件交付的模型文件。")
            options = ort.SessionOptions()
            options.intra_op_num_threads = min(4, os.cpu_count() or 1)
            options.inter_op_num_threads = 1
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
            inputs = _session.get_inputs()
            if len(inputs) != 1 or inputs[0].name != "pixel_values":
                raise RuntimeError("本地视觉模型输入格式不兼容。")
        except Exception as exc:
            _session = None
            _last_error = str(exc)[:300]
            raise RuntimeError(_last_error) from exc
        return availability()


def _open_image(image_bytes: bytes) -> Image.Image:
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ValueError("图片为空。")
    if len(image_bytes) > 20 * 1024 * 1024:
        raise ValueError("单张图片不能超过 20 MB。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as opened:
                if opened.width < 8 or opened.height < 8 or opened.width * opened.height > 40_000_000:
                    raise ValueError("图片尺寸不合适，请使用至少 8×8 且不超过 4000 万像素的图片。")
                opened.seek(0)
                frame = ImageOps.exif_transpose(opened)
                frame.load()
                rgba = frame.convert("RGBA")
                return Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("无法读取图片。") from exc


def preprocess(image_bytes: bytes) -> np.ndarray:
    """Meta/Hugging Face processor: bicubic shortest edge 256, center crop 224."""
    return _preprocess_image(_open_image(image_bytes))


def _preprocess_image(image: Image.Image) -> np.ndarray:
    w, h = image.size
    if w <= h:
        new_w, new_h = 256, int(256 * h / w)
    else:
        new_w, new_h = int(256 * w / h), 256
    image = image.resize((new_w, new_h), Image.Resampling.BICUBIC)
    left, top = (new_w - 224) // 2, (new_h - 224) // 2
    image = image.crop((left, top, left + 224, top + 224))
    array = np.asarray(image, dtype=np.float32) / np.float32(255.0)
    return np.ascontiguousarray(((array - _MEAN) / _STD).transpose(2, 0, 1)[None], dtype=np.float32)


def extract_embedding(image_bytes: bytes) -> list[float]:
    """Return 384-D equal-mean whole/center CLS features from real inference.

    The whole view retains context and off-center items. A second central view
    reduces lifestyle-background dominance. Both views have equal weight; this
    does not segment a product or distinguish exact SKU/color variants.
    """
    if _session is None:
        init_model()
    image = _open_image(image_bytes)
    width, height = image.size
    center = image.crop((int(width * .15), int(height * .15), int(width * .85), int(height * .85)))
    inputs = np.concatenate((_preprocess_image(image), _preprocess_image(center)), axis=0)
    session = _session
    outputs = session.run(None, {"pixel_values": inputs})
    names = [output.name for output in session.get_outputs()]
    if "pooler_output" in names:
        vectors = outputs[names.index("pooler_output")]
    elif "last_hidden_state" in names:
        vectors = outputs[names.index("last_hidden_state")][:, 0]
    else:
        vectors = outputs[0]
        if vectors.ndim == 3:
            vectors = vectors[:, 0]
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.shape != (2, MODEL_DIMENSION) or not np.isfinite(vectors).all():
        raise RuntimeError("本地视觉模型输出无效。")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms < 1e-8):
        raise RuntimeError("本地视觉模型未生成有效特征。")
    vector = (vectors / norms).mean(axis=0)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        raise RuntimeError("本地视觉模型未生成有效特征。")
    return (vector / norm).tolist()


def cosine_similarity(query: list[float], candidate: list[float]) -> float:
    a, b = np.asarray(query, dtype=np.float32), np.asarray(candidate, dtype=np.float32)
    if a.shape != (MODEL_DIMENSION,) or b.shape != (MODEL_DIMENSION,) or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("视觉特征维度不正确或已损坏。")
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator < 1e-8:
        raise ValueError("视觉特征为空。")
    return float(np.clip(np.dot(a, b) / denominator, -1.0, 1.0))

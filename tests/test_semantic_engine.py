"""Local ONNX inference checks; no downloads or provider calls in tests."""
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import pytest

import semantic_engine as semantic


def picture_bytes(image, **kwargs):
    output = io.BytesIO()
    image.save(output, kwargs.pop("format", "PNG"), **kwargs)
    return output.getvalue()


def test_preprocessing_uses_rgb_and_expected_shape():
    image = Image.new("RGBA", (480, 320), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 70, 360, 260), fill=(120, 100, 200, 255))
    array = semantic.preprocess(picture_bytes(image))
    assert array.shape == (1, 3, 224, 224)
    assert array.dtype == np.float32
    assert np.isfinite(array).all()
    assert array.flags.c_contiguous


def test_invalid_image_and_dimension_are_rejected():
    with pytest.raises(ValueError):
        semantic.preprocess(b"not-an-image")
    with pytest.raises(ValueError):
        semantic.preprocess(picture_bytes(Image.new("RGB", (2, 2))))
    with pytest.raises(ValueError):
        semantic.cosine_similarity([1, 2], [1, 2])
    with pytest.raises(ValueError):
        semantic.cosine_similarity([0.0] * 384, [0.0] * 384)


def test_missing_weights_fail_explicitly_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic, "_session", None)
    monkeypatch.setattr(semantic, "_selected_path", None)
    monkeypatch.setattr(semantic, "_last_error", None)
    with pytest.raises(RuntimeError, match="模型文件缺失"):
        semantic.init_model(tmp_path)
    assert not semantic.availability()["available"]


def test_packaged_model_path_is_preferred(tmp_path, monkeypatch):
    model_path = tmp_path / "models" / "dinov2-small" / "model.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.touch()
    monkeypatch.delenv("MIDOO_MODEL_DIR", raising=False)
    monkeypatch.setattr(semantic.sys, "frozen", True, raising=False)
    monkeypatch.setattr(semantic.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert semantic._default_path() == model_path


def test_real_local_model_returns_normalized_features(monkeypatch):
    model = Path(__file__).resolve().parents[1] / "models" / "dinov2-small" / "model.onnx"
    if not model.is_file() or semantic.ort is None:
        pytest.skip("External local model weights/runtime are not installed")
    import urllib.request
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Embedding inference must never fetch remote resources")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)
    semantic.init_model(model)
    image = Image.new("RGB", (400, 400), "#f8f6f0")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((100, 80, 275, 325), radius=20, fill="#8246a0")
    draw.arc((235, 130, 335, 260), -90, 90, fill="#8246a0", width=15)
    original = semantic.extract_embedding(picture_bytes(image))
    resized = semantic.extract_embedding(picture_bytes(image.resize((240, 240)), format="JPEG", quality=75))
    assert len(original) == 384
    assert abs(np.linalg.norm(original) - 1.0) < 1e-5
    assert semantic.cosine_similarity(original, original) > 0.9999
    assert semantic.cosine_similarity(original, resized) > 0.90

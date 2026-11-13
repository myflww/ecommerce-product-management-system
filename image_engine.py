"""Offline image retrieval for a company product library.

This module compares visual evidence, not product identities. Scores are ranking
scores, never probabilities of being the same SKU. No model or network request is
needed. Whole-image / foreground appearance and geometrically consistent ORB
details complement each other; colour is deliberately only a small signal.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import warnings
from functools import lru_cache
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
FEATURE_VERSION = 1
_ANALYSIS_SIDE = 512
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF"}


def _decode(image_bytes: bytes) -> Image.Image:
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ValueError("图片为空，请选择有效图片。")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("单张图片不能超过 20 MB。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as opened:
                if opened.format not in _ALLOWED_FORMATS:
                    raise ValueError("暂不支持此图片格式，请使用 JPG、PNG、WebP、GIF、BMP 或 TIFF。")
                w, h = opened.size
                if w < 8 or h < 8:
                    raise ValueError("图片过小，请选择至少 8 × 8 像素的图片。")
                if w * h > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素过大，请缩小到 4000 万像素以内。")
                # Animated images are represented by their first frame.
                opened.seek(0)
                frame = ImageOps.exif_transpose(opened)
                frame.load()
                if frame.mode in {"RGBA", "LA"} or "transparency" in frame.info:
                    rgba = frame.convert("RGBA")
                    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    frame = Image.alpha_composite(white, rgba).convert("RGB")
                else:
                    frame = frame.convert("RGB")
                return frame.copy()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("无法读取图片，文件可能损坏、格式不支持或像素过大。") from exc


def _pack(array: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(array).tobytes()).decode("ascii")


@lru_cache(maxsize=16_384)
def _unpack_u8(value: str) -> np.ndarray:
    array = np.frombuffer(base64.b64decode(value), dtype=np.uint8)
    array.setflags(write=False)
    return array


@lru_cache(maxsize=16_384)
def _normalized_vector(value: str, centre: bool = False) -> np.ndarray:
    array = _unpack_u8(value).astype(np.float32)
    if centre:
        array -= array.mean()
    norm = float(np.linalg.norm(array))
    if norm > 1e-6:
        array /= norm
    array.setflags(write=False)
    return array


@lru_cache(maxsize=16_384)
def _hash_int(value: str) -> int:
    return int(value, 16)


def _phash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:16, :16].flatten()
    threshold = float(np.median(low[1:]))
    bits = low > threshold
    # Remove overall luminance as evidence without changing the bit count.
    bits[0] = False
    return np.packbits(bits).tobytes().hex()


def _foreground_crop(rgb: np.ndarray) -> tuple[np.ndarray, bool]:
    """Crop a visibly uniform border; do not pretend to segment complex scenes."""
    height, width = rgb.shape[:2]
    band = max(2, min(height, width) // 32)
    border = np.concatenate((rgb[:band].reshape(-1, 3), rgb[-band:].reshape(-1, 3),
                             rgb[:, :band].reshape(-1, 3), rgb[:, -band:].reshape(-1, 3)))
    median = np.median(border, axis=0)
    deviations = np.linalg.norm(border.astype(np.float32) - median, axis=1)
    # A busy/lifestyle background has no trustworthy automatic boundary.
    if np.percentile(deviations, 80) > 32:
        return rgb, False
    distance = np.linalg.norm(rgb.astype(np.float32) - median, axis=2)
    mask = (distance > max(24.0, float(np.percentile(deviations, 90)) + 14)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    valid = [stat for stat in stats[1:count] if stat[4] >= max(12, height * width * 0.0003)]
    if not valid:
        return rgb, False
    x0 = min(int(s[0]) for s in valid)
    y0 = min(int(s[1]) for s in valid)
    x1 = max(int(s[0] + s[2]) for s in valid)
    y1 = max(int(s[1] + s[3]) for s in valid)
    if (x1 - x0) * (y1 - y0) < height * width * 0.01:
        return rgb, False
    if (x1 - x0) > width * .97 and (y1 - y0) > height * .97:
        return rgb, False
    pad = max(2, int(max(x1 - x0, y1 - y0) * .04))
    x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad)
    return rgb[y0:y1, x0:x1], True


def _appearance(rgb: np.ndarray, name: str) -> dict[str, Any]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edge = cv2.Canny(gray, 55, 135)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [12, 4], [0, 180, 0, 256]).flatten()
    hist /= max(1.0, float(hist.max()))
    return {
        "kind": name,
        "phash": _phash(gray),
        "gray": _pack(cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)),
        "edge": _pack(cv2.resize(edge, (16, 16), interpolation=cv2.INTER_AREA)),
        "colour": _pack(np.rint(hist * 255).astype(np.uint8)),
        "aspect": round(rgb.shape[1] / rgb.shape[0], 4),
    }


def extract_features(image_bytes: bytes) -> dict[str, Any]:
    """Validate and convert an image to a JSON-serializable compact signature.

    Raises ValueError for invalid / oversized input. Original bytes are never
    persisted by this module. The caller owns image and database storage.
    """
    image = _decode(image_bytes)
    width, height = image.size
    image.thumbnail((_ANALYSIS_SIDE, _ANALYSIS_SIDE), Image.Resampling.LANCZOS)
    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 55, 135)
    std = float(gray.std())
    edge_density = float(np.count_nonzero(edges) / edges.size)
    foreground, cropped = _foreground_crop(rgb)
    variants = [_appearance(rgb, "whole")]
    if cropped:
        variants.append(_appearance(foreground, "foreground"))

    # Descriptors use the entire frame so crop/position changes can still match.
    detector = cv2.ORB_create(nfeatures=180, scaleFactor=1.2, nlevels=7,
                              edgeThreshold=12, patchSize=31, fastThreshold=9)
    keypoints, descriptors = detector.detectAndCompute(gray, None)
    orb: dict[str, Any] = {"count": 0, "descriptors": "", "points": ""}
    if descriptors is not None and keypoints:
        points = np.array([(kp.pt[0] / rgb.shape[1], kp.pt[1] / rgb.shape[0])
                           for kp in keypoints], dtype=np.float32)
        orb = {"count": len(keypoints), "descriptors": _pack(descriptors),
               "points": _pack(points)}
    return {
        "version": FEATURE_VERSION,
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
        "phash": variants[0]["phash"],
        "width": width,
        "height": height,
        "quality": {"low_signal": std < 5 or edge_density < .0006,
                    "gray_std": round(std, 3), "edge_density": round(edge_density, 5)},
        "variants": variants,
        "orb": orb,
    }


def make_thumbnail(image_bytes: bytes) -> bytes:
    """Create a safe oriented JPEG thumbnail (first frame for animated images)."""
    image = _decode(image_bytes)
    image.thumbnail((640, 640), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=86, optimize=True)
    return output.getvalue()


def _variant_similarity(a: dict, b: dict) -> float:
    bit_distance = (_hash_int(a["phash"]) ^ _hash_int(b["phash"])).bit_count()
    # Random hashes have ~50% equal bits. They must not start at a 50 score.
    hash_signal = max(0.0, 1 - bit_distance / 128)
    gray_signal = max(0.0, float(np.dot(_normalized_vector(a["gray"], True),
                                      _normalized_vector(b["gray"], True))))
    edge_signal = max(0.0, float(np.dot(_normalized_vector(a["edge"]),
                                      _normalized_vector(b["edge"]))))
    colour_signal = max(0.0, float(np.dot(_normalized_vector(a["colour"]),
                                        _normalized_vector(b["colour"]))))
    # Nearly empty edges should not make flat images look like meaningful hits.
    raw = .45 * hash_signal + .35 * gray_signal + .16 * edge_signal + .04 * colour_signal
    return min(98.5, raw * 98.5)


def _result(score: float, *, exact: bool = False, local: bool = False) -> dict[str, Any]:
    score = round(max(0.0, min(100.0, score)), 1)
    if exact:
        reason = "图片文件完全一致，商品规格仍需核对"
    elif local:
        reason = "局部细节存在位置一致的匹配，请核对规格"
    elif score >= 80:
        reason = "整体结构与主体轮廓接近，请核对规格"
    elif score >= 55:
        reason = "主体轮廓或图像结构相近，建议人工核对"
    elif score >= 30:
        reason = "部分外观特征接近，匹配依据较弱"
    else:
        reason = "外观特征关联较弱"
    return {"score": score, "reason": reason, "exact": exact}


def fast_compare_features(query: dict, candidate: dict) -> dict[str, Any]:
    """Cheap whole-library ranking; use compare_features for the top candidates."""
    if query.get("sha256") and query["sha256"] == candidate.get("sha256"):
        return _result(100, exact=True)
    variants_q, variants_c = query.get("variants", []), candidate.get("variants", [])
    if not variants_q or not variants_c:
        return _result(0)
    score = max(_variant_similarity(a, b) for a in variants_q for b in variants_c)
    if query.get("quality", {}).get("low_signal") or candidate.get("quality", {}).get("low_signal"):
        return _result(min(score * .2, 15))
    return _result(score)


@lru_cache(maxsize=2048)
def _orb_arrays(descriptors: str, points: str, count: int) -> tuple[np.ndarray, np.ndarray]:
    desc = _unpack_u8(descriptors).reshape(count, 32)
    pts = np.frombuffer(base64.b64decode(points), dtype=np.float32).reshape(count, 2)
    return desc, pts


def _local_similarity(query: dict, candidate: dict) -> tuple[float, int]:
    q, c = query.get("orb", {}), candidate.get("orb", {})
    if min(q.get("count", 0), c.get("count", 0)) < 8:
        return 0.0, 0
    q_desc, q_points = _orb_arrays(q["descriptors"], q["points"], q["count"])
    c_desc, c_points = _orb_arrays(c["descriptors"], c["points"], c["count"])
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(q_desc, c_desc, k=2)
    good = [m for pair in pairs if len(pair) == 2
            for m, n in [pair] if m.distance < 65 and m.distance < .76 * n.distance]
    # A cluster of query features must not all point to the same target patch.
    unique = {}
    for match in sorted(good, key=lambda m: m.distance):
        unique.setdefault(match.trainIdx, match)
    good = list(unique.values())
    if len(good) < 7:
        return 0.0, 0
    src = np.float32([q_points[m.queryIdx] for m in good])
    dst = np.float32([c_points[m.trainIdx] for m in good])
    try:
        homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, .03,
                                             maxIters=600, confidence=.98)
    except cv2.error:
        return 0.0, 0
    if homography is None or mask is None or not np.isfinite(homography).all():
        return 0.0, 0
    selected = mask.ravel().astype(bool)
    count = int(np.count_nonzero(selected))
    if count < 6:
        return 0.0, count
    coverage_q = float(cv2.contourArea(cv2.convexHull(src[selected])))
    coverage_c = float(cv2.contourArea(cv2.convexHull(dst[selected])))
    # Tiny shared logos/badges should not identify an otherwise different item.
    if min(coverage_q, coverage_c) < .012 or max(coverage_q, coverage_c) < .055:
        return 0.0, count
    inlier_ratio = count / len(good)
    support = min(1.0, count / 16)
    coverage = min(1.0, math.sqrt(coverage_q * coverage_c) / .12)
    score = (45 + 50 * support) * (.6 + .4 * inlier_ratio) * (.6 + .4 * coverage)
    return min(96.5, score), count


def compare_features(query: dict, candidate: dict) -> dict[str, Any]:
    """Rank a candidate using appearance plus geometrically checked local details.

    For thousands of images, first call fast_compare_features on all records and
    only run this detailed comparison for the top 80–120 image candidates.
    """
    coarse = fast_compare_features(query, candidate)
    if coarse["exact"]:
        return coarse
    if query.get("quality", {}).get("low_signal") or candidate.get("quality", {}).get("low_signal"):
        return coarse
    local_score, _ = _local_similarity(query, candidate)
    if local_score > coarse["score"] + 2:
        return _result(local_score, local=True)
    return coarse


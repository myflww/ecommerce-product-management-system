# DINOv2 Small — local image retrieval weights

Original model: Meta AI / Facebook Research DINOv2-S/14, `facebook/dinov2-small`.
Original model and model weights license: Apache License 2.0; see `LICENSE.txt`.

This directory contains the FP32 ONNX conversion published by ONNX Community.
No training, fine-tuning or changes to the weights were performed for Midoo.
Inference is local using ONNX Runtime CPU; no product picture is sent to a model service.

## Provenance

- Original repository: https://github.com/facebookresearch/dinov2
- Original model card: https://github.com/facebookresearch/dinov2/blob/main/MODEL_CARD.md
- Original processor: https://huggingface.co/facebook/dinov2-small/blob/main/preprocessor_config.json
- ONNX publisher: https://huggingface.co/onnx-community/dinov2-small-ONNX
- Pinned ONNX revision: `08c606e3123472a388efa59181b677d428f69bbd`
- File: `onnx/model.onnx`, renamed only by directory placement.
- Size: `88513997` bytes.
- SHA-256: `6266c3cd72db6953cecdcbfeab9422a9f783d96f1a4e296ba70ffbac43b54a18`

Preprocessing follows the original processor: bicubic resize shortest edge 256,
center crop 224×224, rescale 1/255, mean `[0.485,0.456,0.406]`, standard deviation
`[0.229,0.224,0.225]`. Two image views are encoded: the complete frame and a
central crop covering 70% of each side. The two normalized 384-dimensional CLS
tokens are averaged with equal weights and re-normalized. The full view retains
off-center items; the center view reduces background dominance. This is a generic
multi-view heuristic, not object segmentation, and costs about two model views
per indexed image/query. Encoded vectors are cached by the calling application.

The model describes visual appearance and semantic family. A high retrieval
similarity does not establish identical colors, dimensions, package quantities,
materials or SKU identity. Actual product specifications still require review.

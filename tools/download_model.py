"""Fetch the pinned public DINOv2 ONNX model and verify its SHA-256."""
from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

MODEL_SHA256 = '6266c3cd72db6953cecdcbfeab9422a9f783d96f1a4e296ba70ffbac43b54a18'
MODEL_URL = 'https://huggingface.co/onnx-community/dinov2-small-ONNX/resolve/08c606e3123472a388efa59181b677d428f69bbd/onnx/model.onnx'
DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'models' / 'dinov2-small' / 'model.onnx'


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_PATH)
    parser.add_argument('--force', action='store_true', help='Replace a file that does not match the pinned model.')
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists():
        if digest(target) == MODEL_SHA256:
            print('Model already present and verified.')
            return
        if not args.force:
            parser.error('Existing model does not match. Preserve your copy or explicitly use --force.')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.part')
    try:
        request = urllib.request.Request(MODEL_URL, headers={'User-Agent':'Midoo-model-setup/1.6'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as out:
            while block := response.read(1024 * 1024):
                out.write(block)
        if digest(temporary) != MODEL_SHA256:
            raise RuntimeError('Model checksum mismatch; the downloaded file was not installed.')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print('Pinned model downloaded and SHA-256 verified. Original Apache-2.0 license applies.')


if __name__ == '__main__':
    main()

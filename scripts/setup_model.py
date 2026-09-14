# scripts/setup_model.py
"""Download the Llama 3.2 1B GGUF. Idempotent; safe to run repeatedly."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MODEL_FILENAME = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
MIRROR_REPO = "bartowski/Llama-3.2-1B-Instruct-GGUF"
OFFICIAL_REPO = "meta-llama/Llama-3.2-1B-Instruct"
MODEL_DIR = Path("models")
MIN_BYTES = 600 * 1024 * 1024          # a valid Q4_K_M 1B is ~0.8 GB


def resolve_source(has_token: bool) -> tuple:
    """Meta's repo is gated; without HF_TOKEN use the ungated mirror."""
    return (OFFICIAL_REPO if has_token else MIRROR_REPO), MODEL_FILENAME


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                       help="re-download even if the file is already present")
    parser.add_argument("--dest", default=str(MODEL_DIR))
    args = parser.parse_args(argv)

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / MODEL_FILENAME

    if target.exists() and not args.force:
        size = target.stat().st_size
        if size >= MIN_BYTES:
            print(f"already present: {target} ({size / 1e9:.2f} GB)")
            return 0
        print(f"{target} is only {size} bytes; re-downloading")

    token = os.environ.get("HF_TOKEN")
    repo_id, filename = resolve_source(bool(token))
    print(f"downloading {filename} from {repo_id} ...")

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("huggingface_hub is missing; run `make install`", file=sys.stderr)
        return 1

    try:
        path = hf_hub_download(repo_id=repo_id, filename=filename,
                               local_dir=str(dest_dir), token=token)
    except Exception as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        if repo_id == OFFICIAL_REPO:
            print("The official repo is gated. Accept the licence on Hugging Face, "
                  "or unset HF_TOKEN to use the ungated mirror.", file=sys.stderr)
        return 1

    size = Path(path).stat().st_size
    if size < MIN_BYTES:
        print(f"downloaded file is suspiciously small ({size} bytes)",
              file=sys.stderr)
        return 1
    print(f"done: {path} ({size / 1e9:.2f} GB)")
    print("The game runs without this file; it only improves the prose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

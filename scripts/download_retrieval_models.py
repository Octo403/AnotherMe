"""Download public model artifacts only. No application text is read."""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("data/runtime/retrieval_models"))
    parser.add_argument("--proxy")
    args = parser.parse_args()
    if args.proxy:
        os.environ["HTTPS_PROXY"] = args.proxy
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download
    for name in ("bge-small-zh-v1.5", "bge-reranker-base"):
        print("Downloading BAAI/" + name, flush=True)
        snapshot_download("BAAI/" + name, local_dir=args.directory / name,
                          allow_patterns=["config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
                                          "special_tokens_map.json", "vocab.txt", "sentencepiece.bpe.model"],
                          max_workers=3)


if __name__ == "__main__":
    main()

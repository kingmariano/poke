"""Fetch the address dataset from the configured storage hub and build shards.

The dataset lives in a private hub repository; its id and token are provided
via environment variables (configured as repository secrets in CI).

Output: shards/shard-XX.txt.gz (one address per line, lowercased) plus
shards/manifest.json with counts.
"""

import argparse
import gzip
import json
import os
import sys
import time

from huggingface_hub import HfApi, hf_hub_download

ADDRESS_COLUMN = 0
INCLUDE_PREFIXES = ("generated/", "published/")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=os.environ.get("DATASET_REPO", ""))
    ap.add_argument("--shards", type=int, default=20)
    ap.add_argument("--out", default="shards")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    token = os.environ.get("HF_TOKEN")
    if not token or not args.repo:
        print("missing hub configuration (token/repo)", file=sys.stderr)
        return 2

    api = HfApi(token=token)
    files = [
        f for f in api.list_repo_files(repo_id=args.repo, repo_type="dataset")
        if f.endswith(".tsv.gz") and f.startswith(INCLUDE_PREFIXES)
    ]
    print(f"dataset files: {len(files)}", flush=True)

    os.makedirs(args.out, exist_ok=True)
    n = args.shards
    writers = [gzip.open(os.path.join(args.out, f"shard-{i:02d}.txt.gz"), "wt") for i in range(n)]
    seen = [set() for _ in range(n)]
    counts = [0] * n
    total = 0
    t0 = time.time()

    for idx, f in enumerate(files):
        path = hf_hub_download(repo_id=args.repo, filename=f, repo_type="dataset", token=token)
        with gzip.open(path, "rt") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split("\t", 1)
                addr = parts[0].strip().lower()
                if not (addr.startswith("0x") and len(addr) == 42):
                    continue
                shard = int(addr[2:], 16) % n
                if addr in seen[shard]:
                    continue
                seen[shard].add(addr)
                writers[shard].write(addr + "\n")
                counts[shard] += 1
                total += 1
                if args.limit and total >= args.limit:
                    break
        print(f"  [{idx+1}/{len(files)}] total {total:,}", flush=True)
        if args.limit and total >= args.limit:
            break

    for w in writers:
        w.close()

    manifest = {
        "shards": n,
        "addresses": total,
        "shard_counts": counts,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

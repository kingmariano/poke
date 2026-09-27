# poke

Utilities to process a large dataset of addresses and check their current balances across EVM
networks (native assets and ERC-20 tokens), producing a consolidated USD report.

## Workflow

`.github/workflows/check.yml` runs in three stages:

1. **fetch** – downloads the address dataset from the configured hub and splits it into shards.
2. **check** – parallel matrix jobs; each shard queries every supported network with batched
   JSON-RPC calls, adaptive rate limiting (backs off on 429, recovers slowly), bounded concurrency,
   and per-network progressive writes so partial progress is never lost.
3. **report** – aggregates shard outputs, prices balances, and bundles everything into
   `report/results.zip` plus standalone CSV/JSON/Markdown.

## Configuration (repository secrets)

| secret | purpose |
|---|---|
| `HF_TOKEN` | hub access token |
| `DATASET_REPO` | hub dataset identifier |
| `ALCHEMY_API_KEY` | RPC provider key |

## Running

Trigger the workflow manually (Actions → address-balance-check → Run workflow). The `shards`
input controls the number of parallel shards (default 20). Results are attached to the run as
artifacts; the `report` artifact contains the consolidated bundle.

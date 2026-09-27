"""Check balances for one address shard across EVM networks.

For each network in the registry:
  * native balances via batched JSON-RPC
  * ERC-20 balances via the provider's token-balance endpoint (batched)

Robustness:
  * preflight verifies each network (and token support) and skips unavailable ones
  * adaptive rate limiting (token bucket; backs off on 429, recovers slowly)
  * bounded concurrency, retries with exponential backoff
  * results are written progressively per network, so partial progress survives

Output: CSV (address, network, native_wei, token_count, tokens) + stats JSON.
"""

import argparse
import asyncio
import csv
import gzip
import json
import os
import sys
import time
from typing import Dict, List, Optional

import aiohttp

from .networks import NETWORKS, TOKEN_CANDIDATES

PROBE_ADDRESS = "0x28C6c06298d514Db089934071355E5743bf21d60"


class Limit:
    """Adaptive token bucket."""

    def __init__(self, rate: float, max_rate: float):
        self.rate = rate
        self.max_rate = max_rate
        self.tokens = rate
        self.ts = time.monotonic()
        self.lock = asyncio.Lock()
        self.throttled = 0

    async def acquire(self):
        while True:
            async with self.lock:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.ts) * self.rate)
                self.ts = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait)

    async def backoff(self):
        async with self.lock:
            self.throttled += 1
            self.rate = max(0.25, self.rate / 2)
            self.tokens = 0.0

    async def recover(self):
        async with self.lock:
            if self.rate < self.max_rate:
                self.rate = min(self.max_rate, self.rate * 1.05)


class Rpc:
    def __init__(self, session, key, limiter, retries=6):
        self.session = session
        self.key = key
        self.limiter = limiter
        self.retries = retries
        self.stats = {"requests": 0, "throttled": 0, "errors": 0}

    async def batch(self, network: str, calls: List[dict]) -> Optional[List[dict]]:
        url = f"https://{network}.g.alchemy.com/v2/{self.key}"
        payload = [dict(c, jsonrpc="2.0", id=i) for i, c in enumerate(calls)]
        for attempt in range(self.retries):
            await self.limiter.acquire()
            try:
                async with self.session.post(url, json=payload,
                                             timeout=aiohttp.ClientTimeout(total=90)) as r:
                    self.stats["requests"] += 1
                    if r.status == 429:
                        self.stats["throttled"] += 1
                        await self.limiter.backoff()
                        await asyncio.sleep(min(30, 2 ** attempt))
                        continue
                    if r.status >= 500:
                        await asyncio.sleep(min(20, 1.5 ** attempt))
                        continue
                    data = await r.json(content_type=None)
                    if not isinstance(data, list):
                        await asyncio.sleep(1.5 ** attempt)
                        continue
                    out = [None] * len(calls)
                    for item in data:
                        i = item.get("id")
                        if isinstance(i, int) and 0 <= i < len(calls):
                            out[i] = item.get("result")
                    await self.limiter.recover()
                    return out
            except (aiohttp.ClientError, asyncio.TimeoutError):
                self.stats["errors"] += 1
                await asyncio.sleep(min(20, 1.5 ** attempt))
        return None


async def preflight(session, key, networks, token_networks, limiter):
    active, active_tokens, skipped = [], set(), {}
    for net in networks:
        url = f"https://{net}.g.alchemy.com/v2/{key}"
        status = None
        for attempt in range(3):
            await limiter.acquire()
            try:
                async with session.post(url, json={"jsonrpc": "2.0", "id": 1,
                                                   "method": "eth_chainId", "params": []},
                                        timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status == 200:
                        status = "ok"
                        break
                    if r.status == 429:
                        await asyncio.sleep(5 + attempt * 5)
                        continue
                    status = f"http{r.status}"
                    break
            except Exception:  # noqa: BLE001
                status = "unavailable"
                break
        if status in ("ok", "429"):
            active.append(net)
            if net in token_networks:
                ok = False
                for attempt in range(3):
                    await limiter.acquire()
                    try:
                        async with session.post(url, json={"jsonrpc": "2.0", "id": 1,
                                                           "method": "alchemy_getTokenBalances",
                                                           "params": [PROBE_ADDRESS, "erc20"]},
                                                timeout=aiohttp.ClientTimeout(total=25)) as r:
                            if r.status == 200:
                                data = await r.json(content_type=None)
                                res = data.get("result") if isinstance(data, dict) else None
                                ok = isinstance(res, dict) and "tokenBalances" in res
                                break
                            if r.status == 429:
                                await asyncio.sleep(5 + attempt * 5)
                                continue
                            break
                    except Exception:  # noqa: BLE001
                        break
                if ok:
                    active_tokens.add(net)
                else:
                    skipped[f"{net}:tokens"] = "unsupported"
        else:
            skipped[net] = status
        await asyncio.sleep(0.2)
    return active, active_tokens, skipped


def read_addresses(path: str, limit: int = 0) -> List[str]:
    out = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        for line in f:
            a = line.strip()
            if a.startswith("0x") and len(a) == 42:
                out.append(a)
                if limit and len(out) >= limit:
                    break
    return out


class Output:
    """Progressive CSV writer (flush per network)."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.fh = open(path, "w", newline="")
        self.w = csv.writer(self.fh)
        self.w.writerow(["address", "network", "native_wei", "token_count", "tokens"])
        self.fh.flush()

    def write(self, hits: Dict[str, dict], network: str):
        for a, rec in hits.items():
            wei = (rec.get("native") or {}).get(network, 0)
            toks = (rec.get("tokens") or {}).get(network) or []
            if wei or toks:
                desc = "|".join(f"{t}:{v}" for t, v in sorted(toks, key=lambda x: -x[1])[:50])
                self.w.writerow([a, network, wei, len(toks), desc])
        self.fh.flush()

    def close(self):
        self.fh.close()


async def run(args):
    key = os.environ.get("ALCHEMY_API_KEY")
    if not key:
        print("missing provider key", file=sys.stderr)
        return 2
    networks = [n.strip() for n in args.networks.split(",") if n.strip()]
    for n in networks:
        if n not in NETWORKS:
            print(f"unknown network {n}", file=sys.stderr)
            return 2
    token_networks = {n.strip() for n in args.token_networks.split(",") if n.strip() and n in NETWORKS}

    addresses = read_addresses(args.shard, args.limit)
    out = Output(args.out)
    stats = {"shard": os.path.basename(args.shard), "addresses": len(addresses),
             "native_checks": 0, "token_checks": 0, "networks": {}, "skipped": {},
             "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    limiter = Limit(args.rps, args.max_rate)
    hits: Dict[str, dict] = {}

    connector = aiohttp.TCPConnector(limit=args.concurrency * 2, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        rpc = Rpc(session, key, limiter)
        print("preflight ...", flush=True)
        active, active_tokens, skipped = await preflight(session, key, networks, token_networks, limiter)
        stats["skipped"] = skipped
        stats["active"] = active
        stats["active_tokens"] = sorted(active_tokens)
        print(f"preflight: {len(active)} networks, {len(active_tokens)} token-enabled; "
              f"skipped {len(skipped)}", flush=True)

        sem = asyncio.Semaphore(args.concurrency)

        async def process(net):
            async with sem:
                cfg = NETWORKS[net]
                t0 = time.time()
                for i in range(0, len(addresses), args.batch):
                    chunk = addresses[i:i + args.batch]
                    res = await rpc.batch(net, [{"method": "eth_getBalance",
                                                 "params": [a, "latest"]} for a in chunk])
                    if res:
                        for a, bal in zip(chunk, res):
                            if not bal:
                                continue
                            try:
                                v = int(bal, 16)
                            except Exception:  # noqa: BLE001
                                continue
                            if v > 0:
                                hits.setdefault(a, {}).setdefault("native", {})[net] = v
                    stats["native_checks"] += len(chunk)
                if net in active_tokens:
                    for i in range(0, len(addresses), args.token_batch):
                        chunk = addresses[i:i + args.token_batch]
                        res = await rpc.batch(net, [{"method": "alchemy_getTokenBalances",
                                                     "params": [a, "erc20"]} for a in chunk])
                        if res:
                            for a, item in zip(chunk, res):
                                if not isinstance(item, dict):
                                    continue
                                toks = []
                                for tb in item.get("tokenBalances") or []:
                                    try:
                                        amt = int(tb.get("tokenBalance") or "0x0", 16)
                                    except Exception:  # noqa: BLE001
                                        amt = 0
                                    if amt > 0:
                                        toks.append((tb.get("contractAddress", "").lower(), amt))
                                if toks:
                                    hits.setdefault(a, {}).setdefault("tokens", {})[net] = toks
                        stats["token_checks"] += len(chunk)
                out.write(hits, net)
                stats["networks"][net] = {
                    "seconds": round(time.time() - t0, 1),
                    "native_checks": len(addresses),
                    "token_checks": len(addresses) if net in active_tokens else 0,
                }
                print(f"  {net}: done in {stats['networks'][net]['seconds']}s", flush=True)

        for net in active:
            await process(net)

    out.close()
    stats["rate_final"] = round(limiter.rate, 2)
    stats["throttled"] = limiter.throttled
    stats["rpc"] = rpc.stats
    stats["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stats["addresses_with_balance"] = len(hits)
    if args.stats:
        os.makedirs(os.path.dirname(os.path.abspath(args.stats)) or ".", exist_ok=True)
        with open(args.stats, "w") as f:
            json.dump(stats, f, indent=1)
    print(json.dumps({k: v for k, v in stats.items() if k != "networks"}, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shard", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stats", default=None)
    ap.add_argument("--networks", default=",".join(NETWORKS.keys()))
    ap.add_argument("--token-networks", default=",".join(TOKEN_CANDIDATES))
    ap.add_argument("--rps", type=float, default=15.0)
    ap.add_argument("--max-rate", type=float, default=60.0)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--token-batch", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())

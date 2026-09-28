"""Check balances for one address shard across the top EVM networks.

Native balances: general-purpose RPC providers (failover across endpoints).
ERC-20 balances: the dedicated token provider.

Robustness:
  * endpoint failover for native calls
  * bounded concurrency + adaptive backoff on 429
  * progressive per-network writes (partial progress always preserved)
  * resumable: if a per-network output already exists, that network is skipped

Output: results/<shard>.csv (address, network, native_wei, token_count, tokens)
        results/<shard>.json (stats, including per-network progress)
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

try:
    from .networks import NETWORKS, TOKEN_CANDIDATES
    from .providers import native_urls, token_url
except ImportError:  # pragma: no cover
    from networks import NETWORKS, TOKEN_CANDIDATES
    from providers import native_urls, token_url

RETRIES = 5


class Limit:
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
            self.rate = max(0.5, self.rate / 2)
            self.tokens = 0.0

    async def recover(self):
        async with self.lock:
            if self.rate < self.max_rate:
                self.rate = min(self.max_rate, self.rate * 1.02)


async def batch_call(session, urls: List[str], calls: List[dict], state: dict) -> Optional[List[dict]]:
    """Send a JSON-RPC batch, failing over across urls on error."""
    payload = [dict(c, jsonrpc="2.0", id=i) for i, c in enumerate(calls)]
    n = len(calls)
    for url in urls:
        for attempt in range(RETRIES):
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as r:
                    state["requests"] += 1
                    if r.status == 429:
                        state["throttled"] += 1
                        await asyncio.sleep(0.8 * (attempt + 1))
                        continue
                    if r.status >= 500:
                        await asyncio.sleep(0.6 * (attempt + 1))
                        continue
                    if r.status != 200:
                        break  # try next endpoint
                    data = await r.json(content_type=None)
                    if not isinstance(data, list):
                        break
                    out = [None] * n
                    for item in data:
                        i = item.get("id")
                        if isinstance(i, int) and 0 <= i < n:
                            out[i] = item.get("result")
                    if all(x is not None for x in out):
                        state["ok"] += 1
                        return out
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                state["errors"] += 1
                await asyncio.sleep(0.6 * (attempt + 1))
    return None


def read_addresses(path: str, limit: int = 0) -> List[str]:
    out = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        for line in f:
            a = line.strip().lower()
            if a.startswith("0x") and len(a) == 42:
                out.append(a)
                if limit and len(out) >= limit:
                    break
    return out


async def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shard", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stats", default=None)
    ap.add_argument("--networks", default=",".join(NETWORKS.keys()))
    ap.add_argument("--token-networks", default=",".join(TOKEN_CANDIDATES))
    ap.add_argument("--native-batch", type=int, default=50)
    ap.add_argument("--native-concurrency", type=int, default=16)
    ap.add_argument("--token-batch", type=int, default=50)
    ap.add_argument("--token-concurrency", type=int, default=4)
    ap.add_argument("--token-rate", type=float, default=18.0)
    ap.add_argument("--token-max-rate", type=float, default=40.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-seconds", type=int, default=18000)
    args = ap.parse_args(argv)

    networks = [n.strip() for n in args.networks.split(",") if n.strip()]
    token_networks = {n.strip() for n in args.token_networks.split(",") if n.strip()}
    addresses = read_addresses(args.shard, args.limit)
    a_key = "".join(addresses)  # cheap identity for resume checks

    stats = {"shard": os.path.basename(args.shard), "addresses": len(addresses),
             "networks": {}, "skipped": {}, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    new_file = not os.path.exists(args.out)
    fh = open(args.out, "a", newline="")
    w = csv.writer(fh)
    if new_file:
        w.writerow(["address", "network", "native_wei", "token_count", "tokens"])

    done_nets = set()
    if args.stats and os.path.exists(args.stats):
        try:
            prev = json.load(open(args.stats))
            if prev.get("addresses") == len(addresses):
                done_nets = set(prev.get("networks", {}).keys())
                stats["skipped"] = prev.get("skipped", {})
                print(f"resuming: {len(done_nets)} networks already complete", flush=True)
        except Exception:  # noqa: BLE001
            pass

    all_hits = set()
    limiter = Limit(args.token_rate, args.token_max_rate)
    native_state = {"requests": 0, "ok": 0, "throttled": 0, "errors": 0}
    token_state = {"requests": 0, "ok": 0, "throttled": 0, "errors": 0}
    hits: Dict[str, dict] = {}
    connector = aiohttp.TCPConnector(limit=64, ttl_dns_cache=300)

    async with aiohttp.ClientSession(connector=connector) as session:
        deadline = time.monotonic() + args.max_seconds

        async def process_network(net):
            if net in done_nets:
                return
            cfg = NETWORKS.get(net)
            if not cfg:
                return
            if time.monotonic() > deadline:
                stats["skipped"][net] = "time-budget"
                return
            t0 = time.time()
            urls = native_urls(net)
            if not urls:
                stats["skipped"][net] = "no-rpc-endpoint"
                return
            local: Dict[str, dict] = {}
            # ---- native ----
            sem = asyncio.Semaphore(args.native_concurrency)
            nchunks = [addresses[i:i + args.native_batch] for i in range(0, len(addresses), args.native_batch)]

            async def do_native(chunk):
                async with sem:
                    res = await batch_call(session, urls,
                                           [{"method": "eth_getBalance", "params": [a, "latest"]} for a in chunk],
                                           native_state)
                if res:
                    for a, bal in zip(chunk, res):
                        if not bal:
                            continue
                        try:
                            v = int(bal, 16)
                        except Exception:  # noqa: BLE001
                            continue
                        if v > 0:
                            local.setdefault(a, {}).setdefault("native", {})[net] = v
                stats["native_checked"] = stats.get("native_checked", 0) + len(chunk)

            for i in range(0, len(nchunks), args.native_concurrency * 4):
                if time.monotonic() > deadline:
                    break
                await asyncio.gather(*(do_native(c) for c in nchunks[i:i + args.native_concurrency * 4]))
            # ---- tokens ----
            if net in token_networks and time.monotonic() < deadline:
                turl = token_url(net)
                if turl:
                    tsem = asyncio.Semaphore(args.token_concurrency)
                    tchunks = [addresses[i:i + args.token_batch] for i in range(0, len(addresses), args.token_batch)]

                    async def do_tokens(chunk):
                        async with tsem:
                            await limiter.acquire()
                            res = await batch_call(session, [turl],
                                                   [{"method": "alchemy_getTokenBalances",
                                                     "params": [a, "erc20"]} for a in chunk], token_state)
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
                                    local.setdefault(a, {}).setdefault("tokens", {})[net] = toks
                        stats["token_checked"] = stats.get("token_checked", 0) + len(chunk)

                    for i in range(0, len(tchunks), args.token_concurrency * 4):
                        if time.monotonic() > deadline:
                            break
                        await asyncio.gather(*(do_tokens(c) for c in tchunks[i:i + args.token_concurrency * 4]))
            # ---- write network results ----
            for a, rec in local.items():
                all_hits.add(a)
                wei = (rec.get("native") or {}).get(net, 0)
                toks = (rec.get("tokens") or {}).get(net) or []
                if wei or toks:
                    desc = "|".join(f"{t}:{v}" for t, v in sorted(toks, key=lambda x: -x[1])[:50])
                    w.writerow([a, net, wei, len(toks), desc])
            fh.flush()
            stats["networks"][net] = {
                "seconds": round(time.time() - t0, 1),
                "native_checked": len(addresses),
                "token_checked": len(addresses) if net in token_networks else 0,
                "hits": len(local),
            }
            if args.stats:
                with open(args.stats, "w") as f:
                    json.dump(stats, f, indent=1)
            print(f"  {net}: {stats['networks'][net]['seconds']}s "
                  f"(native {native_state['ok']} ok/{native_state['throttled']} 429, "
                  f"token {token_state['ok']} ok/{token_state['throttled']} 429)", flush=True)

        await asyncio.gather(*(process_network(n) for n in networks))

    fh.close()
    stats["native"] = native_state
    stats["token"] = token_state
    stats["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stats["addresses_with_balance"] = len(all_hits)
    if args.stats:
        with open(args.stats, "w") as f:
            json.dump(stats, f, indent=1)
    print(json.dumps({k: v for k, v in stats.items() if k != "networks"}, indent=1), flush=True)
    return 0


def main():
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
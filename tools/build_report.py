"""Aggregate shard results, price balances with the Alchemy Prices API, build report.

Simple, single-provider pricing:
  * token decimals/symbols  -> JSON-RPC alchemy_getTokenMetadata (batched)
  * token prices            -> POST /prices/v1/{key}/tokens/by-address (batched)
  * native prices           -> POST /prices/v1/{key}/tokens/by-symbol
  * any asset without a returned price is EXCLUDED and recorded as spam/unpriced

Input : results/shard-*.csv(.gz) produced by check_balances.py (partials welcome)
Output: report/report.csv.gz, report/summary.json, report/report.md,
        report/excluded.csv.gz, report/results.zip (everything bundled)
"""

import argparse
import csv
import glob
import gzip
import json
import os
import time
import urllib.request
import zipfile
from collections import defaultdict

try:  # allow execution as a plain script and as a module
    from .networks import NETWORKS
except ImportError:  # pragma: no cover
    from networks import NETWORKS

PRICES_BASE = "https://api.g.alchemy.com/prices/v1"
ADDR_BATCH = 25
SYMBOL_BATCH = 25
RPC_BATCH = 50

# native symbol per network slug (for the by-symbol price lookup)
NATIVE_SYMBOL = {
    "ethereum": "ETH", "optimism": "ETH", "base": "ETH", "arbitrum": "ETH", "linea": "ETH",
    "scroll": "ETH", "era": "ETH", "blast": "ETH", "unichain": "ETH", "worldchain": "ETH",
    "soneium": "ETH", "ink": "ETH", "abstract": "ETH", "shape": "ETH", "zora": "ETH", "bob": "ETH",
    "bsc": "BNB", "op_bnb": "BNB", "avax": "AVAX", "polygon": "POL", "xdai": "XDAI",
    "celo": "CELO", "ronin": "RON", "mantle": "MNT", "moonbeam": "GLMR", "metis": "METIS",
    "rootstock": "RBTC", "zetachain": "ZETA", "sei": "SEI", "astar": "ASTR", "sonic": "S",
    "berachain": "BERA", "apechain": "APE", "story": "IP", "kaia": "KAIA", "monad": "MON",
    "tempo": "TEMPO",
}


def open_any(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path, "rt")


def load_rows(paths):
    agg = defaultdict(lambda: {"native": {}, "tokens": {}})
    rows = 0
    for p in paths:
        with open_any(p) as f:
            for r in csv.DictReader(f):
                rows += 1
                a, net = r["address"].lower(), r["network"]
                rec = agg[a]
                try:
                    wei = int(r.get("native_wei") or 0)
                except ValueError:
                    wei = 0
                if wei > 0:
                    rec["native"][net] = rec["native"].get(net, 0) + wei
                if r.get("tokens"):
                    toks = []
                    for part in r["tokens"].split("|"):
                        if ":" in part:
                            t, v = part.split(":", 1)
                            try:
                                toks.append((t, int(v)))
                            except ValueError:
                                pass
                    if toks:
                        rec["tokens"].setdefault(net, []).extend(toks)
    return agg, rows


def _post(url, payload, retries=6):
    data = json.dumps(payload).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(min(30, 3 * (attempt + 1)))
                continue
            if e.code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            return {}
        except Exception:  # noqa: BLE001
            time.sleep(min(20, 2 ** attempt))
    return {}


def _rpc(api_key, network, calls, retries=6):
    url = f"https://{network}.g.alchemy.com/v2/{api_key}"
    payload = [dict(c, jsonrpc="2.0", id=i) for i, c in enumerate(calls)]
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            if isinstance(data, list):
                out = [None] * len(calls)
                for item in data:
                    i = item.get("id")
                    if isinstance(i, int) and 0 <= i < len(calls):
                        out[i] = item.get("result")
                return out
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(min(30, 3 * (attempt + 1)))
                continue
            if e.code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            return []
        except Exception:  # noqa: BLE001
            time.sleep(min(20, 2 ** attempt))
    return []


def token_metadata(api_key, pairs):
    """pairs: set of (network, token) -> {(network, token): {"symbol","decimals"}}"""
    out = {}
    by_net = defaultdict(list)
    for net, tok in pairs:
        by_net[net].append(tok)
    for net, toks in by_net.items():
        for i in range(0, len(toks), RPC_BATCH):
            chunk = toks[i:i + RPC_BATCH]
            res = _rpc(api_key, net, [{"method": "alchemy_getTokenMetadata", "params": [t]} for t in chunk])
            for t, m in zip(chunk, res):
                if isinstance(m, dict):
                    out[(net, t)] = {"symbol": m.get("symbol") or t[:10],
                                     "decimals": int(m.get("decimals") or 0)}
    return out


def token_prices(api_key, pairs):
    """prices via Alchemy Prices API by-address -> {(network, token): price}"""
    url = f"{PRICES_BASE}/{api_key}/tokens/by-address"
    out = {}
    items = sorted(pairs)
    for i in range(0, len(items), ADDR_BATCH):
        chunk = items[i:i + ADDR_BATCH]
        resp = _post(url, {"addresses": [{"network": n, "address": t} for n, t in chunk]})
        for entry in resp.get("data") or []:
            net, tok = entry.get("network"), (entry.get("address") or "").lower()
            for p in entry.get("prices") or []:
                if p.get("currency") == "usd" and p.get("value"):
                    out[(net, tok)] = float(p["value"])
                    break
    return out


def native_prices(api_key, symbols):
    """native prices via Alchemy Prices API by-symbol (GET; POST is not allowed)"""
    out = {}
    syms = sorted(symbols)
    for i in range(0, len(syms), SYMBOL_BATCH):
        url = f"{PRICES_BASE}/{api_key}/tokens/by-symbol?symbols=" + ",".join(syms[i:i + SYMBOL_BATCH])
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=90) as r:
                    resp = json.load(r)
                for entry in resp.get("data") or []:
                    sym = (entry.get("symbol") or "").upper()
                    for p in entry.get("prices") or []:
                        if p.get("currency") == "usd" and p.get("value"):
                            out[sym] = float(p["value"])
                            break
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(30, 3 * (attempt + 1)))
                    continue
                break
            except Exception:  # noqa: BLE001
                time.sleep(min(20, 2 ** attempt))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="report")
    ap.add_argument("--min-usd", type=float, default=1.0)
    args = ap.parse_args(argv)

    api_key = os.environ.get("ALCHEMY_API_KEY")
    if not api_key:
        raise SystemExit("ALCHEMY_API_KEY not set")

    paths = sorted(glob.glob(os.path.join(args.results, "shard-*.csv")) +
                   glob.glob(os.path.join(args.results, "shard-*.csv.gz")))
    if not paths:
        raise SystemExit("no shard results found")
    agg, total_rows = load_rows(paths)
    print(f"loaded {total_rows} rows across {len(paths)} shards; {len(agg)} addresses with balances")

    pairs = {(net, t) for rec in agg.values() for net, toks in rec["tokens"].items() for t, _ in toks}
    symbols = {NATIVE_SYMBOL[NETWORKS[net]["slug"]] for rec in agg.values()
               for net in rec["native"] if net in NETWORKS and NETWORKS[net]["slug"] in NATIVE_SYMBOL}
    print(f"unique tokens: {len(pairs)}, native symbols: {sorted(symbols)}")

    meta = token_metadata(api_key, pairs)
    print(f"token metadata resolved: {len(meta)}/{len(pairs)}")
    prices = token_prices(api_key, pairs)
    print(f"token prices resolved: {len(prices)}/{len(pairs)}")
    nat = native_prices(api_key, symbols)
    print(f"native prices resolved: {len(nat)}/{len(symbols)}")

    os.makedirs(args.out, exist_ok=True)
    rows, excluded = [], []
    for a, rec in agg.items():
        n_usd, n_detail = 0.0, []
        for net, wei in rec["native"].items():
            cfg = NETWORKS.get(net)
            if not cfg:
                continue
            sym = NATIVE_SYMBOL.get(cfg["slug"])
            price = nat.get(sym) if sym else None
            if not price:
                excluded.append({"address": a, "network": net, "asset": "native",
                                 "raw": str(wei), "reason": "no-price"})
                continue
            usd = wei / 1e18 * price
            n_usd += usd
            n_detail.append(f"{cfg['symbol']}:{wei/1e18:.6g}:{usd:.2f}")

        t_usd, t_detail = 0.0, []
        for net, toks in rec["tokens"].items():
            for t, raw in toks:
                price = prices.get((net, t))
                info = meta.get((net, t)) or {}
                dec = info.get("decimals")
                if price is None or dec is None:
                    excluded.append({"address": a, "network": net, "asset": t,
                                     "raw": str(raw), "reason": "spam-or-unpriced"})
                    continue
                usd = raw / (10 ** int(dec)) * price
                t_usd += usd
                t_detail.append(f"{info.get('symbol') or t[:10]}:{usd:.2f}")

        rows.append({
            "address": a,
            "native_usd": round(n_usd, 2),
            "token_usd": round(t_usd, 2),
            "total_usd": round(n_usd + t_usd, 2),
            "networks": "|".join(sorted(set(rec["native"]) | set(rec["tokens"]))),
            "native_detail": "|".join(n_detail),
            "token_detail": "|".join(sorted(t_detail, reverse=True)[:15]),
        })

    rows.sort(key=lambda r: -r["total_usd"])
    selected = [r for r in rows if r["total_usd"] > args.min_usd]
    total_usd = sum(r["total_usd"] for r in selected)

    fields = ["address", "native_usd", "token_usd", "total_usd", "networks", "native_detail", "token_detail"]
    with gzip.open(os.path.join(args.out, "report.csv.gz"), "wt") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in selected:
            w.writerow(r)
    with gzip.open(os.path.join(args.out, "excluded.csv.gz"), "wt") as f:
        w = csv.DictWriter(f, fieldnames=["address", "network", "asset", "raw", "reason"])
        w.writeheader()
        for r in excluded:
            w.writerow(r)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pricing_source": "alchemy-prices",
        "shards": len(paths),
        "addresses_with_balances": len(agg),
        "addresses_above_threshold": len(selected),
        "threshold_usd": args.min_usd,
        "total_usd": round(total_usd, 2),
        "excluded_entries": len(excluded),
        "tokens_priced": len(prices),
        "tokens_seen": len(pairs),
        "top": selected[:100],
    }
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)

    md = ["# Consolidated balance report", "", f"_Generated {summary['generated_at']}_ (priced via Alchemy)", "",
          f"- shards processed: **{len(paths)}**",
          f"- addresses with non-zero balances: **{len(agg):,}**",
          f"- above ${args.min_usd:.2f}: **{len(selected):,}**",
          f"- total USD: **${total_usd:,.2f}**",
          f"- excluded (no price -> treated as spam/unpriced): {len(excluded):,}", "",
          "| # | address | USD | networks |", "|---:|---|---:|---|"]
    for i, r in enumerate(selected[:100], 1):
        md.append(f"| {i} | {r['address']} | ${r['total_usd']:,.2f} | {r['networks']} |")
    md.append("")
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write("\n".join(md))

    with zipfile.ZipFile(os.path.join(args.out, "results.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, os.path.basename(p))
        for name in ("report.csv.gz", "excluded.csv.gz", "summary.json", "report.md"):
            fp = os.path.join(args.out, name)
            if os.path.exists(fp):
                z.write(fp, name)

    print(json.dumps({k: v for k, v in summary.items() if k != "top"}, indent=1))
    for r in selected[:25]:
        print(f"  {r['address']}  ${r['total_usd']:,.2f}  {r['networks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
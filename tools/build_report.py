"""Aggregate shard results, price balances, and build the consolidated report.

Input : results/shard-*.csv(.gz) produced by check_balances.py (partials welcome)
Output: report/report.csv.gz, report/summary.json, report/report.md,
        report/results.zip (all shard files + report bundled together)
"""

import argparse
import csv
import glob
import gzip
import io
import json
import os
import time
import urllib.request
import zipfile
from collections import defaultdict

from .networks import NETWORKS

PRICE_URL = "https://coins.llama.fi/prices/current/"


def open_any(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt")
    return open(path, "rt")


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
                    v = rec["native"].get(net, 0)
                    rec["native"][net] = v + wei
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


def fetch_prices(keys):
    out = {}
    for i in range(0, len(keys), 100):
        url = PRICE_URL + ",".join(keys[i:i + 100])
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=90) as r:
                    out.update(json.load(r).get("coins") or {})
                break
            except Exception:  # noqa: BLE001
                time.sleep(2 + attempt * 2)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="report")
    ap.add_argument("--min-usd", type=float, default=1.0)
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.results, "shard-*.csv")) +
                   glob.glob(os.path.join(args.results, "shard-*.csv.gz")))
    if not paths:
        raise SystemExit("no shard results found")
    agg, total_rows = load_rows(paths)
    print(f"loaded {total_rows} rows across {len(paths)} shards; {len(agg)} addresses with balances")

    price_keys = set()
    for net, cfg in NETWORKS.items():
        if cfg["wrapped"]:
            price_keys.add(f"{cfg['slug']}:{cfg['wrapped']}")
    for a, rec in agg.items():
        for net, toks in rec["tokens"].items():
            cfg = NETWORKS.get(net)
            if cfg:
                for t, _v in toks:
                    price_keys.add(f"{cfg['slug']}:{t}")
    prices = fetch_prices(sorted(price_keys))
    print(f"prices resolved: {len(prices)}")

    os.makedirs(args.out, exist_ok=True)
    rows, unpriced = [], []
    for a, rec in agg.items():
        n_usd = 0.0
        n_detail = []
        for net, wei in rec["native"].items():
            cfg = NETWORKS.get(net)
            if not cfg or not cfg["wrapped"]:
                unpriced.append({"address": a, "network": net, "token": "native", "raw": str(wei)})
                continue
            p = (prices.get(f"{cfg['slug']}:{cfg['wrapped']}") or {}).get("price") or 0.0
            usd = wei / 1e18 * p
            n_usd += usd
            n_detail.append(f"{cfg['symbol']}:{wei/1e18:.6g}:{usd:.2f}")
        t_usd = 0.0
        t_detail = []
        for net, toks in rec["tokens"].items():
            cfg = NETWORKS.get(net)
            if not cfg:
                continue
            for t, raw in toks:
                meta = prices.get(f"{cfg['slug']}:{t}") or {}
                price, dec = meta.get("price"), meta.get("decimals")
                if not price or dec is None:
                    unpriced.append({"address": a, "network": net, "token": t, "raw": str(raw)})
                    continue
                usd = raw / (10 ** int(dec)) * float(price)
                t_usd += usd
                t_detail.append(f"{meta.get('symbol') or t[:10]}:{usd:.2f}")
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

    with gzip.open(os.path.join(args.out, "report.csv.gz"), "wt") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["address", "native_usd", "token_usd", "total_usd", "networks",
                            "native_detail", "token_detail"])
        w.writeheader()
        for r in selected:
            w.writerow(r)
    with gzip.open(os.path.join(args.out, "unpriced.csv.gz"), "wt") as f:
        w = csv.DictWriter(f, fieldnames=["address", "network", "token", "raw"])
        w.writeheader()
        for r in unpriced:
            w.writerow(r)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "shards": len(paths),
        "addresses_with_balances": len(agg),
        "addresses_above_threshold": len(selected),
        "threshold_usd": args.min_usd,
        "total_usd": round(total_usd, 2),
        "unpriced_entries": len(unpriced),
        "top": selected[:100],
    }
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)

    md = [
        "# Consolidated balance report", "",
        f"_Generated {summary['generated_at']}_", "",
        f"- shards processed: **{len(paths)}**",
        f"- addresses with non-zero balances: **{len(agg):,}**",
        f"- above ${args.min_usd:.2f}: **{len(selected):,}**",
        f"- total USD (priced): **${total_usd:,.2f}**",
        f"- unpriced entries: {len(unpriced):,}", "",
        "| # | address | USD | networks |",
        "|---:|---|---:|---|",
    ]
    for i, r in enumerate(selected[:100], 1):
        md.append(f"| {i} | {r['address']} | ${r['total_usd']:,.2f} | {r['networks']} |")
    md.append("")
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write("\n".join(md))

    # bundle everything
    with zipfile.ZipFile(os.path.join(args.out, "results.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, os.path.basename(p))
        for name in ("report.csv.gz", "unpriced.csv.gz", "summary.json", "report.md"):
            fp = os.path.join(args.out, name)
            if os.path.exists(fp):
                z.write(fp, name)

    print(json.dumps({k: v for k, v in summary.items() if k != "top"}, indent=1))
    for r in selected[:25]:
        print(f"  {r['address']}  ${r['total_usd']:,.2f}  {r['networks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

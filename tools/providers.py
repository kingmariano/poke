"""RPC provider selection.

Native balances are served by general-purpose RPC providers (several keys
available, tried in order with failover). ERC-20 token balances and pricing use
the dedicated token provider only.

All endpoints are read from environment variables supplied as CI secrets; no
endpoint is recorded in the repository.
"""

import os
from typing import Dict, List

# network -> ordered native RPC endpoints (built from available provider keys)
_NATIVE_TEMPLATES: Dict[str, List[str]] = {
    "eth-mainnet": [
        "drpc:ethereum",
        "ankr:eth",
        "infura:mainnet",
        "nodereal:eth",
    ],
    "bnb-mainnet": [
        "drpc:bsc",
        "ankr:bsc",
    ],
    "base-mainnet": [
        "drpc:base",
        "ankr:base",
        "infura:base",
    ],
    "arb-mainnet": [
        "drpc:arbitrum",
        "ankr:arbitrum",
        "infura:arbitrum",
    ],
    "polygon-mainnet": [
        "drpc:polygon",
        "ankr:polygon",
        "infura:polygon",
    ],
    "opt-mainnet": [
        "drpc:optimism",
        "infura:optimism",
    ],
}

_DRPC_NET = {
    "ethereum": "ethereum", "bsc": "bsc", "base": "base",
    "arbitrum": "arbitrum", "polygon": "polygon", "optimism": "optimism",
}


def _resolve(spec: str):
    provider, _, net = spec.partition(":")
    if provider == "drpc":
        key = os.environ.get("DRPC_API_KEY")
        if key:
            return f"https://lb.drpc.org/ogrpc?network={_DRPC_NET.get(net, net)}&dkey={key}"
    elif provider == "ankr":
        key = os.environ.get("ANKR_API_KEY")
        if key:
            return f"https://rpc.ankr.com/{net}/{key}"
    elif provider == "infura":
        key = os.environ.get("INFURA_API_KEY")
        if key:
            host = "mainnet" if net == "mainnet" else f"{net}-mainnet"
            return f"https://{host}.infura.io/v3/{key}"
    elif provider == "nodereal":
        key = os.environ.get("NODEREAL_ETH_RPC_URL")
        if key and net == "eth":
            return key
    return None


def native_urls(network: str) -> List[str]:
    """Ordered list of usable native RPC endpoints for a network."""
    out = []
    for spec in _NATIVE_TEMPLATES.get(network, []):
        url = _resolve(spec)
        if url:
            out.append(url)
    # dedicated token provider also serves native as a last resort
    alch = os.environ.get("ALCHEMY_API_KEY")
    if alch:
        out.append(f"https://{network}.g.alchemy.com/v2/{alch}")
    return out


def token_url(network: str):
    """Token provider endpoint (used for ERC-20 balances and pricing)."""
    key = os.environ.get("TOKEN_API_KEY") or os.environ.get("ALCHEMY_API_KEY")
    return f"https://{network}.g.alchemy.com/v2/{key}" if key else None
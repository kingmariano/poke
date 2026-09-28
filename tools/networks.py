"""EVM network registry for the balance check (top-6 chains by value).

Each entry:
  id       : chain id
  symbol   : native symbol
  slug     : price source chain slug
  wrapped  : wrapped-native contract (used for pricing via by-address)
  tokens   : whether ERC-20 balances are checked (via the token provider)
"""

NETWORKS = {
    "eth-mainnet":     {"id": 1,     "symbol": "ETH",  "slug": "ethereum", "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "bnb-mainnet":     {"id": 56,    "symbol": "BNB",  "slug": "bsc",      "wrapped": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", "tokens": True},
    "base-mainnet":    {"id": 8453,  "symbol": "ETH",  "slug": "base",     "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "arb-mainnet":     {"id": 42161, "symbol": "ETH",  "slug": "arbitrum", "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "polygon-mainnet": {"id": 137,   "symbol": "POL",  "slug": "polygon",  "wrapped": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", "tokens": True},
    "opt-mainnet":     {"id": 10,    "symbol": "ETH",  "slug": "optimism", "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
}

TOKEN_CANDIDATES = [n for n, c in NETWORKS.items() if c["tokens"]]

NATIVE_SYMBOL = {
    "ethereum": "ETH", "bsc": "BNB", "base": "ETH",
    "arbitrum": "ETH", "polygon": "POL", "optimism": "ETH",
}
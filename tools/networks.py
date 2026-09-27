"""Network registry: EVM networks reachable with the configured RPC provider.

Fields per network:
  id       : chain id
  symbol   : native symbol
  slug     : price source chain slug
  wrapped  : wrapped-native contract used to price the native asset ('' -> flag unpriced)
  tokens   : whether the provider exposes an ERC-20 balance endpoint
"""

NETWORKS = {
    "eth-mainnet":       {"id": 1,      "symbol": "ETH",  "slug": "ethereum",  "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "opt-mainnet":       {"id": 10,     "symbol": "ETH",  "slug": "optimism",  "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "rootstock-mainnet": {"id": 30,     "symbol": "RBTC", "slug": "rootstock", "wrapped": "0x542fDA317318eBF1d3DEAf76E0b632741A7e677d", "tokens": True},
    "bnb-mainnet":       {"id": 56,     "symbol": "BNB",  "slug": "bsc",       "wrapped": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", "tokens": True},
    "gnosis-mainnet":    {"id": 100,    "symbol": "xDAI", "slug": "xdai",      "wrapped": "0xe91D153E0b41518A2Ce8Dd3D7944Fa863463a97d", "tokens": True},
    "unichain-mainnet":  {"id": 130,    "symbol": "ETH",  "slug": "unichain",  "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "polygon-mainnet":   {"id": 137,    "symbol": "POL",  "slug": "polygon",   "wrapped": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", "tokens": True},
    "monad-mainnet":     {"id": 143,    "symbol": "MON",  "slug": "monad",     "wrapped": "0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A", "tokens": True},
    "metis-mainnet":     {"id": 1088,   "symbol": "METIS","slug": "metis",     "wrapped": "0x75cb093E4D61d2A2e65D8e0BBb01DE8d89b53481", "tokens": True},
    "moonbeam-mainnet":  {"id": 1284,   "symbol": "GLMR", "slug": "moonbeam",  "wrapped": "0xAcc15dC74880C9944775448304B263D191c6077F", "tokens": True},
    "opbnb-mainnet":     {"id": 204,    "symbol": "BNB",  "slug": "op_bnb",    "wrapped": "0x4200000000000000000000000000000000000006", "tokens": True},
    "zksync-mainnet":    {"id": 324,    "symbol": "ETH",  "slug": "era",       "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "zetachain-mainnet": {"id": 7000,   "symbol": "ZETA", "slug": "zetachain", "wrapped": "0x5F0b1a82749cb4E2278EC87F8BF6B054C0370f83", "tokens": True},
    "sei-mainnet":       {"id": 1329,   "symbol": "SEI",  "slug": "sei",       "wrapped": "0xE30feDd158A2e3b13e9badaeABaFc5516e95e8C7", "tokens": True},
    "astar-mainnet":     {"id": 592,    "symbol": "ASTR", "slug": "astar",     "wrapped": "0xAeaaf0e2c81Af264101B9129C00F4440cCF0F720", "tokens": True},
    "shape-mainnet":     {"id": 360,    "symbol": "ETH",  "slug": "shape",     "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "worldchain-mainnet": {"id": 480,   "symbol": "ETH",  "slug": "worldchain","wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "story-mainnet":     {"id": 1514,   "symbol": "IP",   "slug": "story",     "wrapped": "0x1514000000000000000000000000000000000000", "tokens": True},
    "soneium-mainnet":   {"id": 1868,   "symbol": "ETH",  "slug": "soneium",   "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "ronin-mainnet":     {"id": 2020,   "symbol": "RON",  "slug": "ronin",     "wrapped": "0xe514d9DEB7966c8BE0ca922de8a064264eA6bcd4", "tokens": True},
    "abstract-mainnet":  {"id": 2741,   "symbol": "ETH",  "slug": "abstract",  "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "base-mainnet":      {"id": 8453,   "symbol": "ETH",  "slug": "base",      "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "apechain-mainnet":  {"id": 33139,  "symbol": "APE",  "slug": "apechain",  "wrapped": "", "tokens": True},
    "arb-mainnet":       {"id": 42161,  "symbol": "ETH",  "slug": "arbitrum",  "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "celo-mainnet":      {"id": 42220,  "symbol": "CELO", "slug": "celo",      "wrapped": "", "tokens": True},
    "avax-mainnet":      {"id": 43114,  "symbol": "AVAX", "slug": "avax",      "wrapped": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "tokens": True},
    "mantle-mainnet":    {"id": 5000,   "symbol": "MNT",  "slug": "mantle",    "wrapped": "0x78c1b0C915c4FAA5FffA6CAbf0219DA63d7f4cb8", "tokens": False},
    "sonic-mainnet":     {"id": 146,    "symbol": "S",    "slug": "sonic",     "wrapped": "0x039e2fB66102314Ce7b64Ce5Ce3E5183bc94aD38", "tokens": False},
    "kaia-mainnet":      {"id": 8217,   "symbol": "KAIA", "slug": "kaia",      "wrapped": "", "tokens": False},
    "ink-mainnet":       {"id": 57073,  "symbol": "ETH",  "slug": "ink",       "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "linea-mainnet":     {"id": 59144,  "symbol": "ETH",  "slug": "linea",     "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "berachain-mainnet": {"id": 80094,  "symbol": "BERA", "slug": "berachain", "wrapped": "0x6969696969696969696969696969696969696969", "tokens": True},
    "blast-mainnet":     {"id": 81457,  "symbol": "ETH",  "slug": "blast",     "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "scroll-mainnet":    {"id": 534352, "symbol": "ETH",  "slug": "scroll",    "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "zora-mainnet":      {"id": 7777777,"symbol": "ETH",  "slug": "zora",      "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": True},
    "bob-mainnet":       {"id": 60808,  "symbol": "ETH",  "slug": "bob",       "wrapped": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "tokens": False},
    "tempo-mainnet":     {"id": 4217,   "symbol": "TEMPO","slug": "tempo",     "wrapped": "", "tokens": False},
}

# networks to attempt ERC-20 checks on (preflight removes unsupported ones)
TOKEN_CANDIDATES = [n for n, cfg in NETWORKS.items() if cfg["tokens"]]

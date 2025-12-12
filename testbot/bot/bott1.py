import time
import asyncio
import json
import os
import logging

# Try package absolute import first (works when run from project root or with -m),
# then fall back to relative import (works when executed as a package), then disable.
try:
    from bot.scrapping import scrape_basescan_upward_tokens
except Exception:
    try:
        from .scrapping import scrape_basescan_upward_tokens
    except Exception:
        import traceback
        logging.exception("Failed to import scrapping (will treat as unavailable):\n%s", traceback.format_exc())
        scrape_basescan_upward_tokens = None
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import websockets
try:
    # use the Selenium scraper to collect trending tokens
    try:
        from bot.scapper_selenium import collect_token_links
    except Exception:
        from .scapper_selenium import collect_token_links
except Exception:
    collect_token_links = None

# configure logging if not already configured
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

from web3 import Web3
from decimal import Decimal, getcontext
import requests

# Handle different web3.py versions
try:
    from web3.middleware import geth_poa_middleware
except ImportError:
    try:
        from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware
    except ImportError:
        geth_poa_middleware = None

# Set high precision for calculations
getcontext().prec = 28

@dataclass
class DEXInfo:
    name: str
    router_address: str
    factory_address: str
    websocket_url: str

@dataclass
class ArbitrageOpportunity:
    token_address: str
    token_symbol: str
    stable_token: str
    amount: int
    buy_dex: str
    sell_dex: str
    buy_price: float
    sell_price: float
    expected_profit: float
    gas_cost: float
    net_profit: float
    timestamp: datetime

class ArbitrageMonitor:
    def __init__(self, rpc_url: str, private_key: str, contract_address: str):
        # Validate inputs before proceeding
        if not self.validate_inputs(rpc_url, private_key, contract_address):
            raise ValueError("Invalid configuration provided")
            
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Add POA middleware if available (for Base network)
        if geth_poa_middleware:
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            
        self.private_key = private_key
        self.account = self.w3.eth.account.from_key(private_key)
        self.contract_address = contract_address
        
        # DEX configurations for Base chain
        self.dexes = {
            'uniswap': DEXInfo(
                name='Uniswap V2',
                router_address='0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24',
                factory_address='0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6',
                websocket_url='wss://base-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY'
            ),
            'pancake': DEXInfo(
                name='PancakeSwap V2',
                router_address='0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb',
                factory_address='0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E',
                websocket_url='wss://base-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY'
            )
        }
        
        # Stable token addresses on Base
        self.stable_tokens = {
            'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
            'USDbC': '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA'
        }
        
        # Contract ABI (simplified)
        self.contract_abi = [
            {
                "inputs": [
                    {
                        "components": [
                            {"name": "tokenA", "type": "address"},
                            {"name": "tokenB", "type": "address"},
                            {"name": "flashLoanAmount", "type": "uint256"},
                            {"name": "dexLowPrice", "type": "address"},
                            {"name": "dexHighPrice", "type": "address"},
                            {"name": "user", "type": "address"},
                            {"name": "minProfit", "type": "uint256"}
                        ],
                        "name": "params",
                        "type": "tuple"
                    }
                ],
                "name": "executeArbitrage",
                "outputs": [],
                "type": "function"
            },
            {
                "inputs": [
                    {"name": "tokenA", "type": "address"},
                    {"name": "tokenB", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "dexLowPrice", "type": "address"},
                    {"name": "dexHighPrice", "type": "address"}
                ],
                "name": "calculateArbitrageProfit",
                "outputs": [
                    {"name": "expectedProfit", "type": "uint256"},
                    {"name": "profitable", "type": "bool"}
                ],
                "type": "function"
            }
        ]
        
        self.contract = self.w3.eth.contract(
            address=self.contract_address,
            abi=self.contract_abi
        )
        
        # Router ABI for price queries
        self.router_abi = [
            {
                "inputs": [
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "path", "type": "address[]"}
                ],
                "name": "getAmountsOut",
                "outputs": [{"name": "amounts", "type": "uint256[]"}],
                "type": "function"
            }
        ]

        # Minimal factory and pair ABI used for event decoding and pair discovery
        self.factory_abi = [
            {"constant": True, "inputs": [{"name": "tokenA","type": "address"},{"name":"tokenB","type":"address"}], "name": "getPair", "outputs": [{"name":"pair","type":"address"}], "type":"function"}
        ]

        # Uniswap V2 style pair ABI (token0, token1, Swap event)
        self.pair_abi = [
            {"constant": True, "inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "type": "function"},
            {"anonymous": False, "inputs": [
                {"indexed": True, "internalType": "address", "name": "sender", "type": "address"},
                {"indexed": False, "internalType": "uint256", "name": "amount0In", "type": "uint256"},
                {"indexed": False, "internalType": "uint256", "name": "amount1In", "type": "uint256"},
                {"indexed": False, "internalType": "uint256", "name": "amount0Out", "type": "uint256"},
                {"indexed": False, "internalType": "uint256", "name": "amount1Out", "type": "uint256"},
                {"indexed": True, "internalType": "address", "name": "to", "type": "address"}
            ], "name": "Swap", "type": "event"}
        ]
        
        # Monitoring state
        self.selected_tokens = []
        self.selected_dexes = []
        self.is_monitoring = False
        self.price_cache = {}
        self.last_opportunity_check = {}
        
        # Configuration
        self.min_profit_threshold = 10  # Minimum profit in USD
        self.max_flash_loan_amount = 10000  # Maximum flash loan in USD
        self.price_check_interval = 5  # Seconds between price checks
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('arbitrage_bot.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def validate_inputs(self, rpc_url: str, private_key: str, contract_address: str) -> bool:
        """Validate configuration inputs"""
        # Check if inputs are placeholder values
        if private_key == "your_private_key_here":
            print("ERROR: Please replace 'your_private_key_here' with your actual private key")
            return False
        
        if contract_address == "your_deployed_contract_address":
            print("ERROR: Please replace 'your_deployed_contract_address' with your actual contract address")
            return False
            
        if "YOUR_ALCHEMY_KEY" in rpc_url:
            print("ERROR: Please replace 'YOUR_ALCHEMY_KEY' with your actual Alchemy API key")
            return False
        
        # Validate private key format (64 hex characters, optionally with 0x prefix)
        clean_private_key = private_key.replace("0x", "")
        if len(clean_private_key) != 64:
            print("ERROR: Private key must be 64 hexadecimal characters")
            return False
        
        try:
            int(clean_private_key, 16)
        except ValueError:
            print("ERROR: Private key contains non-hexadecimal characters")
            return False
        
        # Validate contract address format
        if not contract_address.startswith("0x") or len(contract_address) != 42:
            print("ERROR: Contract address must be a valid Ethereum address (0x followed by 40 hex characters)")
            return False
        
        return True

    def load_config_from_env(self):
        """Load configuration from environment variables"""
        rpc_url = os.getenv('RPC_URL')
        private_key = os.getenv('PRIVATE_KEY')
        contract_address = os.getenv('CONTRACT_ADDRESS')
        
        if not all([rpc_url, private_key, contract_address]):
            print("Missing environment variables. Please set:")
            print("- RPC_URL: Your Base network RPC URL")
            print("- PRIVATE_KEY: Your wallet private key")
            print("- CONTRACT_ADDRESS: Your deployed arbitrage contract address")
            return None, None, None
        
        return rpc_url, private_key, contract_address

    def load_selected_tokens(self, file_path: str = 'selected_tokens.json'):
        """Load selected tokens from the scanner output"""
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                self.selected_tokens = data['tokens']
                self.logger.info(f"Loaded {len(self.selected_tokens)} tokens for monitoring")
                return True
        except FileNotFoundError:
            self.logger.error(f"Token selection file {file_path} not found. Run the scanner first.")
            return False
        except Exception as e:
            self.logger.error(f"Error loading selected tokens: {e}")
            return False

    def scrape_and_choose_tokens(self, limit: int = 20, filename: str = 'selected_tokens.json',
                                auto_select: bool = False, auto_select_count: int = 10):
        """Run the Selenium scraper (if available), present results, let user choose tokens to monitor.
        If auto_select is True the top `auto_select_count` tokens are selected non-interactively.
        Saves selection to filename as {"tokens": [...] } where each token is {address, symbol, name, trend}.
        """
        if not collect_token_links:
            self.logger.warning("Selenium scraper not available (missing dependencies). Please create selected_tokens.json manually.")
            return False

        try:
            raw = collect_token_links(limit=limit)
        except Exception as e:
            self.logger.error(f"Scraper failed: {e}")
            return False

        # Build token records and assign a simple trend: top half '+' else '-'
        tokens = []
        half = max(1, len(raw) // 2)
        for i, (addr, name, sym) in enumerate(raw):
            tokens.append({
                "address": addr,
                "symbol": (sym or "UNKNOWN"),
                "name": (name or ""),
                "trend": "+" if i < half else "-"
            })

        # If auto_select, pick the top `auto_select_count` tokens
        if auto_select:
            chosen = tokens[:min(auto_select_count, len(tokens))]
            try:
                with open(filename, "w") as f:
                    json.dump({"tokens": chosen}, f, indent=2)
                self.selected_tokens = chosen
                self.logger.info(f"Auto-selected {len(chosen)} tokens and saved to {filename}")
                return True
            except Exception as e:
                self.logger.error(f"Failed to save auto-selected tokens: {e}")
                return False

        # Save to file
        try:
            with open(filename, "w") as f:
                json.dump({"tokens": tokens}, f, indent=2)
            self.logger.info(f"Saved {len(tokens)} scraped tokens to {filename}")
        except Exception as e:
            self.logger.error(f"Failed to save scraped tokens: {e}")
            return False

        # Present a simple table and ask user to choose indices
        print("\nScraped trending tokens:")
        print(f"{'Idx':>3}  {'Symbol':<8}  {'Token':<30}  {'Address':42}  {'Trend'}")
        print("-" * 100)
        for idx, t in enumerate(tokens, start=1):
            print(f"{idx:>3}.  {t['symbol']:<8}  {t['name'][:30]:<30}  {t['address']:<42}  {t['trend']}")

        selection = input("\nEnter token indices to monitor (comma-separated, or 'all'): ").strip()
        chosen = []
        if selection.lower() == "all":
            chosen = tokens
        else:
            try:
                idxs = [int(x.strip()) - 1 for x in selection.split(",") if x.strip()]
                for i in idxs:
                    if 0 <= i < len(tokens):
                        chosen.append(tokens[i])
            except Exception:
                print("Invalid selection. No tokens selected.")

        if not chosen:
            print("No tokens selected. Exiting scrape/selection.")
            return False

        # Persist chosen selection as the active tokens
        try:
            with open(filename, "w") as f:
                json.dump({"tokens": chosen}, f, indent=2)
            self.selected_tokens = chosen
            self.logger.info(f"User selected {len(chosen)} tokens for monitoring")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save selected tokens: {e}")
            return False

    def select_dexes(self):
        """Allow user to select DEXes to monitor"""
        print("\nAvailable DEXes:")
        for i, (key, dex) in enumerate(self.dexes.items(), 1):
            print(f"{i}. {dex.name}")
        
        while True:
            try:
                selection = input("Enter DEX numbers (comma-separated): ")

                # Split by comma, remove spaces, and convert to integers
                indices = [int(x.strip()) - 1 for x in selection.split(',')]

                dex_keys = list(self.dexes.keys())
                selected_dexes = []
                
                for idx in indices:
                    if 0 <= idx < len(dex_keys):
                        dex_key = dex_keys[idx]
                        selected_dexes.append(dex_key)
                        print(f"Selected: {self.dexes[dex_key].name}")
                    else:
                        print(f"Invalid selection: {idx + 1}")
                
                if len(selected_dexes) >= 2:
                    self.selected_dexes = selected_dexes
                    break
                else:
                    print("Please select at least 2 DEXes for arbitrage.")
                    
            except ValueError:
                print("Invalid input. Please enter numbers separated by commas.")

    async def get_token_price(self, token_address: str, stable_token: str, dex_key: str, amount: int = None) -> Optional[float]:
        """Get token price from a specific DEX (runs blocking web3 calls in a thread).
        Prefer cached swap-derived price if available, otherwise fall back to router getAmountsOut.
        """
        try:
            key = token_address.lower()
            # prefer swap-derived cached price
            if key in self.price_cache and dex_key in self.price_cache[key]:
                return self.price_cache[key][dex_key]

            # fallback to on-chain router call (existing implementation)
            token_addr = self.w3.to_checksum_address(token_address)
            stable_addr = self.w3.to_checksum_address(self.stable_tokens[stable_token])
            router_addr = self.w3.to_checksum_address(self.dexes[dex_key].router_address)

            # Helper to get decimals (blocking)
            def _decimals(addr):
                erc20_abi = [{"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}]
                try:
                    c = self.w3.eth.contract(address=addr, abi=erc20_abi)
                    return int(c.functions.decimals().call())
                except Exception:
                    return 18

            decimals = await asyncio.to_thread(_decimals, token_addr)

            if amount is None:
                amount = 10 ** decimals

            # Blocking router call wrapped in thread
            def _call_get_amounts_out():
                router = self.w3.eth.contract(address=router_addr, abi=self.router_abi)
                path = [token_addr, stable_addr]
                return router.functions.getAmountsOut(amount, path).call()

            try:
                amounts_out = await asyncio.to_thread(_call_get_amounts_out)
            except Exception as e:
                # no pair / revert / other issue
                self.logger.debug(f"getAmountsOut failed for {token_addr} on {dex_key}: {e}")
                return None

            # Normalize: amounts_out[1] is in stable token units (e.g. USDC has 6 decimals)
            stable_decimals = await asyncio.to_thread(_decimals, stable_addr)
            price = (float(amounts_out[1]) / (10 ** stable_decimals)) / (float(amount) / (10 ** decimals))
            return price

        except Exception as e:
            self.logger.debug(f"Error getting price for {token_address} on {dex_key}: {e}")
            return None

    async def get_pair_address(self, tokenA: str, tokenB: str, dex_key: str) -> Optional[str]:
        """Return pair address for tokenA/tokenB on given dex via factory.getPair."""
        try:
            factory_addr = self.dexes[dex_key].factory_address
            factory = self.w3.eth.contract(address=self.w3.to_checksum_address(factory_addr), abi=self.factory_abi)

            def _get_pair():
                return factory.functions.getPair(self.w3.to_checksum_address(tokenA), self.w3.to_checksum_address(tokenB)).call()

            pair = await asyncio.to_thread(_get_pair)

            if not pair or pair == '0x0000000000000000000000000000000000000000':
                return None
            return pair
        except Exception as e:
            self.logger.debug(f"get_pair_address error for {tokenA}/{tokenB} on {dex_key}: {e}")
            return None

    async def subscribe_to_swap(self, token_address: str, stable_token: str, dex_key: str):
        """Listen for Swap events on the pair and update price_cache with last swap-derived price."""
        try:
            pair = await self.get_pair_address(token_address, self.stable_tokens[stable_token], dex_key)
            if not pair:
                self.logger.debug(f"No pair for {token_address} / {stable_token} on {dex_key}")
                return

            pair = self.w3.to_checksum_address(pair)
            pair_contract = self.w3.eth.contract(address=pair, abi=self.pair_abi)

            # create a filter (polling) for new logs on the pair
            event_filter = self.w3.eth.filter({'address': pair, 'fromBlock': 'latest'})
            self.logger.info(f"Listening swaps for {token_address} on {dex_key} pair {pair}")

            while self.is_monitoring:
                try:
                    entries = await asyncio.to_thread(event_filter.get_new_entries)
                    for e in entries:
                        try:
                            # decode using contract event ABI
                            ev = pair_contract.events.Swap().processLog(e)
                            a0in = int(ev['args']['amount0In'])
                            a1in = int(ev['args']['amount1In'])
                            a0out = int(ev['args']['amount0Out'])
                            a1out = int(ev['args']['amount1Out'])

                            # find token0/token1 ordering
                            token0 = await asyncio.to_thread(pair_contract.functions.token0().call)
                            token1 = await asyncio.to_thread(pair_contract.functions.token1().call)

                            token0 = token0.lower()
                            token1 = token1.lower()
                            taddr = token_address.lower()
                            stable_addr = self.stable_tokens[stable_token].lower()

                            # determine token amount and stable amount moved in the swap
                            if token0 == taddr and token1 == stable_addr:
                                token_amount = a0in if a0in > 0 else a0out
                                stable_amount = a1out if a1out > 0 else a1in
                            elif token1 == taddr and token0 == stable_addr:
                                token_amount = a1in if a1in > 0 else a1out
                                stable_amount = a0out if a0out > 0 else a0in
                            else:
                                # not the pair we expected
                                continue

                            if token_amount == 0:
                                continue

                            # normalize decimals
                            token_dec = await self.get_token_decimals(token_address)
                            stable_dec = await self.get_token_decimals(self.stable_tokens[stable_token])
                            price = (stable_amount / (10 ** stable_dec)) / (token_amount / (10 ** token_dec))

                            # update nested cache: price_cache[token_address_lower][dex_key] = price
                            key = token_address.lower()
                            if key not in self.price_cache:
                                self.price_cache[key] = {}
                            self.price_cache[key][dex_key] = float(price)
                            self.logger.debug(f"Swap price update {token_address} @ {dex_key} = {price:.6f} {stable_token}")

                        except Exception as decode_e:
                            self.logger.debug(f"Failed to process swap log: {decode_e}")
                    await asyncio.sleep(0.5)
                except Exception as fe:
                    self.logger.error(f"Swap listener error for {pair}: {fe}")
                    await asyncio.sleep(2)

        except Exception as e:
            self.logger.error(f"subscribe_to_swap error: {e}")

    async def start_swap_listeners(self):
        """Spawn swap listeners for each selected token/dex pair."""
        tasks = []
        for token in self.selected_tokens:
            taddr = token['address']
            for dex_key in self.selected_dexes:
                tasks.append(asyncio.create_task(self.subscribe_to_swap(taddr, 'USDC', dex_key)))
        # don't await here — listeners run in background while monitor_opportunities continues
        return tasks

    async def check_arbitrage_opportunity(self, token: dict, stable_token: str = 'USDC') -> Optional[ArbitrageOpportunity]:
        """Check for arbitrage opportunities for a given token"""
        token_address = token['address']
        token_symbol = token['symbol']
        
        prices = {}
        
        # Get prices from all selected DEXes
        for dex_key in self.selected_dexes:
            price = await self.get_token_price(token_address, stable_token, dex_key)
            if price:
                prices[dex_key] = price
        
        if len(prices) < 2:
            return None
        
        # Find the highest and lowest prices
        min_price_dex = min(prices, key=prices.get)
        max_price_dex = max(prices, key=prices.get)
        
        min_price = prices[min_price_dex]
        max_price = prices[max_price_dex]
        
        # Calculate potential profit percentage
        price_diff_percent = ((max_price - min_price) / min_price) * 100
        
        # Skip if price difference is too small (less than 1%)
        if price_diff_percent < 1.0:
            return None
        
        # Calculate optimal flash loan amount
        flash_loan_amount = min(self.max_flash_loan_amount, 5000)  # Start with $5k
        flash_loan_amount_wei = int(flash_loan_amount * 10**6)  # USDC has 6 decimals
        
        # Estimate gas cost (simplified)
        estimated_gas_cost = await self.estimate_gas_cost()
        
        # Calculate expected profit
        expected_profit_percent = price_diff_percent - 0.5  # Account for slippage and fees
        expected_profit = flash_loan_amount * (expected_profit_percent / 100)
        net_profit = expected_profit - estimated_gas_cost
        
        if net_profit > self.min_profit_threshold:
            return ArbitrageOpportunity(
                token_address=token_address,
                token_symbol=token_symbol,
                stable_token=stable_token,
                amount=flash_loan_amount_wei,
                buy_dex=min_price_dex,
                sell_dex=max_price_dex,
                buy_price=min_price,
                sell_price=max_price,
                expected_profit=expected_profit,
                gas_cost=estimated_gas_cost,
                net_profit=net_profit,
                timestamp=datetime.now()
            )
        
        return None

    async def estimate_gas_cost(self) -> float:
        """Estimate gas cost for arbitrage transaction"""
        try:
            # Get current gas price
            gas_price = self.w3.eth.gas_price
            
            # Estimate gas limit (arbitrage transactions are complex)
            estimated_gas_limit = 500000  # Conservative estimate
            
            # Calculate cost in ETH
            gas_cost_wei = gas_price * estimated_gas_limit
            gas_cost_eth = self.w3.from_wei(gas_cost_wei, 'ether')
            
            # Convert to USD (simplified - you'd want to get real ETH price)
            eth_price_usd = await self.get_eth_price()
            gas_cost_usd = float(gas_cost_eth) * eth_price_usd
            
            return gas_cost_usd
            
        except Exception as e:
            self.logger.error(f"Error estimating gas cost: {e}")
            return 10.0  # Default estimate

    async def get_eth_price(self) -> float:
        """Get current ETH price in USD"""
        try:
            response = requests.get(
                'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
                timeout=5
            )
            data = response.json()
            return data['ethereum']['usd']
        except:
            return 2000.0  # Default ETH price

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity) -> bool:
        """Execute the arbitrage trade"""
        try:
            self.logger.info(f"Executing arbitrage for {opportunity.token_symbol}")
            self.logger.info(f"Expected profit: ${opportunity.net_profit:.2f}")

            # Build ordered tuple matching the contract's params tuple
            params_tuple = (
                self.w3.to_checksum_address(opportunity.token_address),                          # tokenA
                self.w3.to_checksum_address(self.stable_tokens[opportunity.stable_token]),     # tokenB
                int(opportunity.amount),                                                        # flashLoanAmount (uint256)
                self.w3.to_checksum_address(self.dexes[opportunity.buy_dex].router_address),   # dexLowPrice
                self.w3.to_checksum_address(self.dexes[opportunity.sell_dex].router_address),  # dexHighPrice
                self.account.address,                                                           # user
                int(opportunity.expected_profit * 10**6)                                       # minProfit (USDC 6 decimals)
            )

            def _send_tx():
                transaction = self.contract.functions.executeArbitrage(params_tuple).build_transaction({
                    'from': self.account.address,
                    'gas': 600000,
                    'gasPrice': self.w3.eth.gas_price,
                    'nonce': self.w3.eth.get_transaction_count(self.account.address),
                })
                signed_tx = self.w3.eth.account.sign_transaction(transaction, self.private_key)
                # tolerate different SignedTransaction shapes and types
                raw = None
                if hasattr(signed_tx, "rawTransaction"):
                    raw = signed_tx.rawTransaction
                elif hasattr(signed_tx, "raw_transaction"):
                    raw = signed_tx.raw_transaction
                elif hasattr(signed_tx, "rawTx"):
                    raw = signed_tx.rawTx
                elif isinstance(signed_tx, (bytes, bytearray)):
                    raw = signed_tx
                elif isinstance(signed_tx, str) and signed_tx.startswith("0x"):
                    raw = bytes.fromhex(signed_tx[2:])
                else:
                    try:
                        raw = signed_tx["rawTransaction"]
                    except Exception:
                        self.logger.error("Unknown signed_tx shape: %s; repr: %r", type(signed_tx), signed_tx)
                        raise

                # Fixed: use self.w3 instead of w3
                tx_hash = self.w3.eth.send_raw_transaction(raw)
                return tx_hash

            tx_hash = await asyncio.to_thread(_send_tx)
            self.logger.info(f"Transaction sent: {tx_hash.hex()}")

            receipt = await asyncio.to_thread(self.w3.eth.wait_for_transaction_receipt, tx_hash, 300)
            if receipt.get('status', 0) == 1:
                self.logger.info(f"Arbitrage executed successfully! Gas used: {receipt.get('gasUsed')}")
                return True
            else:
                self.logger.error("Transaction failed!")
                return False

        except Exception as e:
            self.logger.error(f"Error executing arbitrage: {e}")
            return False
    async def monitor_opportunities(self):
        """Main monitoring loop"""
        self.logger.info("Starting arbitrage monitoring...")
        self.is_monitoring = True

        # Start swap listeners (background tasks) to populate price_cache
        listener_tasks = await self.start_swap_listeners()
        
        while self.is_monitoring:
            try:
                for token in self.selected_tokens:
                    if not self.is_monitoring:
                        break

                    # Check for arbitrage opportunity
                    opportunity = await self.check_arbitrage_opportunity(token)

                    if opportunity:
                        self.logger.info(f"Arbitrage opportunity found for {opportunity.token_symbol}:")
                        self.logger.info(f"  Buy on {opportunity.buy_dex} at ${opportunity.buy_price:.6f}")
                        self.logger.info(f"  Sell on {opportunity.sell_dex} at ${opportunity.sell_price:.6f}")
                        self.logger.info(f"  Expected net profit: ${opportunity.net_profit:.2f}")

                        # Execute if profitable enough
                        if opportunity.net_profit > self.min_profit_threshold:
                            success = await self.execute_arbitrage(opportunity)
                            if success:
                                # Pause monitoring briefly after execution
                                self.logger.info("Pausing monitoring for 30 seconds...")
                                await asyncio.sleep(30)

                    # Small delay between token checks
                    await asyncio.sleep(1)

                # Wait before next full cycle
                await asyncio.sleep(self.price_check_interval)

            except KeyboardInterrupt:
                self.logger.info("Monitoring interrupted by user")
                break
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(10)  # Wait before retrying

        # cancel background listeners
        for t in listener_tasks:
            t.cancel()
        
        self.logger.info("Arbitrage monitoring stopped")

    def stop_monitoring(self):
        """Stop the monitoring process"""
        self.is_monitoring = False

    def display_status(self):
        """Display current bot status"""
        print(f"\n{'='*60}")
        print("ARBITRAGE BOT STATUS")
        print(f"{'='*60}")
        print(f"Account: {self.account.address}")
        print(f"Contract: {self.contract_address}")
        print(f"Selected tokens: {len(self.selected_tokens)}")
        print(f"Selected DEXes: {', '.join(self.selected_dexes)}")
        print(f"Monitoring: {'Active' if self.is_monitoring else 'Inactive'}")
        print(f"Min profit threshold: ${self.min_profit_threshold}")
        print(f"Max flash loan: ${self.max_flash_loan_amount}")
        print(f"{'='*60}")

    erc20_abi = [
        {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
    ]

    async def get_token_decimals(self, token_address: str) -> int:
        try:
            token = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=self.erc20_abi)
            dec = await asyncio.to_thread(token.functions.decimals().call)
            return int(dec)
        except Exception:
            return 18

    def present_scraped_tokens(self, max_pages: int = 3, limit: int = 50) -> List[Dict]:
        """Run scraper (scrapping.py) synchronously, show table, prompt user to select tokens.
        Returns the chosen token records and stores them in self.selected_tokens.
        """
        if not scrape_basescan_upward_tokens:
            raise RuntimeError("scrape_basescan_upward_tokens not available (scrapping.py missing)")

        records = scrape_basescan_upward_tokens(max_pages=max_pages, limit=limit)
        if not records:
            raise RuntimeError("Scraper returned no tokens")

        # Present table
        print("\nScraped trending tokens:")
        print(f"{'Idx':>3}  {'Symbol':<8}  {'Token':<30}  {'Address':42}  {'Trend'}")
        print("-" * 110)
        for idx, t in enumerate(records, start=1):
            print(f"{idx:>3}.  {t.get('symbol',''):<8}  {t.get('name','')[:30]:<30}  {t.get('address',''):<42}  {t.get('trend','')}")
        print()

        selection = input("Enter token indices to monitor (comma-separated), or 'all' to select all, or 'none' to cancel: ").strip()
        chosen = []
        if selection.lower() == "all":
            chosen = records
        elif selection.lower() == "none" or selection == "":
            return []
        else:
            try:
                idxs = [int(x.strip()) - 1 for x in selection.split(",") if x.strip()]
                for i in idxs:
                    if 0 <= i < len(records):
                        chosen.append(records[i])
            except Exception:
                raise RuntimeError("Invalid selection")

        # persist selection for convenience
        selection_file = 'selected_tokens.json'
        try:
            with open(selection_file, "w", encoding="utf-8") as f:
                json.dump({"tokens": chosen}, f, indent=2)
        except Exception:
            logger.debug("Failed to save selected_tokens.json (non-fatal)")

        self.selected_tokens = chosen
        return chosen

def get_configuration():
    """Get configuration from user input or environment variables"""
    print("Arbitrage Bot Configuration")
    print("=" * 40)
    
    # Try to load from environment variables first
    rpc_url = os.getenv('RPC_URL')
    private_key = os.getenv('PRIVATE_KEY')
    contract_address = os.getenv('CONTRACT_ADDRESS')
    
    if all([rpc_url, private_key, contract_address]):
        print("Using configuration from environment variables...")
        return rpc_url, private_key, contract_address
    
    print("Environment variables not found. Please enter configuration manually:")
    print("\nNote: You can set these as environment variables:")
    print("export RPC_URL='your_rpc_url'")
    print("export PRIVATE_KEY='your_private_key'")
    print("export CONTRACT_ADDRESS='your_contract_address'")
    
    # Get RPC URL
    rpc_url = input("\nEnter Base network RPC URL: ").strip()
    if not rpc_url:
        print("ERROR: RPC URL is required")
        return None, None, None
    
    # Get private key
    private_key = input("Enter your private key (without 0x prefix): ").strip()
    if not private_key:
        print("ERROR: Private key is required")
        return None, None, None
    
    # Add 0x prefix if not present
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    
    # Get contract address
    contract_address = input("Enter your deployed contract address: ").strip()
    if not contract_address:
        print("ERROR: Contract address is required")
        return None, None, None
    
    return rpc_url, private_key, contract_address

async def main():
    print("🚀 Arbitrage Monitor Bot")
    print("=" * 40)
    
    # Get configuration
    rpc_url, private_key, contract_address = get_configuration()
    
    if not all([rpc_url, private_key, contract_address]):
        print("Configuration incomplete. Exiting...")
        return
    
    try:
        # Initialize the bot
        bot = ArbitrageMonitor(rpc_url, private_key, contract_address)
        
        selection_file = 'selected_tokens.json'
        if os.path.exists(selection_file):
            if not bot.load_selected_tokens(selection_file):
                print("Failed to load existing selected_tokens.json")
                return
        else:
            # Run scraper and prompt user (no selected_tokens.json required)
            if not scrape_basescan_upward_tokens:
                print("No scraper available (scrapping.py missing). Please create selected_tokens.json manually.")
                return
            try:
                # run scraper in background thread to avoid blocking event loop
                chosen = await asyncio.to_thread(bot.present_scraped_tokens, 3, 50)
            except Exception as e:
                print(f"Scraper/selection failed: {e}")
                return
            if not chosen:
                print("No tokens selected. Exiting.")
                return

        # Select DEXes to monitor
        bot.select_dexes()
        
        # Display status
        bot.display_status()
        
        # Ask user to confirm
        confirm = input("\nStart monitoring? (y/n): ")
        if confirm.lower().startswith('y'):
            # Start listeners and monitoring
            # monitor_opportunities will start swap listeners internally
            await bot.monitor_opportunities()
        else:
            print("Exiting without starting monitoring.")
            return
    except Exception as e:
        print(f"Fatal error: {e}")
        return

# Run the bot
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Unexpected error: {e}")
        time.sleep(5)  # Allow time to read the error


import time
import asyncio
import json
import os
import logging
import traceback
import sys
from termcolor import colored
from dotenv import load_dotenv

# --- Configuration & Logging Setup (PRO STYLE) ---
class ColoredFormatter(logging.Formatter):
    """Custom formatter to match the professional look of testbot.py"""
    def format(self, record):
        timestamp = self.formatTime(record, "%H:%M:%S")
        msg = record.getMessage()
        
        # Arbitrage Opportunity Box (Yellow/Gold)
        if "ARBITRAGE OPPORTUNITY DETECTED" in msg:
            message_body = msg.replace("ARBITRAGE OPPORTUNITY DETECTED", "").strip()
            formatted = (
                "\n" + "╔" + "═" * 78 + "╗\n" +
                "║" + colored("💎 ARBITRAGE OPPORTUNITY DETECTED 💎".center(78), 'yellow', attrs=['bold', 'blink']) + "║\n" +
                "╚" + "═" * 78 + "╝\n" + message_body
            )
            return f"{colored(timestamp, 'cyan')} {formatted}"
        
        # Swap Event Box (Cyan) - New for Event-Driven
        elif "SWAP DETECTED" in msg:
            message_body = msg.replace("SWAP DETECTED", "").strip()
            formatted = (
                "\n" + "┌" + "─" * 50 + "┐\n" +
                "│" + colored("⚡ SWAP DETECTED ⚡".center(50), 'cyan', attrs=['bold']) + "│\n" +
                "└" + "─" * 50 + "┘\n" + message_body
            )
            return f"{colored(timestamp, 'cyan')} {formatted}"

        # Price Update Box (Blue)
        elif "PRICE UPDATE" in msg:
            message_body = msg.replace("PRICE UPDATE", "").strip()
            formatted = (
                "\n" + "┌" + "─" * 50 + "┐\n" +
                "│" + colored("📊 PRICE UPDATE".center(50), 'blue', attrs=['bold']) + "│\n" +
                "└" + "─" * 50 + "┘\n" + message_body
            )
            return f"{colored(timestamp, 'cyan')} {formatted}"
            
        # Default Log Colors
        elif record.levelno == logging.INFO:
            return f"{colored(timestamp, 'cyan')} {colored('INFO', 'green')} {msg}"
        elif record.levelno == logging.WARNING:
            return f"{colored(timestamp, 'cyan')} {colored('WARN', 'yellow')} {msg}"
        elif record.levelno == logging.ERROR:
            return f"{colored(timestamp, 'cyan')} {colored('FAIL', 'red')} {msg}"
        
        return f"{timestamp} {record.levelname} {msg}"

# Setup Logger
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter())
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Changed to DEBUG to see pool checking details
logger.addHandler(handler)
# --- END PRO STYLE LOGGING SETUP ---


# --- Imports with robust fallbacks ---
# Add the current directory to sys.path to ensure local imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from bot.scrapping import scrape_basescan_upward_tokens
except (ImportError, ModuleNotFoundError) as e1:
    try:
        from scrapping import scrape_basescan_upward_tokens
    except (ImportError, ModuleNotFoundError) as e2:
        logger.warning(colored("Scraping dependencies missing (selenium/bs4). Using fallback token list.", "yellow"))
        
        # Define a fallback function that returns popular Base tokens
        def scrape_basescan_upward_tokens(max_pages=1, limit=50):
            return [
                {"address": "0x4200000000000000000000000000000000000006", "name": "Wrapped Ether", "symbol": "WETH"},
                {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "name": "USD Coin", "symbol": "USDC"},
                {"address": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", "name": "Dai Stablecoin", "symbol": "DAI"},
                {"address": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", "name": "Base Swap", "symbol": "BSWAP"},
                {"address": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", "name": "Coinbase Wrapped Staked ETH", "symbol": "cbETH"}
            ]

from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from web3 import Web3
from decimal import Decimal, getcontext
import requests

try:
    from web3.middleware import geth_poa_middleware
except ImportError:
    geth_poa_middleware = None

getcontext().prec = 28

# --- Data Classes ---
@dataclass
class DEXInfo:
    name: str
    router_address: str
    factory_address: str

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

# --- Main Bot Class ---
class ArbitrageMonitor:
    def __init__(self, rpc_url: str, private_key: str, contract_address: str):
        # Validate inputs
        if not rpc_url:
            raise ValueError("RPC_URL is missing. Check your .env file.")
        if not private_key:
            raise ValueError("PRIVATE_KEY is missing. Check your .env file.")
        if not contract_address:
            raise ValueError("CONTRACT_ADDRESS is missing. Check your .env file.")

        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 60}))
        if geth_poa_middleware:
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to the RPC provider.")
        
        self.private_key = private_key
        self.account = self.w3.eth.account.from_key(private_key)
        self.contract_address = self.w3.to_checksum_address(contract_address)
        
        # --- SAFE V2 DEX LIST (from newbot.py) ---
        self.dexes = {
            'uniswap_v2': DEXInfo('Uniswap V2 (Base)', '0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24', '0x8909dc15e40173ff4699343b6eb8132c65e18ec6'),
            'pancake': DEXInfo('PancakeSwap V2', '0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb', '0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E'),
            'baseswap': DEXInfo('BaseSwap', '0x327Df1E6de05895d2ab08513aaDD9313Fe505d86', '0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB'),
            'sushiswap_v2': DEXInfo('SushiSwap V2', '0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891', '0x71524B4f93c58fcbF659783284E38825f0622859'),
        }
        
        self.stable_tokens = {'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'}
        
        # --- ABIs ---
        self.contract_abi = json.loads('[{"inputs":[{"components":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"flashLoanAmount","type":"uint256"},{"name":"dexLowPrice","type":"address"},{"name":"dexHighPrice","type":"address"},{"name":"user","type":"address"},{"name":"minProfit","type":"uint256"}],"name":"params","type":"tuple"}],"name":"executeArbitrage","outputs":[],"type":"function"}]')
        self.router_abi = json.loads('[{"inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"name":"amounts","type":"uint256[]"}],"type":"function"}]')
        self.factory_abi = json.loads('[{"constant":true,"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"name":"pair","type":"address"}],"type":"function"}]')
        self.pair_abi = json.loads('[{"constant":true,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":true,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},{"anonymous":false,"inputs":[{"indexed":true,"name":"sender","type":"address"},{"indexed":false,"name":"amount0In","type":"uint256"},{"indexed":false,"name":"amount1In","type":"uint256"},{"indexed":false,"name":"amount0Out","type":"uint256"},{"indexed":false,"name":"amount1Out","type":"uint256"},{"indexed":true,"name":"to","type":"address"}],"name":"Swap","type":"event"}]')
        self.erc20_abi = json.loads('[{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')
        
        self.contract = self.w3.eth.contract(address=self.contract_address, abi=self.contract_abi)
        
        # --- State and Config ---
        self.selected_tokens = []
        self.selected_dexes = []
        self.is_monitoring = False
        self.price_cache = {}
        self._decimals_cache = {}
        self.min_profit_threshold = 10  # Minimum profit in USD to attempt execution
        self.max_flash_loan_amount = 5000 # Max flash loan in USD
        self.trade_lock = asyncio.Lock() # Prevents multiple trades at once
        
    def display_status(self):
        print(f"\n{'='*60}\nARBITRAGE BOT STATUS\n{'='*60}")
        print(f"Account: {self.account.address}")
        print(f"Contract: {self.contract_address}")
        print(f"Selected tokens: {len(self.selected_tokens)}")
        print(f"Selected DEXes: {', '.join([self.dexes[dex].name for dex in self.selected_dexes])}")
        print(f"Min Profit Threshold: ${self.min_profit_threshold}")
        print(f"Monitoring: {'Active' if self.is_monitoring else 'Inactive'}")
        print(f"{'='*60}")

    def select_dexes(self):
        print("\nAvailable DEXes:")
        dex_keys = list(self.dexes.keys())
        for i, key in enumerate(dex_keys, 1):
            print(f"{i}. {self.dexes[key].name}")
        while True:
            selection = input("Enter DEX numbers to monitor (e.g., 1,3): ").strip()
            try:
                indices = [int(x.strip()) - 1 for x in selection.split(',') if x.strip()]
                chosen_dexes = [dex_keys[i] for i in indices if 0 <= i < len(dex_keys)]
                if len(chosen_dexes) >= 2:
                    self.selected_dexes = chosen_dexes
                    break
                else: print("Please select at least 2 DEXes.")
            except (ValueError, IndexError): print("Invalid input.")

    def present_scraped_tokens(self, limit=50):
        if not scrape_basescan_upward_tokens:
            logger.error("Scraper function not available."); return False
        
        # Using scraping logic from existing bots
        try:
            records = scrape_basescan_upward_tokens(max_pages=3, limit=limit)
        except Exception as e:
            logger.error(f"Error calling scraper: {e}")
            records = []

        if not records:
            logger.error("Scraper returned no tokens."); return False
        
        print("\nScraped Trending Tokens:")
        print(f"{'Idx':>3} | {'Symbol':<10} | {'Name':<30} | {'Address':<42}"); print("-" * 90)
        for idx, t in enumerate(records, start=1):
            print(f"{idx:>3} | {t.get('symbol',''):<10} | {t.get('name','')[:30]:<30} | {t.get('address','')}")
        while True:
            selection = input("\nEnter token indices to monitor (e.g., 1,5,10 or 'all'): ").strip()
            if selection.lower() == 'all': self.selected_tokens = records; break
            try:
                idxs = [int(x.strip()) - 1 for x in selection.split(",") if x.strip()]
                chosen = [records[i] for i in idxs if 0 <= i < len(records)]
                if chosen: self.selected_tokens = chosen; break
                else: print("No valid tokens selected.")
            except (ValueError, IndexError): print("Invalid selection.")
        logger.info(f"Selected {len(self.selected_tokens)} tokens for monitoring.")
        return True

    async def get_token_decimals(self, token_address: str) -> int:
        token_address_lower = token_address.lower()
        if token_address_lower in self._decimals_cache: return self._decimals_cache[token_address_lower]
        try:
            token_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=self.erc20_abi)
            decimals = await asyncio.to_thread(token_contract.functions.decimals().call)
            self._decimals_cache[token_address_lower] = decimals
            return decimals
        except Exception: return 18

    async def get_token_price(self, token_address: str, stable_token: str, dex_key: str) -> Optional[float]:
        try:
            router_addr = self.w3.to_checksum_address(self.dexes[dex_key].router_address)
            router = self.w3.eth.contract(address=router_addr, abi=self.router_abi)
            token_decimals = await self.get_token_decimals(token_address)
            stable_decimals = await self.get_token_decimals(self.stable_tokens[stable_token])
            amount_in = 10 ** token_decimals # Price for 1 whole token
            path = [self.w3.to_checksum_address(token_address), self.w3.to_checksum_address(self.stable_tokens[stable_token])]
            amounts_out = await asyncio.to_thread(router.functions.getAmountsOut(amount_in, path).call)
            price = (amounts_out[1] / (10**stable_decimals))
            return price if 0 < price < 1e12 else None
        except Exception: return None

    async def get_pair_address(self, tokenA: str, tokenB: str, dex_key: str) -> Optional[str]:
        try:
            factory_addr = self.w3.to_checksum_address(self.dexes[dex_key].factory_address)
            factory = self.w3.eth.contract(address=factory_addr, abi=self.factory_abi)
            
            token_a_checksum = self.w3.to_checksum_address(tokenA)
            token_b_checksum = self.w3.to_checksum_address(tokenB)
            
            logger.debug(f"Checking pool on {self.dexes[dex_key].name}:")
            logger.debug(f"  Factory: {factory_addr}")
            logger.debug(f"  Token A: {token_a_checksum}")
            logger.debug(f"  Token B: {token_b_checksum}")
            
            pair_addr = await asyncio.to_thread(factory.functions.getPair(token_a_checksum, token_b_checksum).call)
            
            logger.debug(f"  Result: {pair_addr}")
            
            if pair_addr == '0x0000000000000000000000000000000000000000':
                logger.debug(f"  ❌ Pool does not exist (zero address returned)")
                return None
            else:
                logger.debug(f"  ✅ Pool found at {pair_addr}")
                return pair_addr
                
        except Exception as e:
            logger.error(f"Error checking pair on {dex_key}: {e}")
            return None

    async def estimate_gas_cost(self) -> float:
        try:
            gas_price = await asyncio.to_thread(self.w3.eth.get_gas_price)
            estimated_gas_limit = 600000
            gas_cost_wei = gas_price * estimated_gas_limit
            gas_cost_eth = self.w3.from_wei(gas_cost_wei, 'ether')
            response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd', timeout=5)
            eth_price_usd = response.json()['ethereum']['usd']
            return float(gas_cost_eth) * eth_price_usd
        except Exception as e:
            logger.warning(f"Could not estimate gas cost: {e}. Defaulting to $10.")
            return 10.0

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity):
        if not await self.trade_lock.acquire():
            logger.warning("Could not acquire trade lock, another trade is in progress.")
            return

        try:
            logger.info(f"🚀 EXECUTING ARBITRAGE for {opportunity.token_symbol} 🚀")
            logger.info(f"   Expected Net Profit: ${opportunity.net_profit:.2f}")
            
            params_tuple = (
                self.w3.to_checksum_address(opportunity.token_address),
                self.w3.to_checksum_address(self.stable_tokens[opportunity.stable_token]),
                int(opportunity.amount),
                self.w3.to_checksum_address(self.dexes[opportunity.buy_dex].router_address),
                self.w3.to_checksum_address(self.dexes[opportunity.sell_dex].router_address),
                self.account.address,
                int(opportunity.net_profit * (10**6) * 0.9) # minProfit (USDC 6 decimals), with 10% slippage
            )

            nonce = await asyncio.to_thread(self.w3.eth.get_transaction_count, self.account.address)
            gas_price = await asyncio.to_thread(self.w3.eth.get_gas_price)

            tx = await asyncio.to_thread(
                self.contract.functions.executeArbitrage(params_tuple).build_transaction,
                {'from': self.account.address, 'gas': 800000, 'gasPrice': gas_price, 'nonce': nonce}
            )
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = await asyncio.to_thread(self.w3.eth.send_raw_transaction, signed_tx.rawTransaction)
            
            logger.info(f"Transaction sent! Hash: {tx_hash.hex()}")
            receipt = await asyncio.to_thread(self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=300)

            if receipt.status == 1:
                logger.info(colored(f"✅ SUCCESS! Arbitrage for {opportunity.token_symbol} executed successfully. Tx: {tx_hash.hex()}", 'green', attrs=['bold']))
            else:
                logger.error(colored(f"❌ FAILED! Arbitrage transaction reverted. Status: {receipt.status}. Tx: {tx_hash.hex()}", 'red', attrs=['bold']))
            
            logger.info("Pausing for 60 seconds after trade attempt...")
            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Error during arbitrage execution: {e}")
        finally:
            self.trade_lock.release()

    async def check_and_execute_opportunity(self, token: dict, stable_token: str = 'USDC'):
        token_address = token['address']
        prices = {}
        for dex in self.selected_dexes:
            price = self.price_cache.get(token_address.lower(), {}).get(dex)
            if price: prices[dex] = price

        if len(prices) < 2: return

        min_price_dex, min_price = min(prices.items(), key=lambda item: item[1])
        max_price_dex, max_price = max(prices.items(), key=lambda item: item[1])
        
        if min_price_dex == max_price_dex: return

        price_diff_percent = ((max_price - min_price) / min_price) * 100

        # Increased lower threshold to avoid gas fee losses
        if not (0.8 < price_diff_percent < 50.0):
            return
        
        flash_loan_amount_usd = self.max_flash_loan_amount
        stable_decimals = await self.get_token_decimals(self.stable_tokens[stable_token])
        flash_loan_amount_wei = int(flash_loan_amount_usd * (10**stable_decimals))
        
        gross_profit_usd = flash_loan_amount_usd * (price_diff_percent / 100)
        gas_cost_usd = await self.estimate_gas_cost()
        net_profit_usd = gross_profit_usd - gas_cost_usd

        # Log significant spreads
        if price_diff_percent > 0.5:
             logger.info(f"Spread detected: {price_diff_percent:.2f}% for {token['symbol']} ({min_price_dex} -> {max_price_dex})")

        if net_profit_usd > self.min_profit_threshold:
            opportunity = ArbitrageOpportunity(
                token_address=token_address, token_symbol=token['symbol'],
                stable_token=stable_token, amount=flash_loan_amount_wei,
                buy_dex=min_price_dex, sell_dex=max_price_dex,
                buy_price=min_price, sell_price=max_price,
                expected_profit=gross_profit_usd, gas_cost=gas_cost_usd,
                net_profit=net_profit_usd, timestamp=datetime.now()
            )
            
            # --- PRO REPORTING ---
            log_msg = (
                f"Pair: {token['symbol']}/USDC\n"
                f"  Buy DEX:  {self.dexes[min_price_dex].name} (${min_price:.6f})\n"
                f"  Sell DEX: {self.dexes[max_price_dex].name} (${max_price:.6f})\n"
                f"  Spread: {price_diff_percent:.2f}%\n"
                f"  Est. Gross Profit: ${gross_profit_usd:.2f}\n"
                f"  Est. Gas Cost:     ${gas_cost_usd:.2f}\n"
                f"  NET PROFIT:      ${net_profit_usd:.2f}"
            )
            logger.info("ARBITRAGE OPPORTUNITY DETECTED" + log_msg)

            asyncio.create_task(self.execute_arbitrage(opportunity))

    async def update_price_for_dex(self, token: dict, stable_token: str, dex_key: str):
        """Fetches and updates price cache for a single DEX."""
        price = await self.get_token_price(token['address'], stable_token, dex_key)
        if price:
            key = token['address'].lower()
            if key not in self.price_cache: self.price_cache[key] = {}
            self.price_cache[key][dex_key] = price
            return price
        return None

    # --- EVENT DRIVEN MONITORING LOGIC (from testbot.py concept) ---
    async def subscribe_to_swap(self, token: dict, stable_token: str, dex_key: str):
        token_address, token_symbol = token['address'], token['symbol']
        pair_address = await self.get_pair_address(token_address, self.stable_tokens[stable_token], dex_key)
        
        if not pair_address:
            logger.debug(f"No pair for {token_symbol} on {dex_key}. Skipping subscription.")
            return

        pair_contract = self.w3.eth.contract(address=pair_address, abi=self.pair_abi)
        
        logger.info(f"  • Subscribed to {token_symbol} on {self.dexes[dex_key].name}")
        
        consecutive_errors = 0

        while self.is_monitoring:
            try:
                # Create filter for new events
                event_filter = self.w3.eth.filter({'address': pair_address, 'fromBlock': 'latest'})
                
                while self.is_monitoring:
                    # Poll the filter for changes
                    # Note: On HTTP, 'listening' requires getting new entries periodically.
                    # This is the standard "Event-Driven" approach for non-WebSocket connections.
                    entries = await asyncio.to_thread(event_filter.get_new_entries)
                    consecutive_errors = 0 # Reset error count on success
                    
                    if entries:
                        for event in entries:
                            # Log the event using the custom box
                            logger.info(f"SWAP DETECTEDToken: {token_symbol} | DEX: {self.dexes[dex_key].name}")
                            
                            # Immediately update price for this DEX
                            await self.update_price_for_dex(token, stable_token, dex_key)
                            
                            # Check opportunity
                            await self.check_and_execute_opportunity(token, stable_token)
                    
                    await asyncio.sleep(2) # check for new events every 2s
                    
            except Exception as e:
                consecutive_errors += 1
                if 'filter not found' in str(e).lower():
                    logger.debug(f"Filter expired for {token_symbol} on {dex_key}. Recreating...")
                else:
                    logger.error(f"Subscription error for {token_symbol} on {dex_key}: {e}")
                
                # Exponential backoff for repeated errors (max 60s)
                sleep_time = min(5 * consecutive_errors, 60)
                if consecutive_errors > 1:
                     logger.info(f"Connection unstable. Retrying in {sleep_time}s...")
                await asyncio.sleep(sleep_time)

    async def monitor_opportunities(self):
        logger.info("Starting Event-Driven Arbitrage Monitoring...")
        self.is_monitoring = True
        
        # --- NEW PRE-CHECK LOGIC ---
        logger.info("Verifying pools for selected tokens...")
        valid_pairs = [] # List of (token, dex_key, pair_address)
        
        for token in self.selected_tokens:
            for dex_key in self.selected_dexes:
                pair_address = await self.get_pair_address(token['address'], self.stable_tokens['USDC'], dex_key)
                if pair_address:
                     valid_pairs.append((token, dex_key, pair_address))
                     logger.info(colored(f"✅ Found pool for {token['symbol']} on {self.dexes[dex_key].name}", "green"))
                else:
                     logger.warning(colored(f"⚠️  No pool for {token['symbol']} on {self.dexes[dex_key].name} - Skipping", "yellow"))
                     
        if not valid_pairs:
            logger.error("No valid pools found for any selected token/DEX combination.")
            self.is_monitoring = False
            return
            
        logger.info(f"Verified {len(valid_pairs)} active pools.")
        
        # 1. Initial Price Fetch (Populate Cache)
        logger.info("Fetching initial prices...")
        for token, dex_key, _ in valid_pairs:
            await self.update_price_for_dex(token, 'USDC', dex_key)
        
        # 2. Start Event Listeners for ALL pairs
        tasks = []
        logger.info("Setting up event subscriptions...")
        for token, dex_key, _ in valid_pairs:
            # Create a specific task for each pair on each DEX
            tasks.append(asyncio.create_task(self.subscribe_to_swap(token, 'USDC', dex_key)))
        
        logger.info(f"Monitoring {len(tasks)} liquidity pools for Swap events.")
        
        try:
            # Wait for all listener tasks (they run forever)
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Stopping monitoring...")
            self.is_monitoring = False
            for t in tasks: t.cancel()
            logger.info("All tasks cancelled.")


async def main():
    print("🚀 Arbitrage Monitor Bot v4.0 (Merged Final) 🚀")
    print("=" * 60)
    print("FEATURES: [Safe V2 Logic] + [Pro UI] + [Event-Driven Monitoring]")
    print("=" * 60)
    
    # --- Load Configuration ---
    print("📂 Loading configuration from .env file...")
    load_dotenv() 

    rpc_url = os.getenv('RPC_URL')
    private_key = os.getenv('PRIVATE_KEY')
    contract_address = os.getenv('CONTRACT_ADDRESS')

    missing = []
    if not rpc_url: missing.append("RPC_URL")
    if not private_key: missing.append("PRIVATE_KEY")
    if not contract_address: missing.append("CONTRACT_ADDRESS")

    if missing:
        print("\n❌ CRITICAL ERROR: Missing configuration in .env file:")
        for m in missing:
            print(f"   - {m}")
        print("\nPlease create a .env file with these keys.")
        return

    print("✅ Configuration loaded successfully.")
    print(colored("⚠️  WARNING: This bot is configured for LIVE trading. ⚠️", 'red', attrs=['bold']))
    print("=" * 60)

    try:
        bot = ArbitrageMonitor(rpc_url, private_key, contract_address)
        
        if not bot.present_scraped_tokens():
            print("Token selection failed. Exiting.")
            return

        bot.select_dexes()
        bot.display_status()
        
        confirm = input("\nStart LIVE Event-Driven monitoring? (y/n): ").strip().lower()
        if confirm == 'y':
            await bot.monitor_opportunities()
        else:
            print("Exiting without starting.")

    except (ValueError, ConnectionError) as e:
        logger.error(f"\nFATAL ERROR: {e}")
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except Exception as e:
        logger.error(f"\nAn unexpected error occurred: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())

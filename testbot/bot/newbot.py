import time
import asyncio
import json
import os
import logging
import traceback
from dotenv import load_dotenv  # <--- NEW IMPORT

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Imports with robust fallbacks ---
try:
    from bot.scrapping import scrape_basescan_upward_tokens
except (ImportError, ModuleNotFoundError):
    try:
        from scrapping import scrape_basescan_upward_tokens
    except (ImportError, ModuleNotFoundError):
        logger.error("Failed to import scrape_basescan_upward_tokens from scrapping.py.")
        scrape_basescan_upward_tokens = None

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

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if geth_poa_middleware:
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to the RPC provider.")
        
        self.private_key = private_key
        self.account = self.w3.eth.account.from_key(private_key)
        self.contract_address = self.w3.to_checksum_address(contract_address)
        
        # --- UPDATED DEX LIST FROM bott2.py + new.py ---
        self.dexes = {
            'uniswap_v2': DEXInfo('Uniswap V2 (Base)', '0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24', '0x8909dc15e40173ff4699343b6eb8132c65e18ec6'),
            'pancake': DEXInfo('PancakeSwap V2', '0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb', '0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E'),
            'baseswap': DEXInfo('BaseSwap', '0x327Df1E6de05895d2ab08513aaDD9313Fe505d86', '0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB'),
            'sushiswap_v2': DEXInfo('SushiSwap V2', '0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891', '0x71524B4f93c58fcbF659783284E38825f0622859'), # <--- ADDED FROM new.py
            # 'aerodrome' was removed
        }
        
        self.stable_tokens = {'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'}
        
        # --- ABIs (remains the same) ---
        self.contract_abi = json.loads('[{"inputs":[{"components":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"flashLoanAmount","type":"uint256"},{"name":"dexLowPrice","type":"address"},{"name":"dexHighPrice","type":"address"},{"name":"user","type":"address"},{"name":"minProfit","type":"uint256"}],"name":"params","type":"tuple"}],"name":"executeArbitrage","outputs":[],"type":"function"}]')
        self.router_abi = json.loads('[{"inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"name":"amounts","type":"uint256[]"}],"type":"function"}]')
        self.factory_abi = json.loads('[{"constant":true,"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"name":"pair","type":"address"}],"type":"function"}]')
        self.pair_abi = json.loads('[{"constant":true,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":true,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},{"anonymous":false,"inputs":[{"indexed":true,"name":"sender","type":"address"},{"indexed":false,"name":"amount0In","type":"uint256"},{"indexed":false,"name":"amount1In","type":"uint256"},{"indexed":false,"name":"amount0Out","type":"uint256"},{"indexed":false,"name":"amount1Out","type":"uint256"},{"indexed":true,"name":"to","type":"address"}],"name":"Swap","type":"event"}]')
        self.erc20_abi = json.loads('[{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')
        
        self.contract = self.w3.eth.contract(address=self.contract_address, abi=self.contract_abi)
        
        # --- State and Config (remains the same) ---
        self.selected_tokens = []
        self.selected_dexes = []
        self.is_monitoring = False
        self.price_cache = {}
        self._decimals_cache = {}
        self.min_profit_threshold = 10  # Minimum profit in USD to attempt execution
        self.max_flash_loan_amount = 5000 # Max flash loan in USD
        self.trade_lock = asyncio.Lock() # Prevents multiple trades at once
        
    # All methods (display_status, select_dexes, present_scraped_tokens, get_token_decimals, 
    # get_token_price, get_pair_address, estimate_gas_cost, execute_arbitrage, 
    # check_and_execute_opportunity, subscribe_to_swap, price_polling_loop, monitor_opportunities)
    # are kept from bott2.py as they contain the working logic.

# The rest of the class methods are unchanged for brevity in this response, 
# but they remain the same as in the original bott2.py.

# --- UNCHANGED CLASS METHODS HERE ---
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
        try:
            # Assuming scrape_basescan_upward_tokens is a synchronous function
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
            pair_addr = await asyncio.to_thread(factory.functions.getPair(self.w3.to_checksum_address(tokenA), self.w3.to_checksum_address(tokenB)).call)
            return pair_addr if pair_addr != '0x0000000000000000000000000000000000000000' else None
        except Exception: return None

    async def estimate_gas_cost(self) -> float:
        try:
            gas_price = await asyncio.to_thread(self.w3.eth.get_gas_price)
            estimated_gas_limit = 600000 # Conservative estimate for a complex swap
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
            logger.info("="*40)
            logger.info(f"🚀 EXECUTING ARBITRAGE for {opportunity.token_symbol} 🚀")
            logger.info(f"   Expected Net Profit: ${opportunity.net_profit:.2f}")
            
            params_tuple = (
                self.w3.to_checksum_address(opportunity.token_address),
                self.w3.to_checksum_address(self.stable_tokens[opportunity.stable_token]),
                int(opportunity.amount),
                self.w3.to_checksum_address(self.dexes[opportunity.buy_dex].router_address),
                self.w3.to_checksum_address(self.dexes[opportunity.sell_dex].router_address),
                self.account.address,
                int(opportunity.net_profit * (10**6) * 0.9) # minProfit (USDC 6 decimals), with a 10% buffer
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
                logger.info(f"✅ SUCCESS! Arbitrage for {opportunity.token_symbol} executed successfully.")
            else:
                logger.error(f"❌ FAILED! Arbitrage transaction reverted. Status: {receipt.status}")
            
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

        logger.info(
            f"Potential opportunity for {token['symbol']}: "
            f"Spread: {price_diff_percent:.2f}%, Est. Net Profit: ${net_profit_usd:.2f}"
        )

        if net_profit_usd > self.min_profit_threshold:
            opportunity = ArbitrageOpportunity(
                token_address=token_address, token_symbol=token['symbol'],
                stable_token=stable_token, amount=flash_loan_amount_wei,
                buy_dex=min_price_dex, sell_dex=max_price_dex,
                buy_price=min_price, sell_price=max_price,
                expected_profit=gross_profit_usd, gas_cost=gas_cost_usd,
                net_profit=net_profit_usd, timestamp=datetime.now()
            )
            asyncio.create_task(self.execute_arbitrage(opportunity))

    async def subscribe_to_swap(self, token: dict, stable_token: str, dex_key: str):
        token_address, token_symbol = token['address'], token['symbol']
        pair_address = await self.get_pair_address(token_address, self.stable_tokens[stable_token], dex_key)
        if not pair_address:
            logger.debug(f"No pair for {token_symbol} on {dex_key}.")
            return

        pair_contract = self.w3.eth.contract(address=pair_address, abi=self.pair_abi)
        while self.is_monitoring:
            try:
                event_filter = self.w3.eth.filter({'address': pair_address, 'fromBlock': 'latest'})
                logger.info(f"Listening for swaps on {token_symbol} ({dex_key})")
                while self.is_monitoring:
                    entries = await asyncio.to_thread(event_filter.get_new_entries)
                    for event in entries:
                        logger.debug(f"Swap detected on {token_symbol} ({dex_key}). Re-checking prices.")
                        asyncio.create_task(self.check_and_execute_opportunity(token, stable_token))
                    await asyncio.sleep(2)
            except Exception as e:
                if 'filter not found' in str(e).lower():
                    logger.warning(f"Filter expired for {token_symbol} on {dex_key}. Recreating...")
                else:
                    logger.error(f"Subscription error for {token_symbol} on {dex_key}: {e}")
                await asyncio.sleep(10)

    async def price_polling_loop(self):
        """A fallback polling loop to catch opportunities missed by event listeners."""
        while self.is_monitoring:
            for token in self.selected_tokens:
                # Fetch fresh prices for all selected DEXs for the token
                for dex_key in self.selected_dexes:
                    price = await self.get_token_price(token['address'], 'USDC', dex_key)
                    if price:
                        key = token['address'].lower()
                        if key not in self.price_cache: self.price_cache[key] = {}
                        self.price_cache[key][dex_key] = price
                
                # Check for opportunity with the newly polled prices
                await self.check_and_execute_opportunity(token, 'USDC')
            
            await asyncio.sleep(15) # Poll every 15 seconds

    async def monitor_opportunities(self):
        logger.info("Starting arbitrage monitoring...")
        self.is_monitoring = True
        
        # Start the price polling loop as a background task
        polling_task = asyncio.create_task(self.price_polling_loop())

        try:
            # Main thread can wait or do other things; here we just wait for the task
            await polling_task
        except KeyboardInterrupt:
            logger.info("Stopping monitoring...")
            self.is_monitoring = False
            polling_task.cancel()
            logger.info("All tasks cancelled.")


async def main():
    print("🚀 Arbitrage Monitor Bot v3.2 (Live Trading)")
    print("=" * 45)
    
    # --- Load Configuration ---
    print("📂 Loading configuration from .env file...")
    load_dotenv() # Load variables from .env

    rpc_url = os.getenv('RPC_URL')
    private_key = os.getenv('PRIVATE_KEY')
    contract_address = os.getenv('CONTRACT_ADDRESS')

    # Basic check to ensure keys are loaded
    missing = []
    if not rpc_url: missing.append("RPC_URL")
    if not private_key: missing.append("PRIVATE_KEY")
    if not contract_address: missing.append("CONTRACT_ADDRESS")

    if missing:
        print("\n❌ CRITICAL ERROR: Missing configuration in .env file:")
        for m in missing:
            print(f"   - {m}")
        print("\nPlease create a .env file with these keys and try again.")
        return

    print("✅ Configuration loaded successfully.")
    print("⚠️  WARNING: This bot is configured for LIVE trading. ⚠️")
    print("⚠️  Ensure your contract is funded and you understand the risks. ⚠️")
    print("=" * 45)

    try:
        bot = ArbitrageMonitor(rpc_url, private_key, contract_address)
        
        if not bot.present_scraped_tokens():
            print("Token selection failed. Exiting.")
            return

        bot.select_dexes()
        bot.display_status()
        
        confirm = input("\nStart LIVE monitoring? (y/n): ").strip().lower()
        if confirm == 'y':
            await bot.monitor_opportunities()
        else:
            print("Exiting without starting.")

    except (ValueError, ConnectionError) as e:
        print(f"\nFATAL ERROR: {e}")
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
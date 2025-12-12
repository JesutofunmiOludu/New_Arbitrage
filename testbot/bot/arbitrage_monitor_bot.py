import time
import asyncio
import json
import os
import logging
import traceback

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Imports with robust fallbacks ---
try:
    from scrapping import scrape_basescan_upward_tokens
except (ImportError, ModuleNotFoundError):
    logger.error("Failed to import scrape_basescan_upward_tokens from scrapping.py.")
    scrape_basescan_upward_tokens = None

from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from web3 import Web3
from decimal import getcontext
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
        # --- Essential Validations ---
        if not rpc_url or "YOUR_ALCHEMY_KEY" in rpc_url:
            raise ValueError("Invalid RPC_URL provided.")
        if not private_key or "your_private_key" in private_key:
            raise ValueError("Invalid PRIVATE_KEY provided.")
        if not contract_address or "your_deployed_contract" in contract_address:
            raise ValueError("Invalid CONTRACT_ADDRESS provided.")

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if geth_poa_middleware:
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to the RPC provider.")
        
        self.private_key = private_key
        self.account = self.w3.eth.account.from_key(private_key)
        self.contract_address = self.w3.to_checksum_address(contract_address)
        
        # --- DEX and Token Configuration ---
        self.dexes = {
            'uniswap_v2': DEXInfo('Uniswap V2 (Base)', '0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24', '0x8909Dc15e40173ff4699343b6eb8132c65e18ec6'),
            'pancake': DEXInfo('PancakeSwap V2', '0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb', '0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E'),
        }
        self.stable_tokens = {'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'}
        
        # --- NEW: ABI for FlashLoanArbitrage.sol ---
        self.contract_abi = json.loads('[{"inputs":[],"stateMutability":"nonpayable","type":"constructor"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"token","type":"address"},{"indexed":false,"internalType":"uint256","name":"flashLoanAmount","type":"uint256"},{"indexed":false,"internalType":"uint256","name":"profit","type":"uint256"},{"indexed":true,"internalType":"address","name":"user","type":"address"}],"name":"ArbitrageExecuted","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"token","type":"address"},{"indexed":false,"internalType":"uint256","name":"amount","type":"uint256"},{"indexed":false,"internalType":"uint256","name":"fee","type":"uint256"}],"name":"FlashLoanCompleted","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"previousOwner","type":"address"},{"indexed":true,"internalType":"address","name":"newOwner","type":"address"}],"name":"OwnershipTransferred","type":"event"},{"inputs":[],"name":"BALANCER_VAULT","outputs":[{"internalType":"contract IBalancerVault","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"PANCAKE_ROUTER","outputs":[{"internalType":"contract IPancakeRouter","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"UNISWAP_ROUTER","outputs":[{"internalType":"contract IUniswapV2Router","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"USDC","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"USDbC","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"","type":"address"}],"name":"arbitrageInProgress","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"},{"internalType":"address","name":"dexLowPrice","type":"address"},{"internalType":"address","name":"dexHighPrice","type":"address"}],"name":"calculateArbitrageProfit","outputs":[{"internalType":"uint256","name":"expectedProfit","type":"uint256"},{"internalType":"bool","name":"profitable","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"emergencyWithdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"components":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"},{"internalType":"uint256","name":"flashLoanAmount","type":"uint256"},{"internalType":"address","name":"dexLowPrice","type":"address"},{"internalType":"address","name":"dexHighPrice","type":"address"},{"internalType":"address","name":"user","type":"address"},{"internalType":"uint256","name":"minProfit","type":"uint256"}],"internalType":"struct FlashLoanArbitrage.ArbitrageParams","name":"params","type":"tuple"}],"name":"executeArbitrage","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"token","type":"address"}],"name":"isArbitrageInProgress","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"owner","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address[]","name":"tokens","type":"address[]"},{"internalType":"uint256[]","name":"amounts","type":"uint256[]"},{"internalType":"uint256[]","name":"feeAmounts","type":"uint256[]"},{"internalType":"bytes","name":"userData","type":"bytes"}],"name":"receiveFlashLoan","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"renounceOwnership","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"newOwner","type":"address"}],"name":"transferOwnership","outputs":[],"stateMutability":"nonpayable","type":"function"}]')
        
        self.contract = self.w3.eth.contract(address=self.contract_address, abi=self.contract_abi)
        
        # --- State and Config ---
        self.selected_tokens = []
        self.selected_dexes = []
        self.is_monitoring = False
        self.price_cache = {}
        self.min_profit_threshold = 10  # Minimum profit in USD to attempt execution
        self.max_flash_loan_amount = 5000 # Max flash loan in USD
        self.trade_lock = asyncio.Lock()

    # ... (keep display_status, select_dexes, present_scraped_tokens) ...
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
            selection = input("Enter DEX numbers to monitor (e.g., 1,2): ").strip()
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
        records = scrape_basescan_upward_tokens(max_pages=3, limit=limit)
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

    async def get_token_price(self, token_address: str, stable_token: str, dex_key: str) -> Optional[float]:
        """Gets a simple price for 1 whole token unit for initial discovery."""
        try:
            router_addr = self.w3.to_checksum_address(self.dexes[dex_key].router_address)
            erc20_abi = json.loads('[{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')
            router_abi = json.loads('[{"inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"name":"amounts","type":"uint256[]"}],"type":"function"}]')
            
            router = self.w3.eth.contract(address=router_addr, abi=router_abi)
            
            token_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=erc20_abi)
            token_decimals = await asyncio.to_thread(token_contract.functions.decimals().call)
            
            stable_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(self.stable_tokens[stable_token]), abi=erc20_abi)
            stable_decimals = await asyncio.to_thread(stable_contract.functions.decimals().call)

            amount_in = 10 ** token_decimals
            path = [self.w3.to_checksum_address(token_address), self.w3.to_checksum_address(self.stable_tokens[stable_token])]
            amounts_out = await asyncio.to_thread(router.functions.getAmountsOut(amount_in, path).call)
            
            price = (amounts_out[1] / (10**stable_decimals))
            return price if 0 < price < 1e12 else None
        except Exception:
            return None

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity):
        if not await self.trade_lock.acquire():
            logger.warning("Could not acquire trade lock, another trade is in progress.")
            return

        try:
            logger.info("="*40)
            logger.info(f"🚀 EXECUTING ARBITRAGE for {opportunity.token_symbol} 🚀")
            logger.info(f"   Expected Net Profit: ${opportunity.net_profit:.2f}")
            
            # This tuple MUST match the order in the ArbitrageParams struct in Solidity
            params_tuple = (
                self.w3.to_checksum_address(opportunity.token_address),
                self.w3.to_checksum_address(self.stable_tokens[opportunity.stable_token]),
                int(opportunity.amount),
                self.w3.to_checksum_address(self.dexes[opportunity.buy_dex].router_address),
                self.w3.to_checksum_address(self.dexes[opportunity.sell_dex].router_address),
                self.account.address,
                int(opportunity.net_profit * (10**6)) # minProfit (USDC has 6 decimals)
            )

            nonce = await asyncio.to_thread(self.w3.eth.get_transaction_count, self.account.address)
            gas_price = await asyncio.to_thread(self.w3.eth.get_gas_price)

            tx = await asyncio.to_thread(
                self.contract.functions.executeArbitrage(params_tuple).build_transaction,
                {'from': self.account.address, 'gas': 1000000, 'gasPrice': gas_price, 'nonce': nonce}
            )
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = await asyncio.to_thread(self.w3.eth.send_raw_transaction, signed_tx.rawTransaction)
            
            logger.info(f"Transaction sent! Hash: {tx_hash.hex()}")
            receipt = await asyncio.to_thread(self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=300)

            if receipt.status == 1:
                logger.info(f"✅ SUCCESS! Arbitrage for {opportunity.token_symbol} executed successfully.")
            else:
                logger.error(f"❌ FAILED! Arbitrage transaction reverted. Check the transaction on a block explorer.")
            
            logger.info("Pausing for 60 seconds after trade attempt...")
            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Error during arbitrage execution: {e}")
            traceback.print_exc()
        finally:
            self.trade_lock.release()

    async def check_opportunity(self, token: dict, stable_token: str = 'USDC'):
        token_address = token['address']
        prices = {}
        for dex in self.selected_dexes:
            price = await self.get_token_price(token_address, stable_token, dex)
            if price: prices[dex] = price

        if len(prices) < 2: return

        min_price_dex, min_price = min(prices.items(), key=lambda item: item[1])
        max_price_dex, max_price = max(prices.items(), key=lambda item: item[1])
        
        if min_price_dex == max_price_dex: return

        # Use a standard flash loan amount for the simulation
        flash_loan_amount_usd = self.max_flash_loan_amount
        stable_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(self.stable_tokens[stable_token]), abi=self.contract_abi)
        stable_decimals = 6 # Assuming USDC
        flash_loan_amount_wei = int(flash_loan_amount_usd * (10**stable_decimals))

        try:
            # --- NEW: On-chain simulation ---
            expected_profit_wei, is_profitable = await asyncio.to_thread(
                self.contract.functions.calculateArbitrageProfit(
                    self.w3.to_checksum_address(token_address),
                    self.w3.to_checksum_address(self.stable_tokens[stable_token]),
                    flash_loan_amount_wei,
                    self.w3.to_checksum_address(self.dexes[min_price_dex].router_address),
                    self.w3.to_checksum_address(self.dexes[max_price_dex].router_address)
                ).call
            )

            if not is_profitable:
                return

            gross_profit_usd = expected_profit_wei / (10**stable_decimals)
            
            # Rough gas cost estimation
            gas_cost_usd = 10.0 # Default to $10 for simplicity
            net_profit_usd = gross_profit_usd - gas_cost_usd

            logger.info(
                f"On-chain check for {token['symbol']}: "
                f"Gross Profit: ${gross_profit_usd:.2f}, Est. Net Profit: ${net_profit_usd:.2f}"
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

        except Exception as e:
            logger.debug(f"Profit calculation failed for {token['symbol']}: {e}")

    async def monitor_opportunities(self):
        logger.info("Starting arbitrage monitoring...")
        self.is_monitoring = True
        
        while self.is_monitoring:
            try:
                for token in self.selected_tokens:
                    await self.check_opportunity(token, 'USDC')
                
                await asyncio.sleep(15) # Poll every 15 seconds
            except KeyboardInterrupt:
                logger.info("Stopping monitoring...")
                self.is_monitoring = False
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(30)

async def main():
    print("🚀 Arbitrage Monitor Bot v4.0 (Balancer V3 Integration)")
    print("=" * 55)
    print("⚠️  WARNING: This bot is configured for LIVE trading with your contract. ⚠️")
    print("=" * 55)

    try:
        rpc_url = os.getenv('RPC_URL', input("Enter Base network RPC URL: ").strip())
        private_key = os.getenv('PRIVATE_KEY', input("Enter your private key: ").strip())
        contract_address = os.getenv('CONTRACT_ADDRESS', input("Enter your deployed FlashLoanArbitrage contract address: ").strip())

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
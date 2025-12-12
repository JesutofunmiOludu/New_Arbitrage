import json
import time
import asyncio
import logging
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import websockets
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

    def select_dexes(self):
        """Allow user to select DEXes to monitor"""
        print("\nAvailable DEXes:")
        for i, (key, dex) in enumerate(self.dexes.items(), 1):
            print(f"{i}. {dex.name}")
        
        while True:
            try:
                selection = input("Enter DEX numbers (comma-separated): ").strip()
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
        """Get token price from a specific DEX"""
        try:
            if amount is None:
                amount = 10**18  # 1 token with 18 decimals
            
            dex = self.dexes[dex_key]
            router = self.w3.eth.contract(
                address=dex.router_address,
                abi=self.router_abi
            )
            
            path = [token_address, self.stable_tokens[stable_token]]
            amounts_out = router.functions.getAmountsOut(amount, path).call()
            
            # Price is the output amount divided by input amount
            price = float(amounts_out[1]) / float(amount)
            return price
            
        except Exception as e:
            self.logger.error(f"Error getting price for {token_address} on {dex_key}: {e}")
            return None

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
            
            # Prepare transaction parameters
            params = {
                'tokenA': opportunity.token_address,
                'tokenB': self.stable_tokens[opportunity.stable_token],
                'flashLoanAmount': opportunity.amount,
                'dexLowPrice': self.dexes[opportunity.buy_dex].router_address,
                'dexHighPrice': self.dexes[opportunity.sell_dex].router_address,
                'user': self.account.address,
                'minProfit': int(opportunity.expected_profit * 0.8 * 10**6)  # 80% of expected profit as minimum
            }
            
            # Build transaction
            transaction = self.contract.functions.executeArbitrage(params).build_transaction({
                'from': self.account.address,
                'gas': 600000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
            })
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(transaction, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            self.logger.info(f"Transaction sent: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            if receipt['status'] == 1:
                self.logger.info(f"Arbitrage executed successfully! Gas used: {receipt['gasUsed']}")
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
        
        # Load selected tokens
        if not bot.load_selected_tokens():
            print("Please run the token scanner first to select tokens.")
            return
        
        # Select DEXes to monitor
        bot.select_dexes()
        
        # Display status
        bot.display_status()
        
        # Ask user to confirm
        confirm = input("\nStart monitoring? (y/n): ").strip().lower()
        if confirm != 'y':
            print("Monitoring cancelled.")
            return
        
        # Start monitoring
        await bot.monitor_opportunities()
        
    except ValueError as e:
        print(f"Configuration error: {e}")
    except KeyboardInterrupt:
        print("\nStopping bot...")
        if 'bot' in locals():
            bot.stop_monitoring()
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
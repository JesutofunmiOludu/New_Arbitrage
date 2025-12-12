import json
import time
import asyncio
import logging
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
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
        
        print("🔧 Initializing Web3 connection...")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Test connection
        if not self.w3.is_connected():
            raise ConnectionError("❌ Failed to connect to RPC endpoint")
        
        print(f"✅ Connected to network. Chain ID: {self.w3.eth.chain_id}")
        
        # Add POA middleware if available (for Base network)
        if geth_poa_middleware:
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            print("✅ POA middleware added")
            
        self.private_key = private_key
        self.account = self.w3.eth.account.from_key(private_key)
        self.contract_address = contract_address
        
        print(f"💳 Using account: {self.account.address}")
        
        # Check account balance
        try:
            balance = self.w3.eth.get_balance(self.account.address)
            balance_eth = self.w3.from_wei(balance, 'ether')
            print(f"💰 Account balance: {balance_eth:.6f} ETH")
            
            if balance_eth < 0.001:
                print("⚠️  WARNING: Low ETH balance. May not be enough for gas fees!")
        except Exception as e:
            print(f"❌ Error checking balance: {e}")
        
        # DEX configurations for Base chain (Updated with correct addresses)
        self.dexes = {
            'uniswap': DEXInfo(
                name='Uniswap V3',
                router_address='0x2626664c2603336E57B271c5C0b26F421741e481',  # Uniswap V3 SwapRouter on Base
                factory_address='0x33128a8fC17869897dcE68Ed026d694621f6FDfD',  # Uniswap V3 Factory on Base
                websocket_url=rpc_url  # Use same RPC for now
            ),
            'aerodrome': DEXInfo(
                name='Aerodrome',
                router_address='0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43',  # Aerodrome Router on Base
                factory_address='0x420DD381b31aEf6683db6B902084cB0FFECe40Da',  # Aerodrome Factory on Base
                websocket_url=rpc_url
            ),
            'baseswap': DEXInfo(
                name='BaseSwap',
                router_address='0x327Df1E6de05895d2ab08513aaDD9313Fe505d86',  # BaseSwap Router
                factory_address='0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB',  # BaseSwap Factory
                websocket_url=rpc_url
            )
        }
        
        # Stable token addresses on Base
        self.stable_tokens = {
            'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
            'USDbC': '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA'
        }
        
        # Simplified router ABI for getting prices
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
        
        # ERC20 ABI for decimals and basic info
        self.erc20_abi = [
            {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"}
        ]
        
        # Monitoring state
        self.selected_tokens = []
        self.selected_dexes = []
        self.is_monitoring = False
        self.price_cache = {}
        self.price_cache_time = {}
        
        # Configuration
        self.min_profit_threshold = 5  # Lower threshold for testing - $5
        self.max_flash_loan_amount = 1000  # Lower amount for testing - $1000
        self.price_check_interval = 10  # Check every 10 seconds
        
        # Setup logging
        logging.basicConfig(
            level=logging.DEBUG,  # More verbose logging
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('arbitrage_bot_debug.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info("🤖 ArbitrageMonitor initialized successfully")

    def validate_inputs(self, rpc_url: str, private_key: str, contract_address: str) -> bool:
        """Validate configuration inputs"""
        print("🔍 Validating configuration...")
        
        # Check if inputs are placeholder values
        if private_key == "your_private_key_here":
            print("❌ Please replace 'your_private_key_here' with your actual private key")
            return False
        
        if contract_address == "your_deployed_contract_address":
            print("⚠️  WARNING: Using placeholder contract address - arbitrage execution will be disabled")
            # Don't fail validation, just warn
        
        # Validate private key format
        clean_private_key = private_key.replace("0x", "")
        if len(clean_private_key) != 64:
            print(f"❌ Private key must be 64 hexadecimal characters (got {len(clean_private_key)})")
            return False
        
        try:
            int(clean_private_key, 16)
        except ValueError:
            print("❌ Private key contains non-hexadecimal characters")
            return False
        
        # Validate contract address format
        if contract_address != "your_deployed_contract_address":
            if not contract_address.startswith("0x") or len(contract_address) != 42:
                print("❌ Contract address must be a valid Ethereum address")
                return False
        
        print("✅ Configuration validated")
        return True

    def load_base_tokens(self, file_path: str = 'base_tokens.json') -> List[dict]:
        """Load tokens from base_tokens.json file"""
        print(f"📁 Loading tokens from {file_path}...")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tokens = json.load(f)
                print(f"✅ Loaded {len(tokens)} tokens from {file_path}")
                return tokens
        except FileNotFoundError:
            print(f"❌ Token file {file_path} not found in current directory")
            print(f"📂 Current directory: {os.getcwd()}")
            print(f"📋 Files in directory: {os.listdir('.')}")
            return []
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing JSON from {file_path}: {e}")
            return []
        except Exception as e:
            print(f"❌ Error loading tokens from {file_path}: {e}")
            return []

    def select_tokens_simple(self) -> bool:
        """Simplified token selection for testing"""
        print("\n🚀 Loading Base Network tokens...")
        
        base_tokens = self.load_base_tokens()
        if not base_tokens:
            print("❌ Could not load tokens. Creating test tokens...")
            # Create some test tokens for debugging
            test_tokens = [
                {
                    "name": "USD Base Coin",
                    "symbol": "USDbC", 
                    "contract_address": "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",
                    "change_percent": "0.01%"
                },
                {
                    "name": "Wrapped Ether",
                    "symbol": "WETH",
                    "contract_address": "0x4200000000000000000000000000000000000006", 
                    "change_percent": "1.25%"
                }
            ]
            base_tokens = test_tokens
            print(f"✅ Using {len(test_tokens)} test tokens")
        
        # Show first 10 tokens
        display_tokens = base_tokens[:10]
        print(f"\nShowing first {len(display_tokens)} tokens:")
        print(f"{'Idx':>3}  {'Symbol':<10}  {'Name':<30}  {'Change':<8}")
        print("-" * 60)
        
        for i, token in enumerate(display_tokens, 1):
            name = token.get('name', '').replace('\n', ' ').strip()[:28]
            symbol = token.get('symbol', 'N/A')[:8]
            change = token.get('change_percent', '0%')
            print(f"{i:>3}.  {symbol:<10}  {name:<30}  {change:<8}")
        
        # Auto-select first 3 tokens for testing
        selected = display_tokens[:3]
        print(f"\n🎯 Auto-selecting first {len(selected)} tokens for testing:")
        
        for token in selected:
            print(f"  • {token['symbol']} - {token['name']}")
        
        # Convert to expected format
        formatted_tokens = []
        for token in selected:
            formatted_token = {
                "address": token.get('contract_address', ''),
                "symbol": token.get('symbol', ''),
                "name": token.get('name', '').replace('\n', ' ').strip()
            }
            formatted_tokens.append(formatted_token)
        
        self.selected_tokens = formatted_tokens
        print(f"✅ Selected {len(self.selected_tokens)} tokens for monitoring")
        return True

    def select_dexes_simple(self) -> bool:
        """Simplified DEX selection for testing"""
        print(f"\n📈 Available DEXes on Base:")
        for i, (key, dex) in enumerate(self.dexes.items(), 1):
            print(f"  {i}. {dex.name}")
        
        # Auto-select first 2 DEXes
        dex_keys = list(self.dexes.keys())
        self.selected_dexes = dex_keys[:2]  # Take first 2 DEXes
        
        print(f"\n🎯 Auto-selecting first 2 DEXes for testing:")
        for dex_key in self.selected_dexes:
            print(f"  • {self.dexes[dex_key].name}")
        
        return True

    async def test_token_info(self, token_address: str) -> dict:
        """Test getting basic token information"""
        try:
            print(f"\n🔍 Testing token info for {token_address}...")
            
            token_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(token_address),
                abi=self.erc20_abi
            )
            
            def _get_token_info():
                try:
                    name = token_contract.functions.name().call()
                except:
                    name = "Unknown"
                
                try:
                    symbol = token_contract.functions.symbol().call()
                except:
                    symbol = "???"
                
                try:
                    decimals = token_contract.functions.decimals().call()
                except:
                    decimals = 18
                
                return name, symbol, decimals
            
            name, symbol, decimals = await asyncio.to_thread(_get_token_info)
            
            info = {
                'name': name,
                'symbol': symbol, 
                'decimals': decimals,
                'address': token_address
            }
            
            print(f"✅ Token info: {symbol} ({name}) - {decimals} decimals")
            return info
            
        except Exception as e:
            print(f"❌ Error getting token info: {e}")
            return {'error': str(e)}

    async def test_dex_connection(self, dex_key: str) -> bool:
        """Test connection to DEX router"""
        try:
            print(f"\n🔍 Testing {dex_key} connection...")
            
            dex = self.dexes[dex_key]
            router_address = self.w3.to_checksum_address(dex.router_address)
            
            # Try to get contract code
            code = self.w3.eth.get_code(router_address)
            
            if len(code) > 0:
                print(f"✅ {dex.name} router found at {router_address}")
                return True
            else:
                print(f"❌ {dex.name} router not found at {router_address}")
                return False
                
        except Exception as e:
            print(f"❌ Error testing {dex_key}: {e}")
            return False

    async def test_price_fetch(self, token_address: str, stable_token: str, dex_key: str) -> Optional[float]:
        """Test fetching price from DEX"""
        try:
            print(f"\n🔍 Testing price fetch: {token_address} on {dex_key}...")
            
            token_addr = self.w3.to_checksum_address(token_address)
            stable_addr = self.w3.to_checksum_address(self.stable_tokens[stable_token])
            router_addr = self.w3.to_checksum_address(self.dexes[dex_key].router_address)
            
            # Get token decimals
            token_info = await self.test_token_info(token_address)
            if 'error' in token_info:
                print(f"❌ Could not get token info: {token_info['error']}")
                return None
            
            decimals = token_info['decimals']
            amount = 10 ** decimals  # 1 token
            
            def _get_price():
                router = self.w3.eth.contract(address=router_addr, abi=self.router_abi)
                path = [token_addr, stable_addr]
                return router.functions.getAmountsOut(amount, path).call()
            
            try:
                amounts_out = await asyncio.to_thread(_get_price)
                
                # USDC has 6 decimals
                price = amounts_out[1] / (10 ** 6)
                print(f"✅ Price on {dex_key}: ${price:.6f}")
                return float(price)
                
            except Exception as call_error:
                print(f"❌ Router call failed on {dex_key}: {call_error}")
                # This might be due to no liquidity pair existing
                return None
            
        except Exception as e:
            print(f"❌ Error testing price fetch: {e}")
            return None

    async def run_diagnostics(self):
        """Run comprehensive diagnostics"""
        print(f"\n{'='*60}")
        print("🔬 RUNNING DIAGNOSTICS")
        print(f"{'='*60}")
        
        # Test network connection
        print(f"\n🌐 Network Info:")
        try:
            latest_block = self.w3.eth.block_number
            print(f"✅ Latest block: {latest_block}")
        except Exception as e:
            print(f"❌ Network error: {e}")
            return False
        
        # Test account
        print(f"\n💳 Account Info:")
        print(f"  Address: {self.account.address}")
        try:
            balance = self.w3.eth.get_balance(self.account.address)
            print(f"  Balance: {self.w3.from_wei(balance, 'ether'):.6f} ETH")
        except Exception as e:
            print(f"❌ Balance error: {e}")
        
        # Test DEX connections
        print(f"\n📈 Testing DEX Connections:")
        working_dexes = []
        for dex_key in self.selected_dexes:
            if await self.test_dex_connection(dex_key):
                working_dexes.append(dex_key)
        
        if len(working_dexes) < 2:
            print(f"❌ Need at least 2 working DEXes for arbitrage")
            return False
        
        # Test token info
        print(f"\n🪙 Testing Token Information:")
        working_tokens = []
        for token in self.selected_tokens[:2]:  # Test first 2 tokens
            token_address = token['address']
            info = await self.test_token_info(token_address)
            if 'error' not in info:
                working_tokens.append(token)
        
        if not working_tokens:
            print(f"❌ No working tokens found")
            return False
        
        # Test price fetching
        print(f"\n💰 Testing Price Fetching:")
        price_tests_passed = 0
        total_tests = 0
        
        for token in working_tokens[:1]:  # Test 1 token
            token_address = token['address']
            for dex_key in working_dexes:
                total_tests += 1
                price = await self.test_price_fetch(token_address, 'USDC', dex_key)
                if price is not None:
                    price_tests_passed += 1
        
        print(f"\n📊 Diagnostics Summary:")
        print(f"  Working DEXes: {len(working_dexes)}/{len(self.selected_dexes)}")
        print(f"  Working tokens: {len(working_tokens)}/{len(self.selected_tokens)}")
        print(f"  Price fetches: {price_tests_passed}/{total_tests}")
        
        if price_tests_passed > 0:
            print(f"✅ Basic functionality confirmed - ready for monitoring!")
            return True
        else:
            print(f"❌ No price fetches successful - check DEX configurations")
            return False

    async def simple_monitor_test(self):
        """Simple monitoring test without complex arbitrage logic"""
        print(f"\n{'='*60}")
        print("🔍 SIMPLE MONITORING TEST")
        print(f"{'='*60}")
        
        if not await self.run_diagnostics():
            print("❌ Diagnostics failed - cannot proceed with monitoring")
            return
        
        print(f"\n🚀 Starting simple price monitoring...")
        print("Press Ctrl+C to stop")
        
        self.is_monitoring = True
        cycles = 0
        
        try:
            while self.is_monitoring and cycles < 5:  # Run max 5 cycles for testing
                cycles += 1
                print(f"\n--- Cycle {cycles} ---")
                
                for i, token in enumerate(self.selected_tokens[:2], 1):  # Monitor first 2 tokens
                    token_address = token['address']
                    token_symbol = token['symbol']
                    
                    print(f"\n{i}. Checking {token_symbol} ({token_address[:8]}...)")
                    
                    prices = {}
                    
                    # Get prices from DEXes
                    for dex_key in self.selected_dexes:
                        price = await self.test_price_fetch(token_address, 'USDC', dex_key)
                        if price:
                            prices[dex_key] = price
                    
                    if len(prices) >= 2:
                        # Calculate price difference
                        max_price = max(prices.values())
                        min_price = min(prices.values())
                        diff_percent = ((max_price - min_price) / min_price) * 100
                        
                        print(f"   Prices: {prices}")
                        print(f"   Difference: {diff_percent:.2f}%")
                        
                        if diff_percent > 1.0:  # More than 1% difference
                            print(f"   🚨 Potential arbitrage opportunity!")
                    else:
                        print(f"   ❌ Could not get prices from multiple DEXes")
                
                print(f"\n⏳ Waiting {self.price_check_interval} seconds...")
                await asyncio.sleep(self.price_check_interval)
                
        except KeyboardInterrupt:
            print("\n⏹️ Monitoring stopped by user")
        
        self.is_monitoring = False
        print(f"\n✅ Monitoring test completed ({cycles} cycles)")

def get_test_configuration():
    """Get minimal configuration for testing"""
    print("⚙️ SIMPLIFIED CONFIGURATION FOR TESTING")
    print("=" * 50)
    
    # Check environment variables
    rpc_url = os.getenv('RPC_URL')
    private_key = os.getenv('PRIVATE_KEY')
    
    if not rpc_url:
        print("\n🌐 Base Network RPC URL needed")
        print("💡 Try a free RPC from:")
        print("   • https://www.alchemy.com/ (recommended)")
        print("   • https://infura.io/")
        print("   • https://chainlist.org/ (for public RPCs)")
        rpc_url = input("\nEnter Base RPC URL: ").strip()
    
    if not private_key:
        print("\n🔑 Private key needed (will not be stored)")
        print("⚠️  Make sure account has some ETH for gas fees")
        private_key = input("Enter private key: ").strip()
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
    
    # Use placeholder contract for testing (monitoring only)
    contract_address = "your_deployed_contract_address"
    
    return rpc_url, private_key, contract_address

async def main():
    print("🧪 ARBITRAGE BOT - DEBUG & TEST MODE")
    print("=" * 50)
    print("This version will:")
    print("• Test connections and configurations")
    print("• Monitor prices without executing trades")
    print("• Provide detailed diagnostics")
    print("=" * 50)
    
    # Get configuration
    rpc_url, private_key, contract_address = get_test_configuration()
    
    if not all([rpc_url, private_key]):
        print("❌ Configuration incomplete")
        return
    
    try:
        # Initialize bot
        print("\n🔧 Initializing bot...")
        bot = ArbitrageMonitor(rpc_url, private_key, contract_address)
        
        # Simple token selection
        if not bot.select_tokens_simple():
            print("❌ Token selection failed")
            return
        
        # Simple DEX selection  
        if not bot.select_dexes_simple():
            print("❌ DEX selection failed")
            return
        
        # Run diagnostics and monitoring test
        await bot.simple_monitor_test()
        
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"💥 Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
from web3 import Web3
from eth_account import Account
import json
import time
from typing import Tuple, List, Dict, Optional
import os
from dotenv import load_dotenv
from decimal import Decimal, getcontext # Import getcontext
import asyncio
from web3.exceptions import ProviderConnectionError
from web3.middleware import geth_poa_middleware
import requests
from web3.contract import Contract
from eth_typing import Address
from web3.types import EventData
import logging
from termcolor import colored
import datetime
# from token_selector import ( # Temporarily comment out token_selector import
#     display_available_tokens,
#     get_user_token_selection,
#     get_token_pairs,
#     AVAILABLE_TOKENS,
#     STABLECOINS
# )
# Need dummy AVAILABLE_TOKENS if ADDRESS_TO_TOKEN_INFO is used before initialize_token_pairs
# AVAILABLE_TOKENS = [] # Dummy value - Replaced by CSV loading
STABLECOINS = ['USDC', 'DAI'] # Keep stablecoins definition if needed elsewhere
import aiohttp
import threading
from concurrent.futures import ThreadPoolExecutor
import argparse
import sys
import csv
import re
print("--- Top of script ---", flush=True) # ADDED
import traceback # Import traceback for detailed error logging

# Set precision for Decimal
getcontext().prec = 50 # Set precision for Decimal calculations

# Configure logging with colors and better formatting
class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors"""

    COLORS = {
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'DEBUG': 'blue',
        'CRITICAL': 'red'
    }

    def format(self, record):
        # Color the level name with better formatting
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = colored(f"[{levelname:^7}]", self.COLORS[levelname], attrs=['bold'])

        # Use logger's built-in time formatting
        # Use formatTime to get the formatted timestamp
        timestamp_str = self.formatTime(record, self.datefmt)
        log_message = record.getMessage() # Get the original message

        # Apply specific formatting based on message content
        if "Arbitrage opportunity found" in log_message or "ARBITRAGE OPPORTUNITY DETECTED" in log_message:
            formatted_message = (
                "\n" + "╔" + "═" * 78 + "╗\n" +
                "║" + colored("💎 ARBITRAGE OPPORTUNITY DETECTED 💎".center(78), 'yellow', attrs=['bold', 'blink']) + "║\n" +
                "╚" + "═" * 78 + "╝\n" +
                log_message # Use original message here for the box content
            )
        elif "SWAP EVENT DETAILS" in log_message:
             formatted_message = (
                 "\n" + "┌" + "─" * 78 + "┐\n" +
                 "│" + colored("📊 SWAP EVENT DETAILS".center(78), 'cyan', attrs=['bold']) + "│\n" +
                 "└" + "─" * 78 + "┘\n" +
                 log_message # Use original message here for the box content
             )
        elif "Initialized" in log_message:
            formatted_message = colored("✅ " + log_message, 'green')
        elif "Error" in log_message or "❌" in log_message: # Check for emoji too
            formatted_message = colored("❌ " + log_message, 'red', attrs=['bold'])
        elif "Warning" in log_message or "⚠️" in log_message: # Check for emoji too
            formatted_message = colored("⚠️  " + log_message, 'yellow', attrs=['bold'])
        else:
             # For regular messages, just use the standard format
             formatted_message = log_message

        # Prepend timestamp and levelname
        return f"{colored(timestamp_str, 'cyan', attrs=['bold'])} {record.levelname} {formatted_message}"


# Configure logging
def setup_logging():
    """Set up logging with custom formatter"""
    logger = logging.getLogger()
    # Set level to DEBUG to see more detailed logs for price calculation
    logger.setLevel(logging.INFO) # Changed back to INFO to reduce noise, DEBUG can be enabled if needed

    # Remove existing handlers
    if logger.hasHandlers():
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    # Create console handler with custom formatter
    ch = logging.StreamHandler(sys.stdout) # Use sys.stdout
    ch.setLevel(logging.INFO) # Set handler level to INFO
    # Use standard format codes, let the formatter handle time
    formatter = ColoredFormatter('%(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Silence noisy libraries if needed
    logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


    return logger

# Environment loading section
try:
    # Construct the path relative to the script's directory
    script_dir = os.path.dirname(__file__)
    project_root = os.path.dirname(script_dir) # Assumes script is in scripts/
    env_path = os.path.join(project_root, '.env.new')

    if not os.path.exists(env_path):
        raise FileNotFoundError(f"Missing .env.new file at expected location: {env_path}")

    load_dotenv(dotenv_path=env_path, override=True) # Use dotenv_path and override
    print(f"✅ Successfully loaded environment from {env_path}")

except Exception as e:
    print(f"❌ Critical error loading environment: {str(e)}")
    print(traceback.format_exc()) # Print traceback for env loading errors
    sys.exit(1)

# Debug: Print environment variables (Mask sensitive ones)
print("\n--- Environment Variables ---")
print(f"NETWORK: {os.getenv('NETWORK')}")
print(f"BASE_SEPOLIA_RPC_URL: {os.getenv('BASE_SEPOLIA_RPC_URL')}")
print(f"PRIVATE_KEY: {'*' * len(os.getenv('PRIVATE_KEY', '')) if os.getenv('PRIVATE_KEY') else 'Not Set'}")
print(f"STABLE_TOKEN: {os.getenv('STABLE_TOKEN')}")
print(f"BORROW_AMOUNT: {os.getenv('BORROW_AMOUNT')}")
print(f"ARBITRAGE_CONTRACT_ADDRESS: {os.getenv('ARBITRAGE_CONTRACT_ADDRESS')}")
print(f"MIN_PROFIT_THRESHOLD: {os.getenv('MIN_PROFIT_THRESHOLD')}")
print(f"MIN_PROFIT_USD: {os.getenv('MIN_PROFIT_USD')}")
print("-----------------------------")


# Network Configuration
NETWORK = os.getenv('NETWORK', 'mainnet')  # Default to Base Mainnet

# Network-specific configurations
NETWORK_CONFIG = {
    'mainnet': {
        'name': 'Base Mainnet',
        'rpc_url': os.getenv('BASE_SEPOLIA_RPC_URL', 'https://mainnet.base.org'), # Use env var, fallback to public
        'wss_url': None,  # No WebSocket support
        'factories': {
            'uniswap': '0x33128a8fC17869897dcE68Ed026d694621f6FDfD',  # Base Mainnet Uniswap V3
            'sushiswap': '0xc35DADB65012eC5796536bD9864eD8773aBc74C4'  # Base Mainnet SushiSwap V3 Factory
        },
        'base': {
            'rpc': os.getenv('BASE_RPC_URL', 'https://mainnet.base.org'),  # Use new env var
            'explorer': 'https://basescan.org',
            'chain_id': 8453
        }
    }
    # Add other networks like 'base_sepolia' here if needed
}

# Get current network configuration
if NETWORK not in NETWORK_CONFIG:
    print(f"❌ Network '{NETWORK}' not configured in NETWORK_CONFIG.")
    sys.exit(1)
current_network = NETWORK_CONFIG[NETWORK]
print(f"\n--- Network Configuration ---")
print(f"Network: {current_network['name']}")
print(f"RPC URL: {current_network['rpc_url']}")
print(f"WebSocket: {'Enabled' if current_network.get('wss_url') else 'Disabled'}") # Use .get()
print("---------------------------")


# Set network-specific addresses
UNISWAP_V3_FACTORY = Web3.to_checksum_address(current_network['factories']['uniswap'])
SUSHISWAP_V3_FACTORY = Web3.to_checksum_address(current_network['factories']['sushiswap'])

# Minimal ABI to fetch decimals
MINIMAL_ERC20_ABI = json.loads('[{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":false,"stateMutability":"view","type":"function"}]')

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Arbitrage Bot for Uniswap/Sushiswap V3 on Base')
    parser.add_argument('--simulate', action='store_true', help='Run in simulation mode (detects but does not execute trades)')
    parser.add_argument('--monitor-only', action='store_true', help='Run in monitor-only mode (detects but does not execute trades)')
    return parser.parse_args()

class ArbitrageMonitor:

    def _run_event_loop(self):
        """Runs the asyncio event loop in a separate thread."""
        self.logger.info("Starting asyncio event loop in background thread...")
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        except Exception as e:
            self.logger.error(f"❌ Exception in event loop thread: {e}")
            self.logger.error(traceback.format_exc())
        finally:
            if self.loop.is_running():
                self.logger.info("Stopping event loop...")
                self.loop.call_soon_threadsafe(self.loop.stop)
            # Consider closing the loop if appropriate, maybe in a cleanup method
            # self.loop.close()
            self.logger.info("Asyncio event loop thread finished.")

    def __init__(self, simulate=False, monitor_only=False):
        """Initialize the arbitrage monitor."""
        self.logger = setup_logging() # Setup logging first
        self.logger.info("--- Initializing Arbitrage Monitor ---")
        try:
            # Load environment variables (already loaded globally, but check critical ones)
            self.network = NETWORK # Use globally loaded network
            self.private_key = os.getenv('PRIVATE_KEY')
            self.arbitrage_contract_address = os.getenv('ARBITRAGE_CONTRACT_ADDRESS')

            if not self.private_key:
                 self.logger.error("❌ CRITICAL: PRIVATE_KEY not found in environment variables.")
                 raise ValueError("Missing PRIVATE_KEY")
            if not self.arbitrage_contract_address:
                 self.logger.error("❌ CRITICAL: ARBITRAGE_CONTRACT_ADDRESS not found in environment variables.")
                 raise ValueError("Missing ARBITRAGE_CONTRACT_ADDRESS")


            # Initialize stable token selection
            self.stable_token = os.getenv('STABLE_TOKEN', 'USDC')
            if self.stable_token not in STABLECOINS: # Use globally defined STABLECOINS
                self.logger.error(f"❌ Invalid STABLE_TOKEN '{self.stable_token}' in .env.new - must be one of {STABLECOINS}")
                raise ValueError("Invalid STABLE_TOKEN")

            # Initialize borrow amount
            borrow_amount_str = os.getenv('BORROW_AMOUNT', '1000') # Default to 1000 if not set
            try:
                # Remove comments like '# 1 million'
                cleaned_borrow_amount_str = borrow_amount_str.split('#')[0].strip()
                self.borrow_amount = float(cleaned_borrow_amount_str)
                if self.borrow_amount <= 0:
                    raise ValueError("Borrow amount must be positive")
            except ValueError as e:
                self.logger.error(f"❌ Invalid BORROW_AMOUNT: '{borrow_amount_str}'. Error: {e}")
                self.logger.error("Please set BORROW_AMOUNT to a positive number (e.g., 1000).")
                raise ValueError("Invalid BORROW_AMOUNT")

            # Initialize event loop for background tasks (like polling)
            self.loop = asyncio.new_event_loop()

            # Initialize Web3 and account *before* loading tokens to fetch decimals
            self.w3 = self._initialize_web3()
            self.account = Account.from_key(self.private_key)
            self.logger.info(f"Bot Account Address: {self.account.address}")

            # Load token data from CSV and fetch decimals
            try:
                script_dir = os.path.dirname(__file__)
                project_root = os.path.dirname(script_dir) # Assumes script is in scripts/
                csv_path = os.path.join(project_root, 'tokens.csv')
                self.token_addresses, self.address_to_token_info = self._load_tokens_from_csv(csv_path)
                if not self.token_addresses:
                     self.logger.error("❌ No tokens were loaded successfully from CSV. Check tokens.csv and RPC connection.")
                     raise ValueError("Failed to load any tokens")
                # Ensure stablecoin is loaded
                if self.stable_token not in self.token_addresses:
                    self.logger.error(f"❌ Configured stablecoin {self.stable_token} not found in loaded tokens.csv")
                    raise ValueError(f"Stablecoin {self.stable_token} missing from tokens.csv")

            except Exception as load_error:
                 self.logger.error(f"❌ CRITICAL: Failed to load token data. Exiting. Error: {load_error}")
                 self.logger.error(traceback.format_exc())
                 raise # Re-raise critical error

            # Start event loop in separate thread *after* w3 is initialized
            self.loop_thread = threading.Thread(target=self._run_event_loop, name="AsyncioLoopThread", daemon=True)
            self.loop_thread.start()

            # Initialize state variables
            self.pools: Dict[Tuple[str, str, int, str], Contract] = {}  # Stores pool contract instances: (t0_sym, t1_sym, fee, dex) -> contract
            self.pool_token_info: Dict[Tuple[str, str, int, str], Dict] = {} # Stores actual token order and decimals: pool_key -> {'token0': {'addr': '...', 'symbol': '...', 'decimals': ...}, 'token1': {...}}
            self.pairs_to_monitor: List[Tuple[str, str]] = [] # Will be populated by initialize_token_pairs
            self.swap_filters: Dict[Tuple[str, str, int, str], Dict] = {}  # Stores polling info: pool_key -> {'last_block': ..., 'pool': contract, 'filter': LogFilter}
            self.fee_tiers = [100, 500, 3000, 10000]  # 0.01%, 0.05%, 0.3%, 1% - Added 100 tier

            # Load configurable parameters with optimized thresholds
            self.min_profit_threshold = float(os.getenv('MIN_PROFIT_THRESHOLD', '0.1'))  # 0.1% minimum spread (reduced default)
            self.min_profit_usd = float(os.getenv('MIN_PROFIT_USD', '5.0'))  # Minimum $5 profit (reduced default)
            self.max_trade_size_eth = float(os.getenv('MAX_TRADE_SIZE_ETH', '1.0')) # Max 1 ETH equivalent per trade (more specific name)
            self.gas_price_gwei_limit = int(os.getenv('GAS_PRICE_GWEI_LIMIT', '10'))  # 10 gwei gas price limit (more specific name)

            # Add monitor_only and simulate flags from args
            self.monitor_only = monitor_only
            self.simulate = simulate
            if self.simulate:
                self.logger.warning("⚠️ Running in SIMULATION mode. No transactions will be sent.")
            if self.monitor_only:
                self.logger.warning("⚠️ Running in MONITOR ONLY mode. No transactions will be sent.")

            # Print configuration
            self._print_config()

            # Load contract ABIs
            if not self._load_abis():
                raise RuntimeError("Failed to load contract ABIs.")

            # Initialize Factory and Executor contracts
            self._initialize_contracts() # Call the method here

            # Initialize token pairs to monitor
            self.initialize_token_pairs() # Needs implementation or hardcoding

            # Initialize pools for the monitored pairs
            self._initialize_pools()

            # Check ETH balance
            eth_balance = self.check_eth_balance()
            if eth_balance < 0.01 and not self.simulate and not self.monitor_only: # Check for minimum balance if executing
                self.logger.warning(f"⚠️ Low ETH balance ({eth_balance:.6f} ETH). May not be enough for gas.")


            # Print welcome banner AFTER initialization
            self._print_welcome_banner()
            self.logger.info("--- Initialization Complete ---")


        except Exception as e:
            self.logger.error(f"❌ Initialization failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            # Ensure loop thread is handled if started
            if hasattr(self, 'loop') and self.loop.is_running():
                 self.loop.call_soon_threadsafe(self.loop.stop)
            raise # Re-raise after logging

    def _load_abis(self):
        """Load contract ABIs from artifacts or fallback abis/ directory."""
        self.logger.debug("Loading contract ABIs...")
        try:
            project_dir = os.path.dirname(os.path.dirname(__file__)) # Project root is one level above scripts/
            self.logger.debug(f"Project directory determined as: {project_dir}")

            # Helper function to load ABI
            def load_single_abi(artifact_subpath, fallback_name):
                artifact_path = os.path.join(project_dir, 'artifacts', artifact_subpath)
                fallback_path = os.path.join(project_dir, 'abis', fallback_name)
                abi_path_to_use = None
                abi = None

                if os.path.exists(artifact_path):
                    abi_path_to_use = artifact_path
                    self.logger.debug(f"Attempting to load ABI from artifact: {abi_path_to_use}")
                    with open(abi_path_to_use) as f:
                        data = json.load(f)
                        if isinstance(data, dict) and 'abi' in data:
                            abi = data['abi']
                        else:
                             self.logger.warning(f"Artifact format unrecognized in {abi_path_to_use}, trying fallback.")
                elif os.path.exists(fallback_path):
                     abi_path_to_use = fallback_path
                     self.logger.warning(f"Artifact not found, using fallback ABI path: {abi_path_to_use}")
                     with open(abi_path_to_use) as f:
                         data = json.load(f)
                         # Fallback might be just the ABI list or artifact format
                         if isinstance(data, list):
                             abi = data
                         elif isinstance(data, dict) and 'abi' in data:
                             abi = data['abi']
                         else:
                             raise ValueError(f"Unrecognized format for ABI in {abi_path_to_use}")
                else:
                     raise FileNotFoundError(f"Could not find ABI artifact ({artifact_path}) or fallback ({fallback_path})")

                if abi:
                    self.logger.debug(f"Successfully loaded ABI from {abi_path_to_use}")
                    return abi
                else:
                    raise ValueError(f"Failed to extract ABI from {abi_path_to_use}")


            # Load ABIs using the helper
            self.factory_abi = load_single_abi(os.path.join('@uniswap', 'v3-core', 'contracts', 'interfaces', 'IUniswapV3Factory.sol', 'IUniswapV3Factory.json'), 'UniswapV3Factory.json')
            self.pool_abi = load_single_abi(os.path.join('@uniswap', 'v3-core', 'contracts', 'interfaces', 'IUniswapV3Pool.sol', 'IUniswapV3Pool.json'), 'UniswapV3Pool.json')
            self.executor_abi = load_single_abi(os.path.join('contracts', 'ArbitrageExecutor.sol', 'ArbitrageExecutor.json'), 'ArbitrageExecutor.json')

            self.logger.info("✅ Loaded contract ABIs")
            return True

        except FileNotFoundError as fnf_error:
             self.logger.error(f"❌ ABI file not found: {fnf_error}")
             self.logger.error(traceback.format_exc())
             return False
        except json.JSONDecodeError as json_error:
             self.logger.error(f"❌ Error decoding JSON in ABI file: {json_error}")
             self.logger.error(traceback.format_exc())
             return False
        except ValueError as val_error:
             self.logger.error(f"❌ Error processing ABI file: {val_error}")
             self.logger.error(traceback.format_exc())
             return False
        except Exception as e:
            self.logger.error(f"❌ Unexpected error loading ABIs: {str(e)}")
            self.logger.error(traceback.format_exc())
            return False

    def _load_tokens_from_csv(self, file_path: str) -> Tuple[Dict[str, str], Dict[str, Dict]]:
        """Loads token addresses and info from CSV, fetching decimals via web3."""
        token_addresses: Dict[str, str] = {} # symbol -> address
        address_to_token_info: Dict[str, Dict] = {} # address -> {'symbol': str, 'decimals': int}
        symbol_pattern = re.compile(r'\(([^)]+)\)') # Regex to find symbol in parentheses

        self.logger.info(f"Loading tokens from {file_path} and fetching decimals...")
        if not os.path.exists(file_path):
             self.logger.error(f"❌ Token file not found at: {file_path}")
             raise FileNotFoundError(f"Token file not found: {file_path}")

        try:
            with open(file_path, mode='r', encoding='utf-8') as infile:
                reader = csv.reader(infile)
                try:
                    header = next(reader) # Skip header row
                    self.logger.debug(f"CSV Header: {header}")
                except StopIteration:
                    self.logger.error(f"❌ Token file is empty: {file_path}")
                    return token_addresses, address_to_token_info # Return empty dicts

                processed_count = 0
                skipped_count = 0
                fetch_errors = 0
                for row_num, row in enumerate(reader, start=2):
                    if not row or len(row) < 2:
                        self.logger.warning(f"Skipping row {row_num}: Insufficient columns or empty row.")
                        skipped_count += 1
                        continue

                    name_field = row[0].strip()
                    address_str = row[1].strip()

                    # Extract symbol
                    match = symbol_pattern.search(name_field)
                    if not match:
                        # Fallback: Use the whole name field if no parentheses
                        symbol = name_field
                        if not symbol: # Skip if name is also empty
                             self.logger.warning(f"Skipping row {row_num}: Empty name field.")
                             skipped_count += 1
                             continue
                        self.logger.debug(f"Row {row_num}: Using '{symbol}' as symbol (no parentheses found in '{name_field}').")
                    else:
                        symbol = match.group(1).strip()

                    # Validate address
                    if not address_str or not Web3.is_address(address_str):
                        self.logger.warning(f"Skipping row {row_num}: Invalid address '{address_str}' for symbol '{symbol}'.")
                        skipped_count += 1
                        continue
                    checksum_address = Web3.to_checksum_address(address_str)

                    # Check for duplicates
                    if symbol in token_addresses:
                         self.logger.warning(f"Skipping row {row_num}: Duplicate symbol '{symbol}' found for address {checksum_address}. Previous: {token_addresses[symbol]}")
                         skipped_count += 1
                         continue
                    if checksum_address in address_to_token_info:
                         prev_symbol = address_to_token_info[checksum_address]['symbol']
                         self.logger.warning(f"Skipping row {row_num}: Duplicate address '{checksum_address}' found for symbol '{symbol}'. Previous symbol: '{prev_symbol}'")
                         skipped_count += 1
                         continue


                    # Fetch decimals dynamically
                    try:
                        if not hasattr(self, 'w3') or not self.w3 or not self.w3.is_connected():
                             self.logger.error("Web3 connection not available for fetching decimals.")
                             # Decide whether to raise an error or skip all remaining tokens
                             raise ConnectionError("Web3 not connected, cannot fetch decimals.")

                        token_contract = self.w3.eth.contract(address=checksum_address, abi=MINIMAL_ERC20_ABI)
                        decimals = token_contract.functions.decimals().call()
                        self.logger.debug(f"Fetched decimals for {symbol} ({checksum_address}): {decimals}")

                        # Populate dictionaries
                        token_addresses[symbol] = checksum_address
                        address_to_token_info[checksum_address] = {'symbol': symbol, 'decimals': decimals}
                        processed_count += 1

                    except ConnectionError as ce:
                         self.logger.error(f"Stopping token loading due to Web3 connection error: {ce}")
                         raise # Propagate connection error
                    except Exception as e:
                        self.logger.warning(f"Skipping token '{symbol}' ({checksum_address}): Could not fetch decimals. Error: {str(e)[:150]}...") # Log snippet of error
                        skipped_count += 1
                        fetch_errors += 1
                        continue # Continue with the next token

                    # Optional: Add a small delay to avoid hitting RPC rate limits if the CSV is very large
                    # time.sleep(0.01) # Reduced delay

            self.logger.info(f"Token loading complete. Processed: {processed_count}, Skipped: {skipped_count}, Fetch Errors: {fetch_errors}.")
            if fetch_errors > 0:
                 self.logger.warning("Some tokens were skipped due to errors fetching decimals. Check RPC connection and token addresses.")

        except FileNotFoundError:
             # Already handled above, but keep for safety
             self.logger.error(f"❌ Token file not found at: {file_path}")
             raise
        except Exception as e:
            self.logger.error(f"❌ Unexpected error loading tokens from {file_path}: {e}")
            self.logger.error(traceback.format_exc())
            raise # Re-raise unexpected errors

        return token_addresses, address_to_token_info

    def _initialize_web3(self):
        """Initialize Web3 connection, trying multiple RPC endpoints."""
        self.logger.info("Initializing Web3 connection...")
        # Use the globally configured RPC URL first
        primary_rpc_url = current_network.get('rpc_url')
        fallback_rpc_urls = [
            "https://base.publicnode.com",
            "https://1rpc.io/base",
            # Add more public fallbacks if needed
        ]

        # Combine URLs, prioritizing the configured one
        rpc_urls_to_try = []
        if primary_rpc_url:
            rpc_urls_to_try.append(primary_rpc_url)
        # Add fallbacks, avoiding duplicates
        for url in fallback_rpc_urls:
            if url not in rpc_urls_to_try:
                rpc_urls_to_try.append(url)

        if not rpc_urls_to_try:
             self.logger.error("❌ No RPC URLs configured or available to try.")
             raise ConnectionError("No RPC URLs available for Web3 connection.")

        w3 = None
        for rpc_url in rpc_urls_to_try:
            try:
                self.logger.info(f"Attempting connection to: {rpc_url}")
                provider = Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 10}) # Add timeout
                temp_w3 = Web3(provider)

                # Inject POA middleware if needed (common for Base/Polygon)
                # temp_w3.middleware_onion.inject(geth_poa_middleware, layer=0)

                # Verify connection and chain ID
                if temp_w3.is_connected():
                    chain_id = temp_w3.eth.chain_id
                    expected_chain_id = current_network['base']['chain_id']
                    if chain_id == expected_chain_id:
                        self.logger.info(f"✅ Connected to {current_network['name']} (Chain ID: {chain_id}) via {rpc_url}")
                        w3 = temp_w3 # Assign the successful connection
                        break # Exit loop on success
                    else:
                        self.logger.warning(f"Connection successful via {rpc_url}, but Chain ID mismatch. Expected: {expected_chain_id}, Got: {chain_id}.")
                else:
                    self.logger.warning(f"Connection failed via {rpc_url}: Not connected.")

            except ProviderConnectionError as pce:
                 # Indent the line inside the except block
                 self.logger.warning(f"❌ Provider connection error for {rpc_url}: {pce}")
            except requests.exceptions.Timeout:
                 # Indent the line inside the except block
                 self.logger.warning(f"❌ Connection timed out for {rpc_url}")
            except Exception as e:
                 # Indent the lines inside the except block
                 self.logger.warning(f"❌ Unexpected error connecting via {rpc_url}: {str(e)}")
                 # Continue to the next URL in the list

        # Check if connection was successful after trying all URLs
        if w3 and w3.is_connected():
             self.logger.info(f"Using RPC URL: {w3.provider.endpoint_uri}")
             return w3
        else:
             self.logger.error("❌ Failed to connect to any Base Mainnet RPC endpoint after trying all options.")
             raise ConnectionError("Could not establish Web3 connection to Base Mainnet.")


    def _initialize_contracts(self):
        """Initialize factory and executor contracts."""
        self.logger.info("Initializing core contracts...")
        try:
            # Initialize Uniswap factory
            self.uniswap_factory = self.w3.eth.contract(
                address=UNISWAP_V3_FACTORY,
                abi=self.factory_abi
            )
            self.logger.info(f"✅ Initialized Uniswap V3 Factory contract at {UNISWAP_V3_FACTORY}")

            # Initialize Sushiswap factory
            self.sushiswap_factory = self.w3.eth.contract(
                address=SUSHISWAP_V3_FACTORY,
                abi=self.factory_abi # Assuming Sushi uses the same factory interface ABI
            )
            self.logger.info(f"✅ Initialized SushiSwap V3 Factory contract at {SUSHISWAP_V3_FACTORY}")

            # Initialize ArbitrageExecutor contract
            try:
                executor_address = Web3.to_checksum_address(self.arbitrage_contract_address)
                self.executor_contract = self.w3.eth.contract(
                    address=executor_address,
                    abi=self.executor_abi
                )
                self.logger.info(f"✅ Initialized ArbitrageExecutor contract at {executor_address}")
            except ValueError:
                 self.logger.error(f"❌ Invalid ARBITRAGE_CONTRACT_ADDRESS format: '{self.arbitrage_contract_address}'")
                 raise
            except Exception as contract_init_error:
                 self.logger.error(f"❌ Error initializing ArbitrageExecutor contract at {self.arbitrage_contract_address}: {contract_init_error}")
                 raise # Re-raise the specific error

        # Outer except for any error during contract initialization
        except Exception as e:
            self.logger.error(colored(f"❌ Error initializing core contracts: {str(e)}", 'red'))
            self.logger.error(traceback.format_exc()) # Add traceback
            raise # Re-raise the general error

    def _initialize_pools(self):
        """Initialize pool contracts for monitored pairs and store their token info."""
        self.logger.info(f"Initializing pools for {len(self.pairs_to_monitor)} monitored pairs...")
        if not self.pairs_to_monitor:
             self.logger.warning("No token pairs configured for monitoring. Skipping pool initialization.")
             return

        processed_pools = 0
        failed_pools = 0
        for token0_sym, token1_sym in self.pairs_to_monitor:
            for fee in self.fee_tiers:
                for dex in ['uniswap', 'sushiswap']:
                    pool_key = (token0_sym, token1_sym, fee, dex)
                    try:
                        pool_contract = self._get_pool(token0_sym, token1_sym, fee, dex)
                        if pool_contract:
                            # Store contract instance
                            self.pools[pool_key] = pool_contract
                            # Fetch and store token order and decimals
                            token0_addr_pool = pool_contract.functions.token0().call()
                            token1_addr_pool = pool_contract.functions.token1().call()
                            token0_info = self.address_to_token_info.get(token0_addr_pool)
                            token1_info = self.address_to_token_info.get(token1_addr_pool)

                            if not token0_info or not token1_info:
                                 self.logger.error(f"❌ Could not find loaded token info for pool {pool_key} tokens: {token0_addr_pool}, {token1_addr_pool}")
                                 failed_pools += 1
                                 continue

                            self.pool_token_info[pool_key] = {
                                'token0': {'addr': token0_addr_pool, 'symbol': token0_info['symbol'], 'decimals': token0_info['decimals']},
                                'token1': {'addr': token1_addr_pool, 'symbol': token1_info['symbol'], 'decimals': token1_info['decimals']}
                            }
                            self.logger.debug(f"Successfully initialized and stored info for pool {pool_key} at {pool_contract.address}")
                            processed_pools += 1
                        else:
                            # _get_pool already logged the warning/error
                            failed_pools += 1
                    except Exception as e:
                        self.logger.error(f"❌ Failed to initialize pool {pool_key}: {e}")
                        failed_pools += 1

        self.logger.info(f"Pool initialization complete. Successfully processed: {processed_pools}, Failed/Not Found: {failed_pools}")


    def _get_pool(self, token0_symbol: str, token1_symbol: str, fee: int, dex: str) -> Optional[Contract]:
        """Get or initialize a pool contract from the specified DEX factory."""
        pool_key = (token0_symbol, token1_symbol, fee, dex)
        # Don't return cached pool here, always fetch from factory in this context
        # if pool_key in self.pools:
        #     return self.pools[pool_key]

        self.logger.debug(f"Attempting to find pool for {token0_symbol}/{token1_symbol} fee {fee} on {dex}...")

        token0_addr = self.token_addresses.get(token0_symbol)
        token1_addr = self.token_addresses.get(token1_symbol)

        if not token0_addr or not token1_addr:
            self.logger.error(f"Token address not found for {token0_symbol} or {token1_symbol}")
            return None

        factory_contract = self.uniswap_factory if dex == 'uniswap' else self.sushiswap_factory

        try:
            # Ensure addresses are checksummed
            token0_addr_cs = Web3.to_checksum_address(token0_addr)
            token1_addr_cs = Web3.to_checksum_address(token1_addr)

            pool_address = factory_contract.functions.getPool(
                token0_addr_cs,
                token1_addr_cs,
                fee
            ).call()

            # Address(0) means pool doesn't exist
            if pool_address == '0x0000000000000000000000000000000000000000':
                self.logger.debug(f"Pool not found for {token0_symbol}/{token1_symbol} fee {fee} on {dex}.")
                return None

            pool_contract = self.w3.eth.contract(address=pool_address, abi=self.pool_abi)
            self.logger.debug(f"Found pool {pool_key} at address: {pool_address}")
            # Store the contract instance in _initialize_pools after validation
            return pool_contract

        except Exception as e:
            self.logger.error(f"Error getting pool {pool_key} from {dex} factory: {e}")
            # self.logger.error(traceback.format_exc()) # Optional: Add traceback for debugging
            return None


    def run(self):
        """Main loop to monitor pools and check for arbitrage."""
        self.logger.info("🚀 Starting Arbitrage Monitor Run Loop 🚀")
        if not self.pools:
             self.logger.error("No pools initialized. Cannot monitor. Exiting run loop.")
             return

        # Create event filters for all initialized pools
        self._create_all_swap_event_filters()

        if not self.swap_filters:
             self.logger.error("No swap event filters created. Cannot monitor. Exiting run loop.")
             return

        self.logger.info(f"Monitoring {len(self.swap_filters)} pools for swap events...")

        # Start the polling loop in the background thread
        poll_future = asyncio.run_coroutine_threadsafe(self._poll_swap_events_periodically(), self.loop)

        # Keep the main thread alive (e.g., wait for keyboard interrupt or future completion)
        try:
            # Wait for the polling future to complete (it runs indefinitely until cancelled)
            # Or use a simple loop with sleep
            while self.loop_thread.is_alive():
                time.sleep(1)
                # Add other main thread checks if needed
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt received in main thread. Initiating shutdown...")
            if not poll_future.done():
                 # Cancel the polling coroutine
                 self.loop.call_soon_threadsafe(poll_future.cancel)
        except Exception as main_thread_error:
             self.logger.error(f"Unexpected error in main thread: {main_thread_error}")
             self.logger.error(traceback.format_exc())
             if not poll_future.done():
                 self.loop.call_soon_threadsafe(poll_future.cancel)
        finally:
            self.shutdown()


    def shutdown(self):
        """Gracefully shuts down the monitor."""
        self.logger.info("Initiating shutdown sequence...")
        # Stop the asyncio loop
        if hasattr(self, 'loop') and self.loop.is_running():
            self.logger.info("Requesting asyncio loop stop...")
            # Cancel any running tasks if needed before stopping
            # Example: Find the polling task and cancel it
            tasks = asyncio.all_tasks(self.loop)
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Give tasks a moment to cancel
            # self.loop.run_until_complete(asyncio.sleep(0.1)) # Run within loop if possible
            self.loop.call_soon_threadsafe(self.loop.stop)

        # Wait for the loop thread to finish
        if hasattr(self, 'loop_thread') and self.loop_thread.is_alive():
            self.logger.info("Waiting for asyncio loop thread to join...")
            self.loop_thread.join(timeout=10) # Wait up to 10 seconds
            if self.loop_thread.is_alive():
                 self.logger.warning("Loop thread did not join cleanly.")

        # Close the loop if it exists and is not closed
        if hasattr(self, 'loop') and not self.loop.is_closed():
             # Ensure loop is stopped before closing
             while self.loop.is_running():
                 time.sleep(0.1)
             self.logger.info("Closing asyncio loop.")
             self.loop.close()

        self.logger.info("👋 Arbitrage Monitor Shutdown Complete.")


    def _create_all_swap_event_filters(self):
        """Creates swap event filters for all initialized pools."""
        self.logger.info("Creating swap event filters...")
        created_count = 0
        failed_count = 0
        for pool_key, pool_contract in self.pools.items():
            try:
                # Use create_filter which is synchronous but suitable here
                event_filter = pool_contract.events.Swap.create_filter(fromBlock='latest')
                self.swap_filters[pool_key] = {
                    'pool': pool_contract,
                    'filter': event_filter,
                    'last_block': self.w3.eth.block_number # Store current block num
                }
                self.logger.debug(f"Created swap filter for pool {pool_key}")
                created_count += 1
            except Exception as e:
                self.logger.error(f"❌ Failed to create swap filter for pool {pool_key}: {e}")
                failed_count += 1
        self.logger.info(f"Filter creation complete. Created: {created_count}, Failed: {failed_count}")


    async def _poll_swap_events_periodically(self, interval=2):
         """Periodically polls swap event filters in the asyncio loop."""
         self.logger.info(f"Starting periodic swap event polling (interval: {interval}s)...")
         while True:
             try:
                 start_time = time.monotonic()
                 self.logger.debug("Polling for new swap events...")
                 found_events = False
                 tasks = [] # For processing events concurrently if needed

                 for pool_key, filter_info in self.swap_filters.items():
                     try:
                         new_entries = filter_info['filter'].get_new_entries()
                         if new_entries:
                             found_events = True
                             self.logger.info(f"Found {len(new_entries)} new swap event(s) for pool {pool_key}")
                             for event in new_entries:
                                 # Option 1: Process sequentially (simpler)
                                 self._process_swap_event(pool_key, event)
                                 # Option 2: Process concurrently (if _process_swap_event is async)
                                 # tasks.append(asyncio.create_task(self._process_swap_event(pool_key, event)))
                     except Exception as poll_error:
                          self.logger.error(f"Error polling filter for pool {pool_key}: {poll_error}")
                          # Consider removing or recreating the filter if errors persist

                 # If using concurrent processing:
                 # if tasks:
                 #    await asyncio.gather(*tasks)

                 if found_events:
                      self.logger.debug("Finished processing batch of swap events.")
                 else:
                      self.logger.debug("No new swap events found in this poll.")

                 # Calculate time taken and sleep accordingly
                 elapsed = time.monotonic() - start_time
                 sleep_duration = max(0, interval - elapsed)
                 if elapsed > interval:
                      self.logger.warning(f"Polling took longer ({elapsed:.2f}s) than interval ({interval}s).")
                 await asyncio.sleep(sleep_duration)

             except asyncio.CancelledError:
                  self.logger.info("Swap event polling task cancelled.")
                  break # Exit the loop cleanly
             except Exception as e:
                  self.logger.error(f"❌ Unexpected error in polling loop: {e}")
                  self.logger.error(traceback.format_exc())
                  await asyncio.sleep(interval * 5) # Longer sleep on error


    def _print_config(self):
        """Print the current configuration."""
        self.logger.info("\n--- Bot Configuration ---")
        self.logger.info(f"Network: {self.network} ({current_network['name']})")
        self.logger.info(f"Account: {self.account.address}")
        self.logger.info(f"Stablecoin for Borrowing: {self.stable_token}")
        self.logger.info(f"Borrow Amount: {self.borrow_amount} {self.stable_token}")
        self.logger.info(f"Min Profit Threshold (%): {self.min_profit_threshold * 100:.4f}%")
        self.logger.info(f"Min Profit USD ($): ${self.min_profit_usd:.2f}")
        self.logger.info(f"Max Trade Size (ETH): {self.max_trade_size_eth} ETH")
        self.logger.info(f"Gas Price Limit (Gwei): {self.gas_price_gwei_limit} Gwei")
        self.logger.info(f"Simulate Mode: {self.simulate}")
        self.logger.info(f"Monitor Only Mode: {self.monitor_only}")
        # self.logger.info(f"Monitored Pairs: {self.pairs_to_monitor}") # Printed after initialization
        self.logger.info(f"Fee Tiers: {self.fee_tiers}")
        self.logger.info("-------------------------")


    def _print_welcome_banner(self):
        """Print a welcome banner."""
        banner = """
        ***********************************************************
        *                                                         *
        *      🦄 Uniswap/Sushiswap V3 Arbitrage Bot (Base) 🍣     *
        *                                                         *
        ***********************************************************
        """
        # Use print directly for the banner to ensure it appears before logging might be fully set up sometimes
        print(colored(banner, 'magenta', attrs=['bold']))

    def initialize_token_pairs(self):
        """Initialize token pairs to monitor based on loaded tokens and the configured stablecoin."""
        self.logger.info(f"Generating token pairs against stablecoin: {self.stable_token}")

        if self.stable_token not in self.token_addresses:
             self.logger.error(f"❌ Configured stablecoin '{self.stable_token}' not found in loaded tokens. Cannot generate pairs.")
             raise ValueError(f"Stablecoin {self.stable_token} missing from loaded tokens.")

        generated_pairs = []
        # Iterate through all loaded token symbols
        for token_symbol in self.token_addresses.keys():
            if token_symbol == self.stable_token:
                continue # Don't pair stablecoin with itself

            # Create pairs between each token and the stablecoin
            # Downstream logic might expect a specific order (e.g., WETH/USDC vs USDC/WETH)
            # For now, let's create (token, stable) pairs. Adjust if needed.
            pair = (token_symbol, self.stable_token)
            generated_pairs.append(pair)
            self.logger.debug(f"Generated pair for monitoring: {pair}")

        # Use the dynamically generated pairs
        valid_pairs = generated_pairs

        if not valid_pairs:
             self.logger.error("❌ No valid token pairs configured for monitoring after validation. Exiting.")
             raise ValueError("No valid token pairs to monitor.")

        self.pairs_to_monitor = valid_pairs
        self.logger.info(f"Validated Monitored Pairs: {self.pairs_to_monitor}")


    def check_eth_balance(self) -> float:
        """Checks and returns the ETH balance of the bot's account."""
        self.logger.debug(f"Checking ETH balance for {self.account.address}...")
        try:
            balance_wei = self.w3.eth.get_balance(self.account.address)
            balance_eth = self.w3.from_wei(balance_wei, 'ether')
            self.logger.info(f"Current ETH Balance: {balance_eth:.6f} ETH")
            return float(balance_eth)
        except Exception as e:
            self.logger.error(f"❌ Error checking ETH balance: {e}")
            return 0.0

    def _calculate_price_from_sqrt_price_x96(self, sqrt_price_x96: int, token0_info: Dict, token1_info: Dict) -> Optional[Decimal]:
        """
        Calculate human-readable price (token1 per token0) from sqrtPriceX96.
        Handles potential division by zero and uses Decimal for precision.
        """
        try:
            if sqrt_price_x96 == 0:
                 self.logger.warning("sqrtPriceX96 is zero, cannot calculate price.")
                 return None

            # Formula: price = (sqrtPriceX96 / 2^96)^2 * 10^(decimals0 - decimals1)
            # Use Decimal for calculations to maintain precision
            sqrt_price_decimal = Decimal(sqrt_price_x96)
            two_pow_96 = Decimal(2**96)

            # This check is technically redundant if sqrt_price_x96 != 0, but safe
            if two_pow_96 == 0:
                 self.logger.error("Division by zero error: 2^96 is zero?")
                 return None

            price_ratio_sq = (sqrt_price_decimal / two_pow_96) ** 2

            decimals0 = token0_info['decimals']
            decimals1 = token1_info['decimals']
            decimal_adjustment = Decimal(10) ** (decimals0 - decimals1)

            price = price_ratio_sq * decimal_adjustment
            # self.logger.debug(f"Calculated price ({token1_info['symbol']}/{token0_info['symbol']}): {price:.18f}") # Log with high precision if needed
            return price

        except Exception as e:
            self.logger.error(f"Error calculating price from sqrtPriceX96 ({sqrt_price_x96}): {e}")
            return None


    def _process_swap_event(self, pool_key: Tuple[str, str, int, str], event: EventData):
        """Process a Swap event: log details and check for arbitrage."""
        try:
            token0_sym_pair, token1_sym_pair, fee, dex = pool_key # These are the pair symbols used as key
            args = event['args']
            tx_hash = event['transactionHash'].hex()
            block_num = event['blockNumber']

            # Get token info for this specific pool (handles actual token0/token1 order in pool)
            pool_info = self.pool_token_info.get(pool_key)
            if not pool_info:
                 self.logger.error(f"Missing token info for pool {pool_key} during swap processing.")
                 return

            token0_pool = pool_info['token0'] # Actual token0 of the pool contract
            token1_pool = pool_info['token1'] # Actual token1 of the pool contract

            # Decode amounts using correct decimals for the pool's tokens
            # Use Decimal for precision right away
            amount0_delta_raw = args['amount0']
            amount1_delta_raw = args['amount1']
            amount0_delta = Decimal(amount0_delta_raw) / (Decimal(10) ** token0_pool['decimals'])
            amount1_delta = Decimal(amount1_delta_raw) / (Decimal(10) ** token1_pool['decimals'])

            # Determine direction and swapped amounts based on sign
            if amount0_delta > 0: # Positive amount0 means token0 (pool's) was sent INTO the pool
                amount_in_str = f"{amount0_delta:.6f} {token0_pool['symbol']}"
                amount_out_str = f"{-amount1_delta:.6f} {token1_pool['symbol']}" # amount1 is negative
            elif amount1_delta > 0: # Positive amount1 means token1 (pool's) was sent INTO the pool
                amount_in_str = f"{amount1_delta:.6f} {token1_pool['symbol']}"
                amount_out_str = f"{-amount0_delta:.6f} {token0_pool['symbol']}" # amount0 is negative
            else:
                 # This case (both zero or both negative) shouldn't happen in a valid Swap event
                 self.logger.warning(f"Invalid swap amounts in event for pool {pool_key}: amount0={amount0_delta_raw}, amount1={amount1_delta_raw}")
                 return


            # Calculate price from sqrtPriceX96 *after* the swap
            sqrt_price_x96_after = args['sqrtPriceX96']
            current_price = self._calculate_price_from_sqrt_price_x96(sqrt_price_x96_after, token0_pool, token1_pool)
            price_str = f"{current_price:.8f}" if current_price is not None else "N/A"

            log_message = (
                f"Block: {block_num} | Pool: {dex.upper()} {token0_pool['symbol']}/{token1_pool['symbol']} ({fee/10000}%) | Tx: {tx_hash[:10]}...\n"
                f"  Swap: {amount_in_str} -> {amount_out_str}\n"
                f"  New Price ({token1_pool['symbol']}/{token0_pool['symbol']}): {price_str}\n"
                f"  New Liquidity: {args['liquidity']}"
            )
            # Use a distinct message title for logging
            self.logger.info(f"SWAP EVENT DETECTED\n{log_message}")


            # Check for arbitrage opportunity after processing the swap
            # Use the PAIR symbols (token0_sym_pair, token1_sym_pair) that define the monitoring target
            self._check_arbitrage_opportunity(token0_sym_pair, token1_sym_pair, fee)

        except Exception as e:
            self.logger.error(f"Error processing swap event for pool {pool_key}: {e}")
            self.logger.error(traceback.format_exc())


    def _adjust_decimals(self, amount: int, token_symbol: str) -> Optional[Decimal]:
        """Adjust raw integer amount based on token decimals using Decimal."""
        # Find the address for the symbol
        token_addr = self.token_addresses.get(token_symbol)
        if not token_addr:
            self.logger.error(f"Cannot adjust decimals: Token symbol '{token_symbol}' not found.")
            return None

        # Find the decimals for the address
        token_info = self.address_to_token_info.get(token_addr)
        if not token_info or 'decimals' not in token_info:
            self.logger.error(f"Cannot adjust decimals: Decimals not found for token '{token_symbol}' ({token_addr}).")
            return None

        decimals = token_info['decimals']
        try:
            # Ensure amount is an integer before converting to Decimal
            if not isinstance(amount, int):
                 self.logger.warning(f"Amount provided to _adjust_decimals is not an integer: {amount} ({type(amount)})")
                 # Attempt conversion, or return None/raise error
                 try:
                     amount = int(amount)
                 except (ValueError, TypeError):
                      self.logger.error("Failed to convert amount to integer.")
                      return None

            adjusted_amount = Decimal(amount) / (Decimal(10) ** decimals)
            return adjusted_amount
        except Exception as e:
            self.logger.error(f"Error adjusting decimals for {token_symbol} (amount: {amount}, decimals: {decimals}): {e}")
            return None


    def _check_arbitrage_opportunity(self, token0_key_symbol, token1_key_symbol, fee):
        """
        Check for arbitrage opportunities between Uniswap and Sushiswap for a given pair and fee tier.
        Compares prices and calculates potential profit.
        """
        self.logger.debug(f"Checking arbitrage for {token0_key_symbol}/{token1_key_symbol} fee {fee}...")

        uniswap_pool_key = (token0_key_symbol, token1_key_symbol, fee, 'uniswap')
        sushiswap_pool_key = (token0_key_symbol, token1_key_symbol, fee, 'sushiswap')

        # Ensure both pools exist and we have their info
        if uniswap_pool_key not in self.pools or sushiswap_pool_key not in self.pools:
            self.logger.debug(f"One or both pools missing for {token0_key_symbol}/{token1_key_symbol} fee {fee}. Skipping check.")
            return
        if uniswap_pool_key not in self.pool_token_info or sushiswap_pool_key not in self.pool_token_info:
             self.logger.error(f"Missing token info for one or both pools for {token0_key_symbol}/{token1_key_symbol} fee {fee}.")
             return


        try:
            # Get current prices from both pools using the get_pool_price helper
            uni_price = self.get_pool_price(uniswap_pool_key)
            sushi_price = self.get_pool_price(sushiswap_pool_key)

            if uni_price is None or sushi_price is None:
                self.logger.warning(f"Could not get price for one or both pools ({uniswap_pool_key}, {sushiswap_pool_key}). Skipping check.")
                return

            # Price sanity checks
            uni_token1_sym = self.pool_token_info[uniswap_pool_key]['token1']['symbol'] # Get actual token1 symbol for logging
            sushi_token1_sym = self.pool_token_info[sushiswap_pool_key]['token1']['symbol']
            if not self._is_price_reasonable(uni_price, token0_key_symbol, uni_token1_sym) or \
               not self._is_price_reasonable(sushi_price, token0_key_symbol, sushi_token1_sym):
                self.logger.debug("Price deemed unreasonable. Skipping arbitrage check.")
                return


            # --- Arbitrage Logic ---
            # Calculate percentage difference
            price_diff = abs(uni_price - sushi_price)
            avg_price = (uni_price + sushi_price) / 2
            if avg_price == 0:
                 self.logger.debug("Average price is zero, cannot calculate percentage difference.")
                 return # Avoid division by zero

            potential_profit_percent_decimal = (price_diff / avg_price) # As a decimal ratio
            potential_profit_percent = potential_profit_percent_decimal * 100 # As percentage

            # Determine which DEX has higher/lower price
            higher_price_dex = 'uniswap' if uni_price > sushi_price else 'sushiswap'
            lower_price_dex = 'sushiswap' if uni_price > sushi_price else 'uniswap'
            higher_price = max(uni_price, sushi_price)
            lower_price = min(uni_price, sushi_price)

            self.logger.debug(
                f"Arbitrage Check: {token0_key_symbol}/{token1_key_symbol} (Fee: {fee/10000}%)\n"
                f"  Uniswap Price ({uni_token1_sym}/{token0_key_symbol}): {uni_price:.8f}\n"
                f"  Sushiswap Price ({sushi_token1_sym}/{token0_key_symbol}): {sushi_price:.8f}\n"
                f"  Difference: {potential_profit_percent:.4f}%"
            )


            # Check against profit threshold (percentage)
            if potential_profit_percent >= (self.min_profit_threshold * 100): # Compare percentages
                # Estimate profit in stablecoin based on borrow amount and price diff ratio
                estimated_profit_stablecoin = self.borrow_amount * float(potential_profit_percent_decimal)

                # Check if stablecoin is USDC or DAI for USD comparison
                is_usd_stable = self.stable_token in ['USDC', 'DAI'] # Add other USD stables if needed
                profit_check_passed = False
                if is_usd_stable:
                    if estimated_profit_stablecoin >= self.min_profit_usd:
                         profit_check_passed = True
                    else:
                         self.logger.info(f"Potential arbitrage detected ({potential_profit_percent:.4f}%), but estimated profit ${estimated_profit_stablecoin:.2f} is below minimum ${self.min_profit_usd:.2f}.")
                else:
                     # If not a USD stablecoin, just use the percentage threshold for now
                     self.logger.warning(f"Stablecoin {self.stable_token} is not USD-pegged. Checking profit based on percentage only.")
                     profit_check_passed = True # Pass based on percentage if not USD


                if profit_check_passed:
                    log_message = (
                        f"Pair: {token0_key_symbol}/{token1_key_symbol} | Fee: {fee/10000}%\n"
                        f"  {higher_price_dex.upper()} Price: {higher_price:.8f}\n"
                        f"  {lower_price_dex.upper()} Price: {lower_price:.8f}\n"
                        f"  Potential Profit: {potential_profit_percent:.4f}% (Estimated ${estimated_profit_stablecoin:.2f} on ${self.borrow_amount:.2f} borrow)"
                    )
                    # Use a more prominent log title
                    self.logger.info(colored(f"ARBITRAGE OPPORTUNITY DETECTED\n{log_message}", 'yellow', attrs=['bold']))

                    # Execute arbitrage if not in monitor/simulate mode
                    if not self.monitor_only and not self.simulate:
                        # Pass the decimal profit percentage for potential use in execution logic
                        self._execute_arbitrage(
                            token0_key_symbol, token1_key_symbol, fee,
                            higher_price_dex, lower_price_dex,
                            potential_profit_percent_decimal # Pass the ratio
                        )
                    else:
                         self.logger.info("Monitor/Simulate mode active. No execution attempt.")

        except Exception as e:
            self.logger.error(f"Error checking arbitrage for {token0_key_symbol}/{token1_key_symbol} fee {fee}: {e}")
            self.logger.error(traceback.format_exc())


    def get_pool_price(self, pool_key: Tuple[str, str, int, str]) -> Optional[Decimal]:
        """Get the current price (token1 per token0) for a given pool."""
        token0_sym, token1_sym, fee, dex = pool_key
        # self.logger.debug(f"Getting price for pool {pool_key}...") # Reduce log noise

        if pool_key not in self.pools:
            self.logger.warning(f"Pool {pool_key} not found in initialized pools.")
            return None
        if pool_key not in self.pool_token_info:
             self.logger.error(f"Missing token info for pool {pool_key}.")
             return None

        pool_contract = self.pools[pool_key]
        token0_info = self.pool_token_info[pool_key]['token0']
        token1_info = self.pool_token_info[pool_key]['token1']

        try:
            # Use block_identifier='latest' for the most recent state
            slot0 = pool_contract.functions.slot0().call(block_identifier='latest')
            sqrt_price_x96 = slot0[0]
            price = self._calculate_price_from_sqrt_price_x96(sqrt_price_x96, token0_info, token1_info)
            # if price is not None:
            #      self.logger.debug(f"Price for {pool_key}: {price:.8f} {token1_info['symbol']}/{token0_info['symbol']}") # Reduce log noise
            return price
        except Exception as e:
            self.logger.error(f"Error getting price for pool {pool_key}: {e}")
            return None


    def _is_price_reasonable(self, price: Decimal, token0_symbol: str, token1_symbol: str) -> bool:
        """Basic sanity check for price reasonableness (e.g., not near zero or excessively large)."""
        # TODO: Implement more robust price sanity checks, potentially using external oracles or historical data.
        lower_bound = Decimal('1e-12') # Adjusted lower bound
        upper_bound = Decimal('1e18') # Keep upper bound

        if price <= lower_bound:
            self.logger.warning(f"Price {price:.18f} for {token1_symbol}/{token0_symbol} seems unreasonably low (<= {lower_bound}).")
            return False
        if price >= upper_bound:
            self.logger.warning(f"Price {price:.18f} for {token1_symbol}/{token0_symbol} seems unreasonably high (>= {upper_bound}).")
            return False
        return True


    def _execute_arbitrage(self, token0_symbol, token1_symbol, fee, higher_price_dex, lower_price_dex, potential_profit_ratio):
        """Execute the arbitrage transaction via the ArbitrageExecutor contract."""
        # Note: potential_profit_ratio is the decimal form, e.g., 0.001 for 0.1%
        self.logger.info(f"Attempting to execute arbitrage for {token0_symbol}/{token1_symbol} (Fee: {fee/10000}%)")
        self.logger.info(f"  Buy on {lower_price_dex.upper()}, Sell on {higher_price_dex.upper()}")
        self.logger.info(f"  Potential Profit Ratio: {potential_profit_ratio:.6f}")


        try:
            # --- Pre-checks ---
            # Check gas price
            current_gas_price_wei = self.w3.eth.gas_price
            current_gas_price_gwei = self.w3.from_wei(current_gas_price_wei, 'gwei')
            if current_gas_price_gwei > self.gas_price_gwei_limit:
                self.logger.warning(f"Current gas price ({current_gas_price_gwei:.2f} Gwei) exceeds limit ({self.gas_price_gwei_limit} Gwei). Skipping execution.")
                return

            # Check ETH balance for gas
            eth_balance = self.check_eth_balance()
            # Rough gas cost estimate (needs refinement based on actual execution)
            estimated_gas_limit = 600000 # Use a safer fixed limit initially
            estimated_gas_cost_eth = self.w3.from_wei(current_gas_price_wei * estimated_gas_limit, 'ether')
            if eth_balance < estimated_gas_cost_eth:
                 self.logger.warning(f"Insufficient ETH balance ({eth_balance:.6f} ETH) for estimated gas cost ({estimated_gas_cost_eth:.6f} ETH with limit {estimated_gas_limit}). Skipping execution.")
                 return

            # --- Prepare Transaction Data ---
            # Get token addresses
            token0_addr = self.token_addresses.get(token0_symbol)
            token1_addr = self.token_addresses.get(token1_symbol)
            if not token0_addr or not token1_addr:
                self.logger.error(f"❌ Could not find addresses for {token0_symbol} or {token1_symbol} during execution.")
                return

            # Get pool addresses
            uniswap_pool_key = (token0_symbol, token1_symbol, fee, 'uniswap')
            sushiswap_pool_key = (token0_symbol, token1_symbol, fee, 'sushiswap')
            uni_pool_addr = self.pools.get(uniswap_pool_key).address if uniswap_pool_key in self.pools else None
            sushi_pool_addr = self.pools.get(sushiswap_pool_key).address if sushiswap_pool_key in self.pools else None

            if not uni_pool_addr or not sushi_pool_addr:
                 self.logger.error(f"❌ Missing pool address for Uniswap or Sushiswap for pair {token0_symbol}/{token1_symbol} fee {fee} during execution.")
                 return

            # Determine buy/sell pools based on which dex has the higher price
            sell_pool_addr = uni_pool_addr if higher_price_dex == 'uniswap' else sushi_pool_addr
            buy_pool_addr = sushi_pool_addr if higher_price_dex == 'uniswap' else uni_pool_addr

            # Determine amountIn based on stablecoin and borrow amount
            stable_token_addr = self.token_addresses.get(self.stable_token)
            stable_token_info = self.address_to_token_info.get(stable_token_addr)

            if not stable_token_info:
                self.logger.error(f"❌ Stablecoin {self.stable_token} configuration error during execution.")
                return
            stable_token_decimals = stable_token_info['decimals']

            # Convert borrow amount to wei (integer)
            amount_in_wei = int(self.borrow_amount * (10**stable_token_decimals))

            # Determine tokenIn and tokenOut based on which token is the stablecoin
            # We always borrow the stablecoin via flash loan inside the contract
            token_in_addr = stable_token_addr
            if token0_addr == stable_token_addr:
                token_out_addr = token1_addr
            elif token1_addr == stable_token_addr:
                token_out_addr = token0_addr
            else:
                # This case should ideally be prevented by pair selection logic
                self.logger.error(f"❌ Execution Error: Neither {token0_symbol} nor {token1_symbol} is the configured stablecoin {self.stable_token}")
                return

            # Ensure all addresses are checksummed before use
            buy_pool_addr_cs = Web3.to_checksum_address(buy_pool_addr)
            sell_pool_addr_cs = Web3.to_checksum_address(sell_pool_addr)
            token_in_addr_cs = Web3.to_checksum_address(token_in_addr)
            token_out_addr_cs = Web3.to_checksum_address(token_out_addr)


            self.logger.info(f"Preparing arbitrage transaction:")
            self.logger.info(f"  Borrowing {self.borrow_amount} {self.stable_token} ({token_in_addr_cs})")
            self.logger.info(f"  Target Token: {token_out_addr_cs} ({token0_symbol if token_out_addr == token0_addr else token1_symbol})")
            self.logger.info(f"  Buy Pool ({lower_price_dex.upper()}): {buy_pool_addr_cs}")
            self.logger.info(f"  Sell Pool ({higher_price_dex.upper()}): {sell_pool_addr_cs}")
            self.logger.info(f"  Amount In (wei): {amount_in_wei}")
            self.logger.info(f"  Fee Tier: {fee}")


            # --- Build and Send Transaction ---
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            # Estimate gas using the contract function
            try:
                 # Correct argument order: poolBuy, poolSell, amountIn, tokenIn, fee
                 gas_estimate = self.executor_contract.functions.executeArbitrage(
                     buy_pool_addr_cs,   # address poolBuy
                     sell_pool_addr_cs,  # address poolSell
                     amount_in_wei,      # uint256 amountIn
                     token_in_addr_cs,   # address tokenIn (borrowed stablecoin)
                     fee                 # uint256 fee
                 ).estimate_gas({'from': self.account.address, 'value': 0}) # Ensure value is 0
                 self.logger.info(f"Gas estimate: {gas_estimate}")
                 gas_limit = int(gas_estimate * 1.3) # Add 30% buffer
                 self.logger.info(f"Using gas limit: {gas_limit}")
            except Exception as gas_err:
                 # Log specific details if possible (e.g., revert reason in error message)
                 self.logger.error(f"❌ Gas estimation failed: {gas_err}. Using fixed limit.")
                 # Check if error message contains revert reason hint
                 if "revert" in str(gas_err).lower():
                      self.logger.error("   Potential revert detected during gas estimation. Transaction might fail.")
                 gas_limit = 600000 # Fallback gas limit

            # Build the transaction dictionary
            tx_params = {
                'chainId': self.w3.eth.chain_id,
                'from': self.account.address,
                'to': self.executor_contract.address, # Ensure 'to' is the executor contract
                'nonce': nonce,
                'gas': gas_limit,
                # Add EIP-1559 fields if supported by network/web3.py version
                # 'maxFeePerGas': ...,
                # 'maxPriorityFeePerGas': ...,
                'gasPrice': current_gas_price_wei, # Use gasPrice for legacy txns or if EIP-1559 fails
                'value': 0 # Typically 0 unless sending ETH
            }

            # Encode function data
            encoded_data = self.executor_contract.encodeABI(
                 fn_name='executeArbitrage',
                 args=[
                     buy_pool_addr_cs,   # address poolBuy
                     sell_pool_addr_cs,  # address poolSell
                     amount_in_wei,      # uint256 amountIn
                     token_in_addr_cs,   # address tokenIn (borrowed stablecoin)
                     fee                 # uint256 fee
                 ]
            )
            tx_params['data'] = encoded_data


            # Sign transaction
            self.logger.info("Signing transaction...")
            signed_tx = self.w3.eth.account.sign_transaction(tx_params, self.private_key)

            # Send transaction (already checked simulate/monitor flags before calling this func)
            self.logger.info("🚀 Sending arbitrage transaction...")
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_hex = tx_hash.hex()
            self.logger.info(f"Transaction sent: {tx_hash_hex}")
            explorer_url = current_network.get('base', {}).get('explorer', 'https://basescan.org') # Safer access
            self.logger.info(f"Explorer link: {explorer_url}/tx/{tx_hash_hex}")


            # Optional: Wait for receipt and log result
            self.logger.info("Waiting for transaction receipt...")
            try:
                # Increased timeout for potentially slower networks/confirmations
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
                gas_used = receipt.get('gasUsed', 'N/A')
                effective_gas_price_wei = receipt.get('effectiveGasPrice', 0) # Default to 0 if missing
                gas_cost_eth = self.w3.from_wei(gas_used * effective_gas_price_wei, 'ether') if gas_used != 'N/A' else Decimal(0)

                if receipt.status == 1:
                    self.logger.info(colored(f"✅ Arbitrage successful! Tx: {tx_hash_hex}", 'green', attrs=['bold']))
                    self.logger.info(f"   Gas Used: {gas_used}")
                    self.logger.info(f"   Gas Cost: {gas_cost_eth:.8f} ETH")
                    # TODO: Decode logs from receipt (e.g., Profit event) to find actual profit earned
                    # Example: profit_event = self.executor_contract.events.Profit().process_receipt(receipt)
                    # if profit_event: actual_profit = profit_event[0]['args']['profitAmount'] ...
                else:
                    self.logger.error(colored(f"❌ Arbitrage failed! Tx: {tx_hash_hex}", 'red', attrs=['bold']))
                    self.logger.error(f"   Receipt Status: {receipt.status}")
                    self.logger.error(f"   Gas Used: {gas_used}")
                    self.logger.error(f"   Gas Cost: {gas_cost_eth:.8f} ETH")
                    # Try to get revert reason (may require specific node support/error formatting)
                    # This part is experimental and might not always work
                    try:
                         tx_info = self.w3.eth.get_transaction(tx_hash)
                         # Attempt to decode revert reason (requires web3.py v6+)
                         # result = self.w3.eth.call({'to': tx_params['to'], 'from': tx_params['from'], 'data': tx_params['data']}, receipt.blockNumber - 1)
                         # revert_reason = self.w3.codec.decode_revert_reason(result)
                         # self.logger.error(f"   Revert Reason (attempt): {revert_reason}")
                         self.logger.debug(f"   Failed Tx Info: {tx_info}") # Log tx info for manual check
                    except Exception as revert_err:
                         self.logger.error(f"   Could not decode revert reason: {revert_err}")
                    self.logger.debug(f"   Full Receipt: {receipt}") # Log full receipt for debugging

            except asyncio.TimeoutError: # web3.py uses TimeoutError from concurrent.futures
                 self.logger.warning(f"⏳ Transaction receipt timeout for {tx_hash_hex}. Check explorer manually.")
            except Exception as receipt_error:
                self.logger.error(f"❌ Error waiting for or processing receipt for tx {tx_hash_hex}: {receipt_error}")
                self.logger.error(traceback.format_exc())


        except Exception as e:
            self.logger.error(f"❌ Unexpected error during arbitrage execution: {str(e)}")
            self.logger.error(traceback.format_exc())


# ========================================
#      Main Execution Block
# ========================================
if __name__ == "__main__":
    print("--- Script execution started ---")
    monitor_instance = None # To hold the monitor instance for potential cleanup
    try:
        args = parse_arguments()
        print(f"--- Arguments parsed: {args} ---", flush=True)

        # Initialize the monitor (this handles logging, config, web3, contracts, etc.)
        monitor_instance = ArbitrageMonitor(
            simulate=args.simulate,
            monitor_only=args.monitor_only
        )

        # Start the main monitoring logic (run() keeps main thread alive, background loop handles polling)
        monitor_instance.run() # This will block until shutdown or error

    except ConnectionError as ce:
        print(f"❌ Initialization Failed: Connection Error - {ce}")
        sys.exit(1)
    except FileNotFoundError as fnf:
         print(f"❌ Initialization Failed: File Not Found - {fnf}")
         sys.exit(1)
    except ValueError as ve:
         # Catches config errors like invalid borrow amount, missing env vars raised in __init__
         print(f"❌ Initialization Failed: Configuration Error - {ve}")
         sys.exit(1)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received during initialization or run. Shutting down...")
        # shutdown() is called within run()'s finally block, so might not be needed here
        # if monitor_instance:
        #      monitor_instance.shutdown() # Attempt graceful shutdown if needed
        sys.exit(0) # Exit code 0 for graceful shutdown
    except RuntimeError as rte:
         # Catch specific runtime errors like ABI loading failure
         print(f"❌ Initialization Failed: Runtime Error - {rte}")
         sys.exit(1)
    except Exception as e:
        # Catch-all for any other unexpected errors during setup or run
        print(f"❌ An unexpected critical error occurred: {str(e)}")
        print(traceback.format_exc())
        # shutdown() is called within run()'s finally block
        sys.exit(1) # Exit code 1 for error

    # If run() finishes without error (e.g., if it's designed to complete)
    print("--- Script execution finished normally ---")
    # shutdown() is called within run()'s finally block
    sys.exit(0)
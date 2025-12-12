import requests
import json
import time
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class Token:
    address: str
    symbol: str
    name: str
    volume_24h: float
    price_change_24h: float
    liquidity: float
    tx_count_24h: int

class BaseScanScraper:
    def __init__(self):
        self.base_url = "https://api.basescan.org/api"
        self.dexscreener_url = "https://api.dexscreener.com/latest/dex"
        self.session = requests.Session()
        
        # Common headers to avoid rate limiting
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        })
    
    def get_trending_tokens_dexscreener(self, limit: int = 50) -> List[Token]:
        """
        Get trending tokens from DexScreener API for Base chain
        """
        try:
            # Get pairs for Base chain (chain id: base)
            url = f"{self.dexscreener_url}/pairs/base"
            response = self.session.get(url, timeout=10)
            
            if response.status_code != 200:
                print(f"DexScreener API error: {response.status_code}")
                return []
            
            data = response.json()
            tokens = []
            
            if 'pairs' not in data:
                return []
            
            # Filter and sort by volume
            pairs = data['pairs'][:limit]
            
            for pair in pairs:
                try:
                    # Skip if essential data is missing
                    if not pair.get('baseToken') or not pair.get('volume'):
                        continue
                    
                    base_token = pair['baseToken']
                    
                    # Create token object
                    token = Token(
                        address=base_token.get('address', ''),
                        symbol=base_token.get('symbol', ''),
                        name=base_token.get('name', ''),
                        volume_24h=float(pair.get('volume', {}).get('h24', 0)),
                        price_change_24h=float(pair.get('priceChange', {}).get('h24', 0)),
                        liquidity=float(pair.get('liquidity', {}).get('usd', 0)),
                        tx_count_24h=int(pair.get('txns', {}).get('h24', {}).get('buys', 0) + 
                                       pair.get('txns', {}).get('h24', {}).get('sells', 0))
                    )
                    
                    # Filter out tokens with very low volume or liquidity
                    if token.volume_24h > 1000 and token.liquidity > 5000:
                        tokens.append(token)
                        
                except (ValueError, TypeError, KeyError) as e:
                    continue
            
            # Sort by volume descending
            tokens.sort(key=lambda x: x.volume_24h, reverse=True)
            return tokens[:limit]
            
        except Exception as e:
            print(f"Error fetching trending tokens: {e}")
            return []
    
    def get_token_details_basescan(self, token_address: str) -> Optional[Dict]:
        """
        Get detailed token information from BaseScan API
        """
        try:
            # Get token info
            params = {
                'module': 'token',
                'action': 'tokeninfo',
                'contractaddress': token_address
            }
            
            response = self.session.get(self.base_url, params=params, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            
        except Exception as e:
            print(f"Error fetching token details for {token_address}: {e}")
        
        return None
    
    def filter_arbitrage_suitable_tokens(self, tokens: List[Token]) -> List[Token]:
        """
        Filter tokens that are suitable for arbitrage based on criteria:
        - High volume (>$10k daily)
        - Good liquidity (>$50k)
        - Reasonable transaction count
        - Not too volatile (price change < 50%)
        """
        suitable_tokens = []
        
        for token in tokens:
            # Define criteria for arbitrage suitability
            if (token.volume_24h >= 10000 and 
                token.liquidity >= 50000 and 
                token.tx_count_24h >= 100 and 
                abs(token.price_change_24h) <= 50):
                
                suitable_tokens.append(token)
        
        return suitable_tokens
    
    def display_tokens(self, tokens: List[Token]):
        """
        Display tokens in a formatted table
        """
        if not tokens:
            print("No tokens found!")
            return
        
        print("\n" + "="*120)
        print(f"{'#':<3} {'Symbol':<10} {'Name':<25} {'Address':<45} {'Volume 24h':<15} {'Change %':<10} {'Liquidity':<15}")
        print("="*120)
        
        for i, token in enumerate(tokens, 1):
            print(f"{i:<3} {token.symbol:<10} {token.name[:24]:<25} {token.address:<45} "
                  f"${token.volume_24h:,.0f}".ljust(15) + 
                  f"{token.price_change_24h:+.2f}%".ljust(10) + 
                  f"${token.liquidity:,.0f}")
    
    def get_user_token_selection(self, tokens: List[Token]) -> List[Token]:
        """
        Allow user to select tokens for arbitrage monitoring
        """
        selected_tokens = []
        
        while True:
            try:
                selection = input("\nEnter token numbers (comma-separated) or 'done' to finish: ").strip()
                
                if selection.lower() == 'done':
                    break
                
                indices = [int(x.strip()) - 1 for x in selection.split(',')]
                
                for idx in indices:
                    if 0 <= idx < len(tokens):
                        token = tokens[idx]
                        if token not in selected_tokens:
                            selected_tokens.append(token)
                            print(f"Added: {token.symbol} ({token.name})")
                    else:
                        print(f"Invalid selection: {idx + 1}")
                
            except ValueError:
                print("Invalid input. Please enter numbers separated by commas.")
            except KeyboardInterrupt:
                print("\nSelection cancelled.")
                break
        
        return selected_tokens

def main():
    scraper = BaseScanScraper()
    
    print("Fetching trending tokens from Base blockchain...")
    trending_tokens = scraper.get_trending_tokens_dexscreener(limit=30)
    
    if not trending_tokens:
        print("No trending tokens found!")
        return
    
    print(f"Found {len(trending_tokens)} trending tokens")
    
    # Filter for arbitrage-suitable tokens
    suitable_tokens = scraper.filter_arbitrage_suitable_tokens(trending_tokens)
    
    print(f"\nFiltered to {len(suitable_tokens)} tokens suitable for arbitrage:")
    scraper.display_tokens(suitable_tokens)
    
    # Allow user selection
    print("\nSelect tokens you want to monitor for arbitrage opportunities:")
    selected_tokens = scraper.get_user_token_selection(suitable_tokens)
    
    if selected_tokens:
        print(f"\nSelected {len(selected_tokens)} tokens for monitoring:")
        for token in selected_tokens:
            print(f"- {token.symbol} ({token.address})")
        
        # Save selection to file for the monitoring bot to use
        selection_data = {
            'timestamp': datetime.now().isoformat(),
            'tokens': [
                {
                    'address': token.address,
                    'symbol': token.symbol,
                    'name': token.name
                }
                for token in selected_tokens
            ]
        }
        
        with open('selected_tokens.json', 'w') as f:
            json.dump(selection_data, f, indent=2)
        
        print(f"\nToken selection saved to 'selected_tokens.json'")
    else:
        print("No tokens selected.")

if __name__ == "__main__":
    main()
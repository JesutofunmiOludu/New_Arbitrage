#!/usr/bin/env python3
"""
BaseScan Token Scraper
Scrapes trending tokens from BaseScan and provides token information
"""

import requests
import json
import time
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
import logging
from bs4 import BeautifulSoup
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class TokenData:
    address: str
    symbol: str
    name: str
    price_usd: Optional[float] = None
    market_cap: Optional[float] = None
    volume_24h: Optional[float] = None
    price_change_24h: Optional[float] = None
    holders: Optional[int] = None
    verified: bool = False

class BaseScanScraper:
    """Scraper for trending tokens on Base network"""
    
    def __init__(self):
        self.base_url = "https://basescan.org"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Base network well-known tokens
        self.well_known_tokens = {
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913": TokenData(
                "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USDC", "USD Coin", verified=True
            ),
            "0x4200000000000000000000000000000000000006": TokenData(
                "0x4200000000000000000000000000000000000006", "WETH", "Wrapped Ether", verified=True
            ),
            "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb": TokenData(
                "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", "DAI", "Dai Stablecoin", verified=True
            ),
            "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22": TokenData(
                "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", "cbETH", "Coinbase Wrapped Staked ETH", verified=True
            ),
            "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA": TokenData(
                "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", "USDbC", "USD Base Coin", verified=True
            )
        }
    
    def get_trending_tokens(self, limit: int = 20) -> List[TokenData]:
        """Get trending tokens from BaseScan (scrapes token links & titles)."""
        try:
            tokens: List[TokenData] = []
            seen = set()

            # Keep a few well-known tokens as seeds
            seeds = list(self.well_known_tokens.values())[:3]
            for t in seeds:
                tokens.append(t)
                seen.add(t.address.lower())

            # Collect candidate token links from pages likely to contain trending/new tokens
            candidates = self._collect_token_links(limit * 3)
            for addr, name, symbol in candidates:
                if addr.lower() in seen:
                    continue
                tokens.append(TokenData(address=addr, symbol=symbol or "UNKNOWN", name=name or symbol or "UNKNOWN"))
                seen.add(addr.lower())
                if len(tokens) >= limit:
                    break

            logger.info(f"Retrieved {len(tokens)} trending tokens")
            return tokens[:limit]

        except Exception as e:
            logger.error(f"Error getting trending tokens: {e}")
            return list(self.well_known_tokens.values())[:limit]
    
    def _scrape_token_tracker(self) -> List[TokenData]:
        """Scrape tokens from BaseScan token tracker"""
        tokens = []
        
        try:
            url = f"{self.base_url}/tokens"
            response = self.session.get(url, timeout=10)
            
            if response.status_code != 200:
                logger.warning(f"Failed to fetch token tracker: {response.status_code}")
                return tokens
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for token rows in the table
            token_rows = soup.find_all('tr', class_=['odd', 'even'])
            
            for row in token_rows[:10]:  # Limit to 10 additional tokens
                try:
                    token = self._parse_token_row(row)
                    if token and token.address not in self.well_known_tokens:
                        tokens.append(token)
                except Exception as e:
                    logger.debug(f"Error parsing token row: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"Error scraping token tracker: {e}")
        
        return tokens
    
    def _parse_token_row(self, row) -> Optional[TokenData]:
        """Parse a token row from the HTML table"""
        try:
            # This is a simplified parser - BaseScan's actual structure may vary
            cells = row.find_all('td')
            if len(cells) < 3:
                return None
            
            # Extract token link and address
            token_link = cells[1].find('a')
            if not token_link:
                return None
            
            href = token_link.get('href', '')
            address_match = re.search(r'/token/0x([a-fA-F0-9]{40})', href)
            if not address_match:
                return None
            
            address = f"0x{address_match.group(1)}"
            
            # Extract symbol and name
            token_text = token_link.get_text(strip=True)
            symbol_match = re.search(r'\(([^)]+)\)', token_text)
            symbol = symbol_match.group(1) if symbol_match else "UNKNOWN"
            name = token_text.replace(f"({symbol})", "").strip()
            
            return TokenData(
                address=address,
                symbol=symbol,
                name=name,
                verified=False  # Would need additional checks
            )
            
        except Exception as e:
            logger.debug(f"Error parsing token row: {e}")
            return None
    
    def get_token_details(self, address: str) -> Optional[TokenData]:
        """Get detailed information for a specific token"""
        try:
            if address in self.well_known_tokens:
                return self.well_known_tokens[address]
            
            # Try to fetch from BaseScan
            url = f"{self.base_url}/token/{address}"
            response = self.session.get(url, timeout=10)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract token information
            token_name = self._extract_token_name(soup)
            token_symbol = self._extract_token_symbol(soup)
            
            if not token_name or not token_symbol:
                return None
            
            return TokenData(
                address=address,
                symbol=token_symbol,
                name=token_name,
                verified=self._is_token_verified(soup)
            )
            
        except Exception as e:
            logger.error(f"Error getting token details for {address}: {e}")
            return None
    
    def _extract_token_name(self, soup) -> Optional[str]:
        """Extract token name from BaseScan page"""
        try:
            # Look for token name in page title or header
            title = soup.find('title')
            if title:
                title_text = title.get_text()
                # Extract name from title like "TokenName (SYMBOL) Token Tracker | BaseScan"
                match = re.search(r'^(.+?)\s+\([^)]+\)\s+Token', title_text)
                if match:
                    return match.group(1).strip()
            
            return None
        except:
            return None
    
    def _extract_token_symbol(self, soup) -> Optional[str]:
        """Extract token symbol from BaseScan page"""
        try:
            title = soup.find('title')
            if title:
                title_text = title.get_text()
                # Extract symbol from title
                match = re.search(r'\(([^)]+)\)\s+Token', title_text)
                if match:
                    return match.group(1).strip()
            
            return None
        except:
            return None
    
    def _is_token_verified(self, soup) -> bool:
        """Check if token is verified on BaseScan"""
        try:
            # Look for verification indicators
            verified_indicators = soup.find_all(['i', 'span'], class_=re.compile(r'.*verif.*|.*check.*', re.I))
            return len(verified_indicators) > 0
        except:
            return False
    
    def display_tokens(self, tokens: List[TokenData]):
        """Display tokens in a formatted table"""
        print("\n" + "="*80)
        print("🔥 TRENDING TOKENS ON BASE NETWORK")
        print("="*80)
        
        print(f"{'#':<3} {'Symbol':<10} {'Name':<25} {'Address':<42} {'Verified'}")
        print("-" * 80)
        
        for i, token in enumerate(tokens, 1):
            verified_mark = "✅" if token.verified else "❌"
            print(f"{i:<3} {token.symbol:<10} {token.name[:24]:<25} {token.address:<42} {verified_mark}")
        
        print("=" * 80)
    
    def save_tokens_to_file(self, tokens: List[TokenData], filename: str = "base_tokens.json"):
        """Save tokens to JSON file"""
        try:
            data = [asdict(token) for token in tokens]
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Tokens saved to {filename}")
        except Exception as e:
            logger.error(f"Error saving tokens: {e}")
    
    def load_tokens_from_file(self, filename: str = "base_tokens.json") -> List[TokenData]:
        """Load tokens from JSON file"""
        try:
            with open(filename, 'r') as f:
                data = json.load(f)
            tokens = [TokenData(**item) for item in data]
            logger.info(f"Loaded {len(tokens)} tokens from {filename}")
            return tokens
        except Exception as e:
            logger.error(f"Error loading tokens: {e}")
            return []

    def _collect_token_links(self, max_links: int = 50) -> List[tuple]:
        """
        Visit a few token listing pages and collect (address, name, symbol) tuples.
        This is robust to simple HTML pages; if BaseScan requires JS this will
        still fail and a headless browser or API will be needed.
        """
        results = []
        pages = [
            f"{self.base_url}/tokens",
            f"{self.base_url}/tokens?sort=trending",
            f"{self.base_url}/tokens?sort=new"
        ]

        for url in pages:
            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code != 200:
                    logger.debug(f"Skipped {url}, status {resp.status_code}")
                    continue

                soup = BeautifulSoup(resp.content, "html.parser")

                # Find anchors that link to token pages
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    m = re.search(r"/token/(0x[a-fA-F0-9]{40})", href)
                    if not m:
                        continue
                    addr = m.group(1)
                    text = a.get_text(" ", strip=True)

                    # Try to extract symbol and name from link text like "Token Name (SYM)"
                    sym = None
                    name = None
                    sym_m = re.search(r"\(([^)]+)\)", text)
                    if sym_m:
                        sym = sym_m.group(1).strip()
                        name = text.replace(f"({sym})", "").strip()
                    else:
                        # sometimes the link is just the symbol or the name
                        parts = text.split()
                        if len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9_]{1,12}", parts[0]):
                            sym = parts[0]
                            name = None
                        else:
                            name = text

                    results.append((addr, name, sym))

            except Exception as e:
                logger.debug(f"Error collecting links from {url}: {e}")
                continue

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for addr, name, sym in results:
            if addr.lower() in seen:
                continue
            seen.add(addr.lower())
            unique.append((addr, name, sym))
            if len(unique) >= max_links:
                break

        return unique

def main():
    """Main function for testing the scraper"""
    scraper = BaseScanScraper()
    
    print("🤖 BaseScan Token Scraper")
    print("Fetching trending tokens...")
    
    # Get trending tokens
    tokens = scraper.get_trending_tokens(limit=15)
    
    # Display tokens
    scraper.display_tokens(tokens)
    
    # Save to file
    scraper.save_tokens_to_file(tokens)
    
    # Interactive selection
    print("\nSelect tokens for arbitrage monitoring:")
    selected_numbers = input("Enter token numbers (comma-separated): ").strip()
    
    if selected_numbers:
        try:
            indices = [int(x.strip()) - 1 for x in selected_numbers.split(",")]
            selected_tokens = [tokens[i] for i in indices if 0 <= i < len(tokens)]
            
            print(f"\n✅ Selected {len(selected_tokens)} tokens:")
            for token in selected_tokens:
                print(f"  • {token.symbol} ({token.name}) - {token.address}")
                
            return selected_tokens
        except (ValueError, IndexError):
            print("❌ Invalid selection")
    
    return []

if __name__ == "__main__":
    main()

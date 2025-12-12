import csv
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import re
import time
import os
from typing import List, Dict


BASE_URL = "https://basescan.org"


def create_headless_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    return driver


def scrape_basescan_upward_tokens(max_pages: int = 3, limit: int = 50, wait_seconds: int = 8):
    """
    Scrape BaseScan token listing pages and return a list of dicts:
    [{ "address": str, "name": str, "symbol": str, "trend": "+"|"-" }, ...]
    Falls back to bot/base_tokens.json or a small seed list if scraping fails.
    """
    from bs4 import BeautifulSoup
    import re, time, os, json
    records = []
    # Try Selenium scraping if the file has the driver helpers
    try:
        driver = create_headless_driver()
        pages = [f"https://basescan.org/tokens", f"https://basescan.org/tokens?sort=trending", f"https://basescan.org/tokens?sort=new"]
        found = []
        for url in pages[:max_pages]:
            try:
                driver.get(url)
                try:
                    WebDriverWait(driver, wait_seconds).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/token/0x']"))
                    )
                except Exception:
                    pass
                soup = BeautifulSoup(driver.page_source, "html.parser")
                for a in soup.select("a[href*='/token/0x']"):
                    href = a.get("href", "")
                    m = re.search(r"/token/(0x[a-fA-F0-9]{40})", href)
                    if not m:
                        continue
                    addr = m.group(1)
                    text = a.get_text(" ", strip=True)
                    sym = None; name = ""
                    sym_m = re.search(r"\(([^)]+)\)", text)
                    if sym_m:
                        sym = sym_m.group(1).strip()
                        name = text.replace(f"({sym})", "").strip()
                    else:
                        parts = text.split()
                        if len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9_.-]{1,12}", parts[0]):
                            sym = parts[0]
                        else:
                            name = text
                    found.append((addr, name, sym))
                    if len(found) >= limit:
                        break
                if len(found) >= limit:
                    break
                time.sleep(0.3)
            except Exception:
                continue
    except Exception:
        found = []
    finally:
        try:
            if 'driver' in locals() and driver:
                driver.quit()
        except Exception:
            pass

    # Fallback to base_tokens.json if no results
    if not found:
        fallback = os.path.join(os.path.dirname(__file__), "base_tokens.json")
        try:
            with open(fallback, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    addr = item.get("contract_address") or item.get("contractAddress") or item.get("address")
                    if not addr:
                        continue
                    name = item.get("name") or item.get("token") or ""
                    sym = item.get("symbol") or ""
                    found.append((addr, name, sym))
                    if len(found) >= limit:
                        break
        except Exception:
            # final seed fallback
            seed = [
                ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "Wrapped Ether", "WETH"),
                ("0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USD Coin", "USDC"),
                ("0x6B175474E89094C44Da98b954EedeAC495271d0F", "DAI", "DAI")
            ]
            for s in seed:
                found.append(s)
                if len(found) >= limit:
                    break

    # Dedupe and build records
    seen = set()
    for i, (addr, name, sym) in enumerate(found):
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        records.append({"address": addr, "name": name or "", "symbol": sym or "UNKNOWN", "trend": "+"})
        if len(records) >= limit:
            break

    return records


if __name__ == "__main__":
    toks = scrape_basescan_upward_tokens(max_pages=2, limit=20)
    for i, t in enumerate(toks, 1):
        print(f"{i:2d}. {t['symbol']:<8} | {t['name'][:30]:30} | {t['address']} | {t['trend']}")
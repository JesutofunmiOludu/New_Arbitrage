import csv
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


#pip install selenium webdriver-manager
def scrape_basescan_upward_tokens(max_pages=9, output_file="tokens.csv"):
    base_url = "https://basescan.org/tokens?p="

    # Configure Chrome options
    options = Options()
    options.add_argument("--headless")  # Run in background
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920x1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # Initialize WebDriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    # Prepare output containers
    tokens = []
    output_json = "base_tokens.json"

    # Open CSV file for writing
    with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(["Token Name", "Symbol", "Address", "Change %"])  # CSV header

        try:
            for page in range(1, max_pages + 1):
                url = f"{base_url}{page}"
                print(f"\n📄 Scraping page {page}: {url}")
                driver.get(url)

                # Wait for table to load
                wait = WebDriverWait(driver, 10)
                rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table tbody tr")))

                if not rows:
                    print("No more data. Stopping pagination.")
                    break

                for row in rows:
                    try:
                        name_elem = row.find_element(By.CSS_SELECTOR, "td:nth-child(2) a")
                        name = name_elem.text.strip()
                        # Extract symbol from the token name (assuming it's in parentheses at the end)
                        symbol = name.split()[-1].removeprefix("(").removesuffix(")")
                        address = name_elem.get_attribute("href").split("/")[-1]

                        change_elem = row.find_element(By.CSS_SELECTOR, "td:nth-child(4)")
                        change_text = change_elem.text.strip()

                        # Check if the change percentage has a "caret up" (▲) symbol
                        if not "-" in change_text:
                            print(f"Token: {name}, Symbol: {symbol}, Address: {address}, Change: {change_text}")
                            csv_writer.writerow([name, symbol, address, change_text])  # Write to CSV with symbol
                            tokens.append({
                                "name": name,
                                "symbol": symbol,
                                "address": address,
                                "change_percent": change_text
                            })

                    except Exception as e:
                        print("Skipping a row due to an error:", e)

        except Exception as e:
            print("Error:", e)

        finally:
            driver.quit()
            # Write JSON file with collected tokens
            try:
                with open(output_json, "w", encoding="utf-8") as jf:
                    json.dump(tokens, jf, ensure_ascii=False, indent=2)
                print(f"\n✅ Data saved to {output_file} and {output_json}")
            except Exception as je:
                print("Failed writing JSON:", je)

# Run the scraper for multiple pages
scrape_basescan_upward_tokens(max_pages=9, output_file="tokens.csv")  # Change max_pages and file name as needed
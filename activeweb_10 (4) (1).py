import time
from datetime import datetime, time as dt_time
import webull.webull as webull
from tabulate import tabulate
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException, UnexpectedAlertPresentException
import requests
import math
import pandas as pd
import re

USERNAME = "cora1008"
PASSWORD = "Omerta19!!"
BOT_TOKEN = '7488863044:AAEBcSLMjo1TxomFxWl3o6OTVqOKszGKdTk'
TELEGRAM_CHAT_ID = '1452322224'

class DilutionDataScraper:
    def __init__(self):
        self.months_remaining_xpath = "/html/body/div[1]/div/div/div/div[2]/p[2]/strong[1]"
        self.months_remaining_css = "#dashContentWrapper > p.cursor-default.mb-3.ml-2.ml-sm-3.opacity-7 > strong:nth-child(1)"
        self.cashflow_xpath = "/html/body/div[1]/div/div/div/div[2]/p[2]/text()[1]"
    
    def extract_number_from_text(self, text):
        """Extract numeric value from text like '9.0 months' or '$4.02M'"""
        if 'months' in text.lower():
            # Extract number before 'months'
            match = re.search(r'(\d+\.?\d*)\s*months', text)
            if match:
                return float(match.group(1))
        
        # If no 'months' found, try to extract any number
        match = re.search(r'(\d+\.?\d*)', text)
        if match:
            return float(match.group(1))
        
        return None
    
    def fetch_data_for_symbol(self, symbol):
        """
        Fetch dilution data for a specific symbol
        """
        try:
            # Setup Chrome options for headless browsing
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            
            driver = webdriver.Chrome(options=chrome_options)
            url = f"https://dilutiontracker.com/app/search/{symbol}"
            driver.get(url)
            
            # Wait for the page to load
            wait = WebDriverWait(driver, 10)
            
            # Try to find the months remaining element
            months_text = None
            try:
                months_element = wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, self.months_remaining_css))
                )
                months_text = months_element.text.strip()
            except:
                # Fallback to XPath if CSS selector doesn't work
                try:
                    months_element = wait.until(
                        EC.presence_of_element_located((By.XPATH, self.months_remaining_xpath))
                    )
                    months_text = months_element.text.strip()
                except:
                    months_text = None
            
            # Extract numeric value
            months_remaining = None
            if months_text:
                months_remaining = self.extract_number_from_text(months_text)
            
            # Check for cashflow positive status
            is_cashflow_positive = False
            try:
                parent_element = driver.find_element(By.XPATH, "/html/body/div[1]/div/div/div/div[2]/p[2]")
                full_text = parent_element.text
                is_cashflow_positive = 'cashflow positive' in full_text.lower()
            except:
                pass
            
            driver.quit()
            
            return {
                'months_remaining': months_remaining,
                'is_cashflow_positive': is_cashflow_positive,
                'raw_text': months_text
            }
            
        except Exception as e:
            print(f"Error fetching dilution data for {symbol}: {e}")
            if 'driver' in locals():
                driver.quit()
            return None

class StockTracker:
    def __init__(self):
        self.wb = webull()
        self.last_printed = {}
        self.current_session = None
        self.positions = {}
        self.buy_back_positions = {}
        self.driver = None
        self.locate_routes = ["LOCATE4", "LOCATE5", "LOCATE8", "LOCATE10", "LOCATE9", 
                            "LOCATE11", "LOCATE12", "LOCATE2", "LOCATE3"]
        self.failed_symbols = {}
        self.current_equity = None
        self.avg_volume_cache = {}
        self.avg_volume_time_cache = {}
        self.vwap_cache = {}
        self.months_data_cache = {}  # New cache for months data
        self.dilution_scraper = DilutionDataScraper()  # Initialize scraper
        self.skipped_symbols_notified = set()  # Track symbols we've already notified about
        self.float_shares_cache = {}  # New cache for actual float shares

    def get_months_data(self, symbol):
        """Get months remaining data for a symbol, cached to avoid repeated calls"""
        if symbol not in self.months_data_cache:
            try:
                data = self.dilution_scraper.fetch_data_for_symbol(symbol)
                if data:
                    # If cashflow positive, return "Positive +"
                    if data['is_cashflow_positive']:
                        self.months_data_cache[symbol] = "Positive +"
                    elif data['months_remaining'] is not None:
                        self.months_data_cache[symbol] = data['months_remaining']
                    else:
                        self.months_data_cache[symbol] = 'N/A'
                else:
                    self.months_data_cache[symbol] = 'N/A'
            except Exception as e:
                print(f"Error fetching months data for {symbol}: {e}")
                self.months_data_cache[symbol] = 'N/A'
        
        return self.months_data_cache[symbol]

    def get_avg_1min_volume(self, symbol, bar_count=5):
        """Get average 1-minute bar volume over specified number of bars"""
        cache_key = f"{symbol}_{bar_count}"
        current_time = datetime.now()
        
        # Check if we have cached data and if it's recent (less than 60 seconds old)
        if (cache_key in self.avg_volume_cache and 
            cache_key in self.avg_volume_time_cache):
            
            time_diff = (current_time - self.avg_volume_time_cache[cache_key]).total_seconds()
            if time_diff < 60:  # Use cached data if less than 60 seconds old
                return self.avg_volume_cache[cache_key]
        
        try:
            # Get 1-minute bars with extended trading for premarket
            bars = self.wb.get_bars(stock=symbol, interval='m1', count=bar_count, extendTrading=1)
            
            if bars is not None and not bars.empty and len(bars) >= bar_count:
                avg_volume = bars['volume'].mean()
                self.avg_volume_cache[cache_key] = avg_volume
                self.avg_volume_time_cache[cache_key] = current_time
                return avg_volume
            else:
                self.avg_volume_cache[cache_key] = 'N/A'
                self.avg_volume_time_cache[cache_key] = current_time
                return 'N/A'
                
        except Exception as e:
            print(f"Error fetching 1-min volume data for {symbol}: {e}")
            self.avg_volume_cache[cache_key] = 'N/A'
            self.avg_volume_time_cache[cache_key] = current_time
            return 'N/A'

    def get_vwap_data(self, symbol):
        """Get VWAP data for a symbol"""
        try:
            # Determine if we should use extended trading
            current_time = datetime.now().time()
            extend_trading = 0  # Default to regular hours
            
            # Set extendTrading=1 for pre-market and after-hours
            if (dt_time(4, 0) <= current_time < dt_time(9, 30) or  # Pre-market
                dt_time(16, 0) <= current_time < dt_time(20, 0)):  # After-hours
                extend_trading = 1
            
            # Get bars with appropriate extended trading parameter
            bars = self.wb.get_bars(stock=symbol, interval='m1', count=1, extendTrading=extend_trading)
            
            if bars is not None and not bars.empty:
                return {
                    'vwap': bars['vwap'].iloc[0],
                    'candle_open': bars['open'].iloc[0]
                }
            return None
        except Exception as e:
            print(f"Error fetching VWAP data for {symbol}: {e}")
            return None

    def calculate_dist_vwap(self, candle_open, vwap):
        """Calculate distance from VWAP as percentage"""
        try:
            return ((candle_open / vwap) - 1) * 100
        except (TypeError, ZeroDivisionError):
            return None

    def check_free_float_distvwap_conditions(self, symbol, change_percentage, dist_vwap, session):
        """
        Check free float based DistVWAP conditions along with existing session conditions
        """
        try:
            # Get average 1-minute volume
            avg_volume = self.get_avg_1min_volume(symbol)
            if avg_volume == 'N/A':
                return False, "Could not get volume data"
            
            # Get free float shares
            free_float_shares = self.get_float_shares(symbol)
            if free_float_shares == 'N/A':
                return False, "Could not get free float data"
            
            # Convert to millions for comparison
            avg_vol_thousands = float(avg_volume) / 1000
            try:
                free_float_millions = float(free_float_shares) / 1000000
            except (ValueError, TypeError):
                return False, "Invalid free float data"
            
            # Determine required DistVWAP based on free float
            if free_float_millions < 4:
                required_distvwap = 45
            elif free_float_millions <= 10:
                required_distvwap = 20
            else:
                required_distvwap = 10
            
            # Check DistVWAP condition
            distvwap_ok = isinstance(dist_vwap, float) and dist_vwap > required_distvwap
            
            if session == "premarket":
                # Premarket conditions: Change % > 200, 1 min volume bar < 1000k and DistVWAP based on free float
                volume_ok = avg_vol_thousands < 1000000
                change_ok = change_percentage > 100
                
                meets_conditions = volume_ok and change_ok and distvwap_ok
                
                reason = f"Premarket: Change={change_percentage:.1f}% (>150), Vol={avg_vol_thousands:.1f}k (<1000k), FF={free_float_millions:.1f}M, DistVWAP={dist_vwap:.1f}% (>{required_distvwap}%)"
                print(f"{symbol}: {reason} - Meets conditions: {meets_conditions}")
                
                return meets_conditions, reason
                
            elif session == "regular":
                # Intraday conditions: (Change % > 300 OR DistVWAP based on free float) AND 1 min volume bar < 500k
                volume_ok = avg_vol_thousands < 5000000
                change_condition = change_percentage > 200
                change_or_distvwap_ok = change_condition or distvwap_ok
                
                meets_conditions = volume_ok and change_or_distvwap_ok
                
                reason = f"Intraday: Vol={avg_vol_thousands:.1f}k (<400k), Change={change_percentage:.1f}% (>300) OR FF={free_float_millions:.1f}M, DistVWAP={dist_vwap:.1f}% (>{required_distvwap}%)"
                print(f"{symbol}: {reason} - Meets conditions: {meets_conditions}")
                
                return meets_conditions, reason
                
            else:
                # For afterhours, change > 150% and free float DistVWAP check
                change_ok = change_percentage > 100
                meets_conditions = change_ok and distvwap_ok
                
                reason = f"Afterhours: Change={change_percentage:.1f}% (>150), FF={free_float_millions:.1f}M, DistVWAP={dist_vwap:.1f}% (>{required_distvwap}%)"
                print(f"{symbol}: {reason} - Meets conditions: {meets_conditions}")
                
                return meets_conditions, reason
                
        except Exception as e:
            print(f"Error checking free float DistVWAP conditions for {symbol}: {e}")
            return False, f"Error: {e}"
        
    def get_float_shares(self, symbol):
        """Get actual float shares for a symbol, cached to avoid repeated API calls"""
        if not hasattr(self, 'float_shares_cache'):
            self.float_shares_cache = {}
        
        if symbol not in self.float_shares_cache:
            try:
                quote = self.wb.get_quote(symbol)
                # Try to get float shares - this might be a different field like 'floatShares' or 'shareFloat'
                float_shares = quote.get('outstandingShares') or quote.get('shareFloat') or quote.get('float')
                self.float_shares_cache[symbol] = float_shares if float_shares is not None else 'N/A'
            except Exception as e:
                print(f"Error fetching float shares for {symbol}: {e}")
                self.float_shares_cache[symbol] = 'N/A'
        
        return self.float_shares_cache[symbol]

    def get_current_equity(self):
        """Get current equity from account tab"""
        try:
            print("Getting current equity...")
            
            # Click Account tab
            if not self.wait_and_click(By.XPATH, "/html/body/div[3]/div/div/div[3]/form/div[3]/input[1]", timeout=10):
                print("Failed to click Account tab")
                return None
            
            # Wait for page to load
            time.sleep(2)
            
            # Get current equity value
            equity_element = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "/html/body/div[3]/div/div/div[3]/form/div[3]/div/div[1]/div/div/div/table/tbody/tr[3]/td[2]/span"))
            )
            
            equity_text = equity_element.text.strip()
            
            # Clean up the text and convert to float
            equity_text = equity_text.replace('$', '').replace(',', '')
            current_equity = float(equity_text)
            
            print(f"Current equity: ${current_equity:.2f}")
            self.current_equity = current_equity
            return current_equity
            
        except Exception as e:
            print(f"Error getting current equity: {e}")
            return None

    def calculate_tranche_quantities(self, current_price):
        """Calculate the 2 tranche quantities based on the given formula"""
        quantities = []
        
        # 1st order: $100/current_price or 1 if current_price > $100
        q1 = math.floor(100 / current_price) if current_price <= 1000 else 1
        quantities.append(max(1, q1))
        
        # 2nd order: $100/(current_price * 2) or 2 if (current_price * 2) > $100
        q2 = math.floor(100 / (current_price * 2)) if (current_price * 2) <= 1000 else 2
        quantities.append(max(1, q2))
        
        total_quantity = sum(quantities)
        print(f"Tranche quantities for ${current_price:.2f}: {quantities} (Total: {total_quantity})")
        
        return quantities, total_quantity

    def parse_position_data(self):
        """Parse position data from position block to get average price and PnL"""
        position_data = {}
        try:
            # Click on Position tab first
            if not self.wait_and_click(By.ID, "ui-id-22", timeout=10):
                print("Failed to click Position tab.")
                return position_data

            # Wait for position block to load
            time.sleep(2)
            
            # Try to find position block using class name
            try:
                position_block = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "positionblock"))
                )
            except:
                # Try XPath as fallback
                position_block = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "/html/body/div[3]/div/div/div[4]/div[1]/div[2]/div/table/tbody"))
                )
            
            rows = position_block.find_elements(By.TAG_NAME, "tr")
            
            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 4:
                        symbol = cells[0].text.strip()
                        quantity = int(cells[1].text.strip())
                        avg_price = float(cells[2].text.strip())
                        pnl = float(cells[3].text.strip())
                        
                        position_size = abs(avg_price * quantity)
                        pnl_percentage = (pnl / position_size) * 100 if position_size > 0 else 0
                        
                        position_data[symbol] = {
                            'quantity': quantity,
                            'avg_price': avg_price,
                            'pnl': pnl,
                            'position_size': position_size,
                            'pnl_percentage': pnl_percentage
                        }
                        
                        print(f"{symbol}: Qty={quantity}, AvgPrice=${avg_price:.4f}, PnL=${pnl:.2f}, PnL%={pnl_percentage:.1f}%")
                        
                except Exception as e:
                    print(f"Error parsing row: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error parsing position data: {e}")
            
        return position_data

    def print_gainers(self, gainers_data, session):
            if not gainers_data or not isinstance(gainers_data, dict):
                print("No data available")
                return

            data = gainers_data.get('data', [])
            if not data:
                print(f"No {session} gainer data available")
                return

            # Updated headers to replace News with Free Float
            headers = ["Symbol", "Current Price", "Change%", "Position Price", "DistVWAP%", "1min Vol", "Free Float", "Conditions"]
            rows = []
            short_candidates = []
            buy_back_candidates = []

            # Process current positions first
            for symbol, position_price in self.positions.items():
                current_price = self.get_current_price(symbol)
                
                # Get VWAP data for positions
                vwap_data = self.get_vwap_data(symbol)
                avg_volume = self.get_avg_1min_volume(symbol)
                
                # Get free float data
                free_float_shares = self.get_float_shares(symbol)
                free_float_display = f"{float(free_float_shares)/1000000:.1f}M" if isinstance(free_float_shares, (int, float)) else free_float_shares
                
                if vwap_data:
                    dist_vwap = self.calculate_dist_vwap(vwap_data['candle_open'], vwap_data['vwap'])
                else:
                    dist_vwap = 'N/A'
                
                if current_price is not None:
                    change_percentage = ((current_price - position_price) / position_price) * 100
                    # Check for buy-back trigger (current price > 3x position price)
                    if current_price > position_price * 10.00:
                        buy_back_candidates.append((symbol, current_price))
                else:
                    change_percentage = 'N/A'

                row = [
                    symbol,
                    f"{current_price:.2f}" if isinstance(current_price, float) else 'N/A',
                    f"{change_percentage:.2f}%" if isinstance(change_percentage, float) else change_percentage,
                    f"{position_price:.2f}",
                    f"{dist_vwap:.1f}%" if isinstance(dist_vwap, float) else dist_vwap,
                    f"{float(avg_volume)/1000:.1f}k" if isinstance(avg_volume, (int, float)) else avg_volume,
                    free_float_display,  # Free Float instead of News
                    'Position'  # Conditions
                ]
                rows.append(row)

            # Process gainers - only for stocks >75% change
            for item in data:
                ticker = item.get('ticker', {})
                values = item.get('values', {})

                symbol = ticker.get('symbol', 'N/A')
                if symbol in self.positions:
                    continue

                price = float(values.get('price', 0))
                change_percentage = float(values.get('changeRatio', 0)) * 100

                # Skip if change percentage <= 75%
                if change_percentage <= 100:
                    continue

                # Skip if already processed recently
                if not self.should_print(symbol, change_percentage):
                    continue

                current_price = self.get_current_price(symbol)
                
                # Get VWAP and volume data for analysis
                vwap_data = self.get_vwap_data(symbol)
                avg_volume = self.get_avg_1min_volume(symbol)
                
                # Get free float data
                free_float_shares = self.get_float_shares(symbol)
                free_float_display = f"{float(free_float_shares)/1000000:.1f}M" if isinstance(free_float_shares, (int, float)) else free_float_shares
                
                if vwap_data:
                    dist_vwap = self.calculate_dist_vwap(vwap_data['candle_open'], vwap_data['vwap'])
                else:
                    dist_vwap = 'N/A'

                # Check new trading conditions with free float DistVWAP requirements
                meets_conditions, condition_reason = self.check_free_float_distvwap_conditions(symbol, change_percentage, dist_vwap, session)
                
                # Get detailed condition breakdown for display
                detailed_conditions = self.get_detailed_condition_status(symbol, change_percentage, dist_vwap, session, avg_volume)

                row = [
                    symbol,
                    f"{current_price:.2f}" if current_price is not None else 'N/A',
                    f"{change_percentage:.2f}%",
                    'N/A',  # Position Price
                    f"{dist_vwap:.1f}%" if isinstance(dist_vwap, float) else dist_vwap,
                    f"{float(avg_volume)/1000:.1f}k" if isinstance(avg_volume, (int, float)) else avg_volume,
                    free_float_display,  # Free Float instead of News
                    detailed_conditions  # Detailed conditions instead of just ✓/✗
                ]
                rows.append(row)

                # Check if this is a short candidate using new conditions
                if (symbol not in self.positions and 
                    symbol not in self.buy_back_positions and
                    symbol not in self.failed_symbols and
                    meets_conditions):
                    short_candidates.append((symbol, price))

            if rows:
                print(f"\nTop {session.capitalize()} Gainers and Current Positions (>75% Change):")
                print(tabulate(rows, headers=headers, tablefmt="grid"))
            else:
                print(f"\nNo {session.capitalize()} gainers above 75% or current positions to display.")

            # Execute short orders for candidates
            if short_candidates:
                for symbol, price in short_candidates:
                    self.execute_short_order(symbol, price)

            # Execute buy-back orders for candidates (if current price > 3x position price)
            if buy_back_candidates:
                self.execute_buy_back_orders(buy_back_candidates)

    def get_detailed_condition_status(self, symbol, change_percentage, dist_vwap, session, avg_volume):
        """Get detailed breakdown of why conditions passed or failed"""
        try:
            # Get free float shares
            free_float_shares = self.get_float_shares(symbol)
            if free_float_shares == 'N/A':
                return "No float data"
            
            # Convert to millions for comparison
            avg_vol_thousands = float(avg_volume) / 1000 if isinstance(avg_volume, (int, float)) else 0
            try:
                free_float_millions = float(free_float_shares) / 1000000
            except (ValueError, TypeError):
                return "Invalid float data"
            
            # Determine required DistVWAP based on free float
            if free_float_millions < 4:
                required_distvwap = 45
            elif free_float_millions <= 10:
                required_distvwap = 25
            else:
                required_distvwap = 15
            
            # Check individual conditions
            distvwap_ok = isinstance(dist_vwap, float) and dist_vwap > required_distvwap
            
            # Get months data
            months_data = self.get_months_data(symbol)
            months_ok = True
            months_reason = ""
            
            if months_data == "Positive +":
                months_ok = False
                months_reason = "CashFlow+"
            elif isinstance(months_data, (int, float)) and months_data > 40:
                months_ok = False
                months_reason = f"Months>{months_data}"
            elif isinstance(months_data, str) and months_data != 'N/A':
                try:
                    months_val = float(months_data)
                    if months_val > 40:
                        months_ok = False
                        months_reason = f"Months>{months_val}"
                except:
                    pass
            
            # Check time restrictions
            current_time = datetime.now().time()
            time_ok = not (dt_time(8, 30) <= current_time < dt_time(11, 31))
            
            if session == "premarket":
                # Premarket conditions: Change % > 200, 1 min volume bar < 1000k and DistVWAP based on free float
                volume_ok = avg_vol_thousands < 10000000
                change_ok = change_percentage > 100
                
                # Build condition string
                conditions = []
                if not change_ok:
                    conditions.append(f"Change<150%")
                if not volume_ok:
                    conditions.append(f"Vol>{avg_vol_thousands:.0f}k")
                if not distvwap_ok:
                    conditions.append(f"DistVWAP<{required_distvwap}%")
                if not months_ok:
                    conditions.append(months_reason)
                if not time_ok:
                    conditions.append("8:30-8:31AM")
                    
                if not conditions:
                    return "✓ All conditions met"
                else:
                    return "✗ " + ", ".join(conditions)
                    
            elif session == "regular":
                # Intraday conditions: (Change % > 300 OR DistVWAP based on free float) AND 1 min volume bar < 400k
                volume_ok = avg_vol_thousands < 4000000
                change_condition = change_percentage > 200
                change_or_distvwap_ok = change_condition or distvwap_ok
                
                conditions = []
                if not volume_ok:
                    conditions.append(f"Vol>{avg_vol_thousands:.0f}k")
                if not change_or_distvwap_ok:
                    conditions.append(f"Change<300% & DistVWAP<{required_distvwap}%")
                if not months_ok:
                    conditions.append(months_reason)
                if not time_ok:
                    conditions.append("8:30-8:31AM")
                    
                if not conditions:
                    return "✓ All conditions met"
                else:
                    return "✗ " + ", ".join(conditions)
                    
            else:  # afterhours
                # For afterhours, change > 150% and free float DistVWAP check
                change_ok = change_percentage > 100
                
                conditions = []
                if not change_ok:
                    conditions.append(f"Change<150%")
                if not distvwap_ok:
                    conditions.append(f"DistVWAP<{required_distvwap}%")
                if not months_ok:
                    conditions.append(months_reason)
                if not time_ok:
                    conditions.append("8:30-8:31AM")
                    
                if not conditions:
                    return "✓ All conditions met"
                else:
                    return "✗ " + ", ".join(conditions)
                    
        except Exception as e:
            return f"Error: {str(e)[:20]}"

    def execute_short_order(self, symbol, price):
        """Execute short order in tranches"""
        # Check if current time is between 8:30-9:30 AM (skip execution during this period)
        current_time = datetime.now().time()
        if dt_time(8, 30) <= current_time < dt_time(11, 31):
            # Only send notification if we haven't already notified about this symbol
            if symbol not in self.skipped_symbols_notified:
                self.send_telegram_notification(f"Skipped {symbol} - No short orders between 8:30-8:31 AM")
                self.skipped_symbols_notified.add(symbol)
            print(f"Skipping {symbol} - No short orders between 8:30-8:31 AM")
            return False

        # Add months data check BEFORE other checks
        months_data = self.get_months_data(symbol)
        
        # Check if months > 40 or 'Positive +', skip execution
        if months_data == "Positive +":
            # Only send notification if we haven't already notified about this symbol
            if symbol not in self.skipped_symbols_notified:
                self.send_telegram_notification(f"Skipped {symbol} - Company is cashflow positive")
                self.skipped_symbols_notified.add(symbol)
            print(f"Skipping {symbol} - Company is cashflow positive")
            return False
        elif isinstance(months_data, (int, float)) and months_data > 40:
            # Only send notification if we haven't already notified about this symbol
            if symbol not in self.skipped_symbols_notified:
                self.send_telegram_notification(f"Skipped {symbol} - Months remaining ({months_data}) > 40")
                self.skipped_symbols_notified.add(symbol)
            print(f"Skipping {symbol} - Months remaining ({months_data}) > 40")
            return False
        elif isinstance(months_data, str) and months_data != 'N/A':
            try:
                months_val = float(months_data)
                if months_val > 40:
                    # Only send notification if we haven't already notified about this symbol
                    if symbol not in self.skipped_symbols_notified:
                        self.send_telegram_notification(f"Skipped {symbol} - Months remaining ({months_val}) > 40")
                        self.skipped_symbols_notified.add(symbol)
                    print(f"Skipping {symbol} - Months remaining ({months_val}) > 40")
                    return False
            except:
                pass  # If we can't parse it, continue with execution

        if symbol in self.buy_back_positions:
            # Only send notification if we haven't already notified about this symbol
            if symbol not in self.skipped_symbols_notified:
                self.send_telegram_notification(f"Skipped {symbol} - Already in buy-back positions")
                self.skipped_symbols_notified.add(symbol)
            print(f"Skipping short order for {symbol} as it's in buy-back positions")
            return False
        
        if symbol in self.positions:
            # Only send notification if we haven't already notified about this symbol
            if symbol not in self.skipped_symbols_notified:
                self.send_telegram_notification(f"Skipped {symbol} - Already in positions")
                self.skipped_symbols_notified.add(symbol)
            print(f"Skipping short order for {symbol} as it's already in positions")
            return False

        # Check if symbol has previously failed
        if symbol in self.failed_symbols:
            last_failure_time = self.failed_symbols[symbol]
            if (datetime.now() - last_failure_time).total_seconds() < 7200:  # 2 hours
                # Only send notification if we haven't already notified about this symbol
                if symbol not in self.skipped_symbols_notified:
                    self.send_telegram_notification(f"Skipped {symbol} - Failed recently (within 2 hours)")
                    self.skipped_symbols_notified.add(symbol)
                print(f"Skipping {symbol} - Failed recently")
                return False

        try:
            print(f"Executing short order for {symbol}")
            
            if self.driver is None:
                self.driver = webdriver.Chrome()
                self.driver.maximize_window()

            self.driver.get("https://activeweb.speedtrader.com/")
            time.sleep(1)

            self.wait_and_send_keys(By.ID, "txtUserID", USERNAME)
            self.wait_and_send_keys(By.ID, "txtPassword", PASSWORD)
            self.wait_and_click(By.ID, "btnLogin")

            if self.wait_and_click(By.ID, "Button1", timeout=5):
                print("Clicked 'Next Time' button.")

            # Get current equity
            current_equity = self.get_current_equity()
            if current_equity is None:
                print("Failed to get current equity")

            if self.wait_and_click(By.ID, "tradingmenu", timeout=10):
                print("Clicked 'TRADING' menu button.")

            # Check if symbol is already in positions
            if self.check_position_exists(symbol):
                print(f"Symbol {symbol} already in positions. Adding to processed symbols.")
                self.positions[symbol] = price
                return False

            # Click on Short Locate tab
            if not self.wait_and_click(By.XPATH, "/html/body/div[3]/div/div/div[3]/div[2]/ul/li[9]/a", timeout=10):
                print("Failed to click Short Locate tab.")
                return False
            
            # Click Short Locate menu button
            if not self.wait_and_click(By.XPATH, "/html/body/div[3]/div/div/div[3]/form/div[3]/div/ul/li[4]/a", timeout=10):
                print("Failed to click Short Locate menu button.")
                return False

            current_price = self.get_current_price(symbol)
            if current_price is None:
                print(f"Unable to get current price for {symbol}. Aborting short order.")
                return False

            # Calculate tranche quantities and total locate quantity needed
            tranche_quantities, total_locate_quantity = self.calculate_tranche_quantities(current_price)

            # Try to locate shares for total quantity
            locate_success = False
            locate_cost = None
            
            # First try LOCATE4 (default)
            success, cost = self.try_locate4_route(symbol, total_locate_quantity)
            if success:
                locate_success = True
                locate_cost = cost
            else:
                # Try other routes
                for route in self.locate_routes[1:]:
                    success, cost = self.try_other_locate_route(route, symbol, total_locate_quantity)
                    if success:
                        locate_success = True
                        locate_cost = cost
                        break
                    time.sleep(1)
            
            if not locate_success:
                print(f"Failed to locate shares for {symbol}.")
                self.failed_symbols[symbol] = datetime.now()
                self.send_telegram_notification(f"Skipping {symbol} - Could not locate shares")
                return False

            # Execute ALL tranche orders in sequence with different prices
            orders_placed = 0
            orders_failed = 0
            order_details = []

            # Click on Position tab
            if not self.wait_and_click(By.ID, "ui-id-22", timeout=10):
                print("Failed to click Position tab.")
                return False

            # Click STOCK+ETFS tab
            if not self.wait_and_click(By.XPATH, "/html/body/div[3]/div/div/div[3]/form/div[3]/div/ul/li[1]/a", timeout=10):
                print("Failed to click STOCK+ETFS tab.")
                return False

            # Place all tranche orders with different prices
            for tranche_num, quantity in enumerate(tranche_quantities, 1):
                # Calculate price for this tranche: (current_price * tranche_num) * 0.95
                order_price = round((current_price * tranche_num) * 0.95, 2)
                order_details.append(f"Tranche {tranche_num}: {quantity} shares @ ${order_price:.2f}")
                print(f"\n--- Placing Tranche {tranche_num}: {quantity} shares @ ${order_price:.2f} ---")
                
                try:
                    # Input symbol
                    if not self.wait_and_send_keys(By.ID, "symbol", symbol):
                        raise Exception(f"Failed to enter symbol for tranche {tranche_num}")
                    
                    # Wait 2 seconds for ticker data to load properly
                    time.sleep(2)
                    print("Waited 2 seconds for ticker data to load")

                    # Input quantity
                    if not self.wait_and_send_keys(By.ID, "quantity", str(quantity)):
                        raise Exception(f"Failed to enter quantity for tranche {tranche_num}")
                    print(f"Entered quantity: {quantity}")
                        
                    # Input limit price
                    if not self.wait_and_send_keys(By.ID, "limitprice", f"{order_price:.2f}"):
                        raise Exception(f"Failed to enter limit price for tranche {tranche_num}")
                    print(f"Entered limit price: {order_price:.2f}")
                    
                    # Check fields before proceeding with multiple attempts
                    field_max_attempts = 3
                    for field_attempt in range(field_max_attempts):
                        # Check if quantity field has a value
                        if not self.check_field_value(By.ID, "quantity"):
                            print(f"Quantity field is empty. Re-entering... (Attempt {field_attempt+1}/{field_max_attempts})")
                            if not self.wait_and_send_keys(By.ID, "quantity", str(quantity)):
                                if field_attempt == field_max_attempts - 1:
                                    raise Exception(f"Failed to enter quantity for tranche {tranche_num} after multiple attempts")
                                continue
                        
                        # Check if limit price field has a value
                        if not self.check_field_value(By.ID, "limitprice"):
                            print(f"Limit price field is empty. Re-entering... (Attempt {field_attempt+1}/{field_max_attempts})")
                            if not self.wait_and_send_keys(By.ID, "limitprice", f"{order_price:.2f}"):
                                if field_attempt == field_max_attempts - 1:
                                    raise Exception(f"Failed to enter limit price for tranche {tranche_num} after multiple attempts")
                                continue
                        
                        # If both fields have values, break the loop
                        if self.check_field_value(By.ID, "quantity") and self.check_field_value(By.ID, "limitprice"):
                            print(f"Verified both quantity and limit price fields have values for tranche {tranche_num}")
                            break

                    if not all([
                        self.wait_and_click(By.ID, "Button4", timeout=10),  # Short button
                        self.wait_and_click(By.ID, "Button6", timeout=30)   # Place Order button
                    ]):
                        raise Exception(f"Failed to click order buttons for tranche {tranche_num}")

                    # Handle any alerts
                    alert_text = self.handle_alert(timeout=5)
                    if alert_text:
                        print(f"Alert after placing tranche {tranche_num}: {alert_text}")

                    if not self.verify_order_placement(symbol, quantity, "Short"):
                        raise Exception(f"Failed to verify order placement for tranche {tranche_num}")

                    print(f"Tranche {tranche_num} placed successfully: {quantity} shares at ${order_price:.2f}")
                    orders_placed += 1

                    # Click Try Again button to reset for next order (except for last tranche)
                    if tranche_num < len(tranche_quantities):
                        try:
                            # Try using XPath first
                            try:
                                try_again_button = WebDriverWait(self.driver, 10).until(
                                    EC.element_to_be_clickable((By.XPATH, 
                                        "/html/body/div[3]/div/div/div[3]/form/div[3]/div/div[1]/div/div/table/tbody/tr[3]/td/div/input"))
                                )
                                try_again_button.click()
                                print(f"Clicked 'Try Again' button for tranche {tranche_num} (XPath)")
                            except:
                                # If XPath fails, try using ID
                                try_again_button = WebDriverWait(self.driver, 10).until(
                                    EC.element_to_be_clickable((By.ID, "btntradeagain"))
                                )
                                try_again_button.click()
                                print(f"Clicked 'Try Again' button for tranche {tranche_num} (ID)")

                            time.sleep(2)  # Wait for the page to reset

                        except Exception as e:
                            print(f"Error clicking 'Try Again' button for tranche {tranche_num}: {e}")
                            # Continue to next tranche even if Try Again fails

                except Exception as e:
                    print(f"Error during tranche {tranche_num} placement: {e}")
                    orders_failed += 1
                    # Continue to next tranche instead of failing completely
                    continue

            # Check if any orders were successful
            if orders_placed > 0:
                # At least one order successful
                self.positions[symbol] = current_price
                print(f"Added {symbol} to positions at price {current_price}")
                
                total_shares_ordered = sum(tranche_quantities[:orders_placed])
                
                self.send_telegram_notification(
                    f"Short orders placed for {symbol}:\n"
                    f"Successfully placed {orders_placed}/{len(tranche_quantities)} tranches\n"
                    f"Orders:\n" + "\n".join(order_details[:orders_placed]) + "\n"
                    f"Total shares ordered: {total_shares_ordered}\n"
                    f"Total located: {total_locate_quantity} shares"
                )
                return True
            else:
                print(f"Failed to place any orders for {symbol}")
                self.failed_symbols[symbol] = datetime.now()
                return False

        except Exception as e:
            print(f"Error executing short order for {symbol}: {e}")
            self.failed_symbols[symbol] = datetime.now()
            return False
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None

    def execute_buy_back_orders(self, candidates):
        try:
            if self.driver is None:
                self.driver = webdriver.Chrome()
                self.driver.maximize_window()

            self.driver.get("https://activeweb.speedtrader.com/")
            time.sleep(1)

            self.wait_and_send_keys(By.ID, "txtUserID", USERNAME)
            self.wait_and_send_keys(By.ID, "txtPassword", PASSWORD)
            self.wait_and_click(By.ID, "btnLogin")

            if self.wait_and_click(By.ID, "Button1", timeout=5):
                print("Clicked 'Next Time' button.")

            if self.wait_and_click(By.ID, "tradingmenu", timeout=10):
                print("Clicked 'TRADING' menu button.")
            else:
                print("Failed to click 'TRADING' menu button. Trying to proceed anyway.")

            # Click on the Position tab
            if self.wait_and_click(By.ID, "ui-id-22", timeout=10):
                print("Clicked 'Position' tab.")
            else:
                print("Failed to click 'Position' tab.")

            for symbol, current_price in candidates:
                quantity, position_type = self.find_position_info(symbol)
                if quantity is None or position_type != "short":
                    print(f"Could not find short position information for {symbol}")
                    continue

                # Click 'STOCK+ETFS' tab
                if self.wait_and_click(By.ID, "ui-id-31", timeout=10):
                    print(f"Clicked 'STOCK+ETFS' tab for {symbol}.")
                else:
                    print(f"Failed to click 'STOCK+ETFS' tab for {symbol}.")
                    continue

                # Input symbol
                if not self.wait_and_send_keys(By.ID, "symbol", symbol):
                    print(f"Failed to enter symbol for {symbol}")
                    continue
                    
                # Input quantity
                if not self.wait_and_send_keys(By.ID, "quantity", str(quantity)):
                    print(f"Failed to enter quantity for {symbol}")
                    continue
                print(f"Entered quantity: {quantity}")
                
                # Calculate new price
                new_price = round(current_price * 1.05, 2)
                
                # Input limit price
                if not self.wait_and_send_keys(By.ID, "limitprice", f"{new_price:.2f}"):
                    print(f"Failed to enter limit price for {symbol}")
                    continue
                print(f"Entered limit price: {new_price:.2f}")
                
                # Check fields before proceeding
                max_attempts = 3
                for attempt in range(max_attempts):
                    # Check if quantity field has a value
                    if not self.check_field_value(By.ID, "quantity"):
                        print(f"Quantity field is empty. Re-entering... (Attempt {attempt+1}/{max_attempts})")
                        if not self.wait_and_send_keys(By.ID, "quantity", str(quantity)):
                            if attempt == max_attempts - 1:
                                print(f"Failed to enter quantity for {symbol} after multiple attempts")
                                break
                            continue
                    
                    # Check if limit price field has a value
                    if not self.check_field_value(By.ID, "limitprice"):
                        print(f"Limit price field is empty. Re-entering... (Attempt {attempt+1}/{max_attempts})")
                        if not self.wait_and_send_keys(By.ID, "limitprice", f"{new_price:.2f}"):
                            if attempt == max_attempts - 1:
                                print(f"Failed to enter limit price for {symbol} after multiple attempts")
                                break
                            continue
                    
                    # If both fields have values, break the loop
                    if self.check_field_value(By.ID, "quantity") and self.check_field_value(By.ID, "limitprice"):
                        print(f"Verified both quantity and limit price fields have values for {symbol}")
                        break
                        
                    # Wait a moment before retry
                    time.sleep(1)

                # Click Buy button
                if self.wait_and_click(By.ID, "Button2", timeout=10):
                    print(f"Clicked 'Buy' button for {symbol}.")
                else:
                    print(f"Failed to click 'Buy' button for {symbol}.")
                    continue

                # Click Place Order button
                if self.wait_and_click(By.ID, "Button6", timeout=30):
                    print(f"Clicked 'Place Order' button for {symbol}.")
                    del self.positions[symbol]
                    self.buy_back_positions[symbol] = current_price
                    print(f"Removed {symbol} from positions and added to buy-back positions.")
                    self.send_telegram_notification(f"Position closed for {symbol}")
                else:
                    print(f"Failed to click 'Place Order' button for {symbol}.")

            # Continue with print_gainers function
            gainers = self.get_gainers(self.current_session)
            if gainers and 'data' in gainers and gainers['data']:
                self.print_gainers(gainers, self.current_session)
            else:
                print(f"\nNo new gainers found in the {self.current_session} session after executing buy-back orders.")
                self.print_current_positions()

        except Exception as e:
            print(f"An error occurred while executing buy-back orders: {e}")
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None

    def find_position_info(self, symbol):
        try:
            position_block = self.driver.find_element(By.CLASS_NAME, "lpositionblock")
            rows = position_block.find_elements(By.TAG_NAME, "tr")
            for row in rows:
                if row.get_attribute("title") == symbol:
                    cols = row.find_elements(By.TAG_NAME, "td")
                    quantity = int(cols[1].text.split()[1])
                    position_type = cols[1].text.split()[0].lower()
                    return quantity, position_type
            return None, None
        except Exception as e:
            print(f"Error finding position information for {symbol}: {e}")
            return None, None

    def buy_to_close_positions(self): 
        """Close positions with consistent workflow"""
        try:
            current_time = datetime.now().time()
            if not (dt_time(4, 0) <= current_time <= dt_time(4, 15)):
                return
                
            processed_symbols = set()
            
            while True:
                try:
                    current_time = datetime.now().time()
                    if not (dt_time(4, 0) <= current_time <= dt_time(4, 15)):
                        print("Time window elapsed")
                        break
                        
                    self.driver = webdriver.Chrome()
                    self.driver.maximize_window()
                    self.driver.get("https://activeweb.speedtrader.com/")
                    time.sleep(1)

                    self.wait_and_send_keys(By.ID, "txtUserID", USERNAME)
                    self.wait_and_send_keys(By.ID, "txtPassword", PASSWORD)
                    self.wait_and_click(By.ID, "btnLogin")
                    
                    if self.wait_and_click(By.ID, "Button1", timeout=5):
                        print("Clicked 'Next Time' button.")

                    if self.wait_and_click(By.ID, "tradingmenu", timeout=10):
                        print("Clicked 'TRADING' menu button.")

                    if not self.wait_and_click(By.ID, "ui-id-22", timeout=10):
                        print("Failed to click 'Position' tab.")
                        continue

                    position_block = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "lpositionblock"))
                    )
                    position_rows = position_block.find_elements(By.TAG_NAME, "tr")
                    
                    position_found = False
                    
                    for row in position_rows:
                        if not row.get_attribute("title"):
                            continue
                            
                        symbol = row.get_attribute("title")
                        # Skip if already processed
                        if symbol in processed_symbols:
                            continue
                            
                        quantity, position_type = self.find_position_info(symbol)
                        if quantity is None or position_type != "short":
                            continue

                        position_found = True
                        print(f"Processing position: {symbol} - {quantity} shares")

                        if not self.wait_and_click(By.ID, "ui-id-31", timeout=10):
                            print(f"Failed to click 'STOCK+ETFS' tab for {symbol}")
                            continue

                        # Input symbol
                        if not self.wait_and_send_keys(By.ID, "symbol", symbol):
                            print(f"Failed to enter symbol for {symbol}")
                            continue
                            
                        # Input quantity
                        if not self.wait_and_send_keys(By.ID, "quantity", str(quantity)):
                            print(f"Failed to enter quantity for {symbol}")
                            continue
                        print(f"Entered quantity: {quantity}")

                        current_price = self.get_current_price(symbol)
                        if current_price is None:
                            continue

                        limit_price = round(current_price * 1.05, 2)
                        
                        # Input limit price
                        if not self.wait_and_send_keys(By.ID, "limitprice", f"{limit_price:.2f}"):
                            print(f"Failed to enter limit price for {symbol}")
                            continue
                        print(f"Entered limit price: {limit_price:.2f}")
                        
                        # Check fields before proceeding
                        max_attempts = 3
                        for attempt in range(max_attempts):
                            # Check if quantity field has a value
                            if not self.check_field_value(By.ID, "quantity"):
                                print(f"Quantity field is empty. Re-entering... (Attempt {attempt+1}/{max_attempts})")
                                if not self.wait_and_send_keys(By.ID, "quantity", str(quantity)):
                                    if attempt == max_attempts - 1:
                                        print(f"Failed to enter quantity for {symbol} after multiple attempts")
                                        break
                                    continue
                            
                            # Check if limit price field has a value
                            if not self.check_field_value(By.ID, "limitprice"):
                                print(f"Limit price field is empty. Re-entering... (Attempt {attempt+1}/{max_attempts})")
                                if not self.wait_and_send_keys(By.ID, "limitprice", f"{limit_price:.2f}"):
                                    if attempt == max_attempts - 1:
                                        print(f"Failed to enter limit price for {symbol} after multiple attempts")
                                        break
                                    continue
                            
                            # If both fields have values, break the loop
                            if self.check_field_value(By.ID, "quantity") and self.check_field_value(By.ID, "limitprice"):
                                print(f"Verified both quantity and limit price fields have values for {symbol}")
                                break
                                
                            # Wait a moment before retry
                            time.sleep(1)

                        # Click Buy button
                        if not self.wait_and_click(By.ID, "Button2", timeout=10):
                            print(f"Failed to click 'Buy' button for {symbol}")
                            continue

                        # Click Place Order button
                        if self.wait_and_click(By.ID, "Button6", timeout=30):
                            if self.verify_order_placement(symbol, quantity, "Buy"):
                                # Add to processed_symbols immediately after successful order
                                processed_symbols.add(symbol)
                                if symbol in self.positions:
                                    del self.positions[symbol]
                                self.buy_back_positions[symbol] = current_price
                                print(f"Successfully closed position for {symbol}")
                                self.send_telegram_notification(f"Position closed for {symbol} at {current_price:.2f}")
                        # Break after processing one symbol to restart with fresh browser
                        break

                    if not position_found:
                        print("No more unprocessed positions found")
                        break
                        
                finally:
                    if self.driver:
                        self.driver.quit()
                        self.driver = None
                    time.sleep(2)  # Reduced from 5 seconds
                    
        except Exception as e:
            print(f"Error in buy_to_close_positions: {e}")
            self.send_telegram_notification(f"Error in buy_to_close_positions: {e}")
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None

    def check_short_locate_block(self, symbol):
        """Check if symbol already exists in short locate order status block with 'Located' status"""
        try:
            # Click on Short Locate tab first
            if not self.wait_and_click(By.XPATH, "/html/body/div[3]/div/div/div[3]/div[2]/ul/li[9]/a", timeout=10):
                print("Failed to click Short Locate tab.")
                return False

            # Wait for short locate order status block to load
            try:
                time.sleep(2)  # Wait for data to load
                short_locate_table = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "/html/body/div[3]/div/div/div[3]/div[2]/div[10]/div[2]/table"))
                )
                rows = short_locate_table.find_elements(By.TAG_NAME, "tr")
                
                for row in rows:
                    try:
                        cells = row.find_elements(By.TAG_NAME, "td")
                        if len(cells) >= 7:  # Make sure we have enough cells
                            symbol_cell = cells[2]  # Symbol is in 3rd column (index 2)
                            status_cell = cells[6]  # Status is in 7th column (index 6)
                            
                            if symbol_cell.text.strip() == symbol and status_cell.text.strip() == "Located":
                                print(f"Symbol {symbol} already located in short locate order status block.")
                                return True
                    except (NoSuchElementException, IndexError):
                        continue
            except Exception as e:
                print(f"Error checking short locate order status block: {e}")
                
            return False
        except Exception as e:
            print(f"Error checking if symbol exists in short locate order status block: {e}")
            return False

    def check_position_exists(self, symbol):
        """Check if symbol already exists in positions tab"""
        try:
            # Click on Position tab
            if not self.wait_and_click(By.ID, "ui-id-22", timeout=10):
                print("Failed to click Position tab.")
                return False

            # Wait for position block to load
            try:
                position_block = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "lpositionblock"))
                )
                rows = position_block.find_elements(By.TAG_NAME, "tr")
                
                for row in rows:
                    if row.get_attribute("title") == symbol:
                        print(f"Symbol {symbol} already exists in positions.")
                        return True
            except Exception as e:
                print(f"Error checking position block: {e}")
                
            return False
        except Exception as e:
            print(f"Error checking if position exists: {e}")
            return False

    def try_locate4_route(self, symbol, quantity):
        """Try LOCATE4 route (default) with improved element location"""
        try:
            print("Trying LOCATE4 route (default)")
            
            # Enter symbol
            if not self.wait_and_send_keys(By.ID, "LocateSymbol", symbol):
                print("Failed to enter symbol.")
                return False, None
            
            # Enter quantity
            if not self.wait_and_send_keys(By.ID, "LocateInquireQty", str(quantity)):
                print("Failed to enter quantity.")
                return False, None
            
            # Click Inquire button
            inquire_xpath = "/html/body/div[3]/div/div/div[3]/form/div[3]/div/div[4]/div/div/div[1]/input[3]"
            if not self.wait_and_click(By.XPATH, inquire_xpath, timeout=15):
                print("Failed to click Inquire button for LOCATE4.")
                return False, None
            
            time.sleep(2)
            
            # Check result with robust element location
            try:
                first_row_xpath = "/html/body/div[3]/div/div/div[3]/div[2]/div[9]/div[2]/table/tbody/tr[1]/td[13]/a"
                first_row = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, first_row_xpath))
                )
                
                # Check symbol using robust method
                symbol_cell = self.find_table_cell_robust(first_row, 3)
                if not symbol_cell or symbol_cell.text != symbol:
                    print(f"Symbol mismatch or cell not found. Expected: {symbol}, Got: {symbol_cell.text if symbol_cell else 'None'}")
                    return False, None
                
                # Check status using robust method
                status_cell = self.find_table_cell_robust(first_row, 7)
                if not status_cell or status_cell.text != "Offered":
                    print(f"Status check failed. Expected: 'Offered', Got: {status_cell.text if status_cell else 'None'}")
                    return False, None
                
                # Check price using robust method
                price_cell = self.find_table_cell_robust(first_row, 11)
                if not price_cell:
                    print("Price cell not found")
                    return False, None
                    
                price_text = price_cell.text
                total_cost = self.parse_locate_cost(price_text)
                if total_cost is None or total_cost >= 3:
                    print(f"Cost check failed. Total cost: {total_cost}")
                    return False, None
                
                # Accept the locate
                accept_button = first_row.find_element(By.XPATH, "td[13]/a/span")
                accept_button.click()
                self.handle_alert(timeout=5)
                
                print("LOCATE4 successfully accepted")
                return True, total_cost / quantity
                
            except Exception as e:
                print(f"Error processing LOCATE4: {e}")
                return False, None
            
        except Exception as e:
            print(f"Error trying LOCATE4 route: {e}")
            return False, None

    def try_other_locate_route(self, route, symbol, quantity):
        """Try other locate routes"""
        try:
            print(f"Trying {route} route")
            
            select_element = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "/html/body/div[3]/div/div/div[3]/form/div[3]/div/div[4]/div/div/div[1]/select"))
            )
            select = Select(select_element)
            select.select_by_visible_text(route)
            time.sleep(0.5)
            
            if not self.wait_and_click(By.ID, "BtnLocateInquire", timeout=15):
                return False, None
            
            time.sleep(2)
            
            cost_per_share, shares_available = self.check_locate_message()
            
            if shares_available and cost_per_share is not None:
                total_cost = cost_per_share * quantity
                
                if total_cost < 50:
                    # Place locate order
                    locate_qty_xpath = "/html/body/div[3]/div/div/div[3]/form/div[3]/div/div[4]/div/div/div[2]/input[1]"
                    if not self.wait_and_send_keys(By.XPATH, locate_qty_xpath, str(quantity)):
                        return False, None
                    
                    if not self.wait_and_click(By.XPATH, "/html/body/div[3]/div/div/div[3]/form/div[3]/div/div[4]/div/div/div[2]/input[2]", timeout=10):
                        return False, None
                    
                    self.handle_alert(timeout=5)
                    
                    if not self.wait_and_click(By.XPATH, "/html/body/div[3]/div/div/div[3]/form/div[3]/div/div[4]/div/div/div[2]/input[1]", timeout=10):
                        return False, None
                    
                    self.handle_alert(timeout=5)
                    
                    print(f"{route} locate placed successfully")
                    return True, cost_per_share
                else:
                    print(f"{route} cost too high: ${total_cost:.2f}")
                    return False, None
            else:
                return False, None
            
        except Exception as e:
            print(f"Error trying {route} route: {e}")
            return False, None

    def check_locate_message(self):
        """Check locate message for cost and availability with improved element location"""
        message_xpath = "/html/body/div[3]/div/div/div[3]/div[2]/div[10]/div[1]/span"
        
        try:
            # Try XPath first
            message_element = None
            try:
                message_element = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, message_xpath))
                )
            except Exception as xpath_error:
                print(f"XPath method failed for locate message: {xpath_error}")
                # Try JavaScript selector as fallback
                try:
                    message_element = self.driver.execute_script(
                        "return document.querySelector('#shortlocatemsg')"
                    )
                    if not message_element:
                        print("JavaScript selector returned null for locate message")
                        return None, False
                except Exception as js_error:
                    print(f"JavaScript selector failed for locate message: {js_error}")
                    return None, False
            
            if not message_element:
                return None, False
                
            message = message_element.text.strip()
            
            if "No shares available" in message:
                return None, False

            if "Already Shortable" in message:
                return 0.0, True
            
            # Extract cost per share
            import re
            cost_match = re.search(r'(\d+\.\d+)/share', message)
            if cost_match:
                cost_per_share = float(cost_match.group(1))
                return cost_per_share, True
            
            return None, False
            
        except Exception as e:
            print(f"Error checking locate message: {e}")
            return None, False

    def parse_locate_cost(self, text_value):
        """Parse locate cost from text value"""
        try:
            if text_value.startswith('$'):
                text_value = text_value[1:]
            text_value = text_value.replace(',', '')
            return float(text_value)
        except Exception as e:
            print(f"Error parsing locate cost: {e}")
            return None

    def handle_alert(self, timeout=5):
        """Handle any alert that appears"""
        try:
            WebDriverWait(self.driver, timeout).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            return alert_text
        except TimeoutException:
            return None

    def verify_order_placement(self, symbol, quantity, order_type="Short"):
        """Verify order was placed successfully"""
        try:
            orderblock = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, "orderblock"))
            )
            
            rows = orderblock.find_elements(By.TAG_NAME, "tr")
            
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 12:
                    order_symbol = cells[2].text
                    order_quantity = cells[1].text
                    order_status = cells[11].find_element(By.TAG_NAME, "span").text.lower()
                    actual_order_type = cells[0].text
                    
                    if (order_symbol == symbol and 
                        int(order_quantity) == quantity and 
                        actual_order_type.strip() == order_type and
                        order_status in ["filled", "open"]):
                        print(f"Order verified: {order_type} {quantity} shares of {symbol} - Status: {order_status.capitalize()}")
                        return True
                    
            return False
            
        except Exception as e:
            print(f"Error verifying order placement: {e}")
            return False

    def check_field_value(self, by, value, timeout=5):
        """Check if a field has a value"""
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            field_value = element.get_attribute('value')
            return field_value.strip() != ""
        except Exception as e:
            return False

    def get_current_price(self, symbol):
        """Get current price for symbol"""
        try:
            quote = self.wb.get_quote(symbol)
            now = datetime.now().time()
            
            if dt_time(4, 0) <= now < dt_time(9, 30):
                # Premarket
                price = quote.get('pPrice') or quote.get('price') or quote.get('close')
            elif dt_time(16, 0) <= now < dt_time(20, 0):
                # Afterhours
                price = quote.get('pPrice') or quote.get('price') or quote.get('close')
            else:
                # Regular session
                price = quote.get('close') or quote.get('price')
            
            if price is not None:
                return float(str(price).replace(',', ''))
            else:
                return None
        except Exception as e:
            print(f"Error fetching current price for {symbol}: {e}")
            return None

    def get_session(self):
        """Get current market session"""
        now = datetime.now().time()
        if dt_time(4, 10) <= now < dt_time(9, 30):
            return "premarket"
        elif dt_time(9, 30) <= now < dt_time(16, 0):
            return "regular"
        elif dt_time(16, 0) <= now < dt_time(20, 0):
            return "afterhours"
        else:
            return None

    def get_gainers(self, session):
        """Get gainers for current session"""
        rank_type = {"premarket": "preMarket", "regular": "1d", "afterhours": "afterMarket"}[session]
        try:
            return self.wb.active_gainer_loser(direction='gainer', rank_type=rank_type, count=10)
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def should_print(self, symbol, change_percentage):
        """Check if symbol should be printed (for filtering duplicates)"""
        if symbol not in self.last_printed:
            self.last_printed[symbol] = change_percentage
            return True
        if abs(change_percentage - self.last_printed[symbol]) > 1:
            self.last_printed[symbol] = change_percentage
            return True
        return False

    def send_telegram_notification(self, message):
        """Send Telegram notification"""
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            print(f"Telegram notification sent: {message}")
        except requests.exceptions.RequestException as e:
            print(f"Failed to send Telegram notification: {e}")

    def print_current_positions(self):
        """Print current positions"""
        if self.positions:
            print("\nCurrent Positions:")
            headers = ["Symbol", "Position Price", "Current Price", "Change%"]
            rows = []
            for symbol, position_price in self.positions.items():
                current_price = self.get_current_price(symbol)
                if current_price is not None:
                    change_percentage = ((current_price - position_price) / position_price) * 100
                    rows.append([
                        symbol,
                        f"{position_price:.2f}",
                        f"{current_price:.2f}",
                        f"{change_percentage:.2f}%"
                    ])
                else:
                    rows.append([symbol, f"{position_price:.2f}", "N/A", "N/A"])
            print(tabulate(rows, headers=headers, tablefmt="grid"))
        else:
            print("No current positions.")

    def clear_failed_symbols(self):
        """Clear old failed symbols"""
        current_time = datetime.now()
        expired_symbols = []
        
        for symbol, failure_time in self.failed_symbols.items():
            if (current_time - failure_time).total_seconds() >= 7200:  # 2 hours
                expired_symbols.append(symbol)
        
        for symbol in expired_symbols:
            del self.failed_symbols[symbol]

    def run(self):
        """Main execution loop"""
        while True:
            try:
                current_time = datetime.now().time()
                
                # Clear failed symbols periodically
                self.clear_failed_symbols()
                
                # CHANGED: 4:00-4:15 AM
                if dt_time(4, 0) <= current_time <= dt_time(4, 15):
                    self.buy_to_close_positions()
                    time.sleep(10)
                    continue
                
                session = self.get_session()
                
                # Handle session changes
                if session != self.current_session:
                    if self.current_session:
                        print(f"Ending {self.current_session} session")
                    self.current_session = session
                    self.last_printed.clear()
                    self.current_equity = None
                    # Clear caches on session change
                    self.avg_volume_cache.clear()
                    self.avg_volume_time_cache.clear()
                    self.vwap_cache.clear()
                    self.months_data_cache.clear()
                    self.skipped_symbols_notified.clear()
                    self.float_shares_cache.clear()
                    print(f"Starting {self.current_session} session")

                # Process session activities
                if session:
                    gainers = self.get_gainers(session)
                    if gainers and 'data' in gainers and gainers['data']:
                        self.print_gainers(gainers, session)
                    else:
                        print(f"\nNo gainers found in the {session} session")
                        self.print_current_positions()
                else:
                    print("Market is currently closed.")
                    self.print_current_positions()

            except Exception as e:
                print(f"Error in main loop: {e}")
                self.send_telegram_notification(f"Error in main loop: {e}")
                
            finally:
                time.sleep(1)

    def get_element_robust(self, primary_by, primary_value, js_selector=None, timeout=10):
        """
        Get element using multiple strategies with fallbacks
        
        Args:
            primary_by: Primary Selenium By method (e.g., By.XPATH, By.ID)
            primary_value: Primary selector value
            js_selector: JavaScript selector as fallback
            timeout: Wait timeout
            
        Returns:
            WebElement if found, None otherwise
        """
        try:
            # Try primary method first
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((primary_by, primary_value))
            )
            return element
        except Exception as primary_error:
            print(f"Primary selector failed ({primary_by}, {primary_value}): {primary_error}")
            
            # Try JavaScript selector if provided
            if js_selector:
                try:
                    element = self.driver.execute_script(f"return {js_selector}")
                    if element:
                        return element
                    else:
                        print(f"JavaScript selector returned null: {js_selector}")
                except Exception as js_error:
                    print(f"JavaScript selector failed ({js_selector}): {js_error}")
            
            return None

    def find_table_cell_robust(self, table_row, column_index, table_id="ShortLocateTable"):
        """
        Find table cell using multiple strategies
        
        Args:
            table_row: Row element or row index (1-based)
            column_index: Column index (1-based)
            table_id: Table ID for JavaScript selector
            
        Returns:
            WebElement if found, None otherwise
        """
        try:
            # If table_row is an integer, convert to element reference
            if isinstance(table_row, int):
                row_xpath = f"//table[@id='{table_id}']//tbody/tr[{table_row}]"
                table_row = self.driver.find_element(By.XPATH, row_xpath)
            
            # Try XPath method first
            try:
                cell = table_row.find_element(By.XPATH, f"td[{column_index}]")
                return cell
            except Exception as xpath_error:
                print(f"XPath method failed for table cell td[{column_index}]: {xpath_error}")
                
                # Try JavaScript selector as fallback
                try:
                    js_selector = f"document.querySelector('#{table_id} > tbody > tr:nth-child(1) > td:nth-child({column_index})')"
                    cell = self.driver.execute_script(f"return {js_selector}")
                    if cell:
                        return cell
                    else:
                        print(f"JavaScript selector returned null for table cell: {js_selector}")
                except Exception as js_error:
                    print(f"JavaScript selector failed for table cell: {js_error}")
                
                return None
                
        except Exception as e:
            print(f"Error finding table cell: {e}")
            return None

    def check_locate_availability(self, symbol, route):
        """Check if short locate is available for a symbol with improved element location"""
        try:
            # Navigate to locate page if not already there
            if "locate" not in self.driver.current_url.lower():
                self.driver.get("https://activeweb.interactivebrokers.com/GtwServ?option=shortlocate")
                time.sleep(2)
                
                # Dismiss any alerts
                try:
                    alert = self.driver.switch_to.alert
                    alert.accept()
                    time.sleep(1)
                except:
                    pass
            
            # Enter symbol
            if not self.wait_and_send_keys(By.ID, "Underlying", symbol):
                print(f"Failed to enter symbol {symbol}")
                return False, "Failed to enter symbol"
            
            # Select route
            try:
                select_element = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "Route"))
                )
                select = Select(select_element)
                select.select_by_value(route)
                time.sleep(2)
            except Exception as e:
                return False, f"Failed to select route {route}: {e}"
            
            # Click search
            if not self.wait_and_click(By.ID, "btnSearch"):
                return False, "Failed to click search button"
            
            time.sleep(2)
            
            # Check for results using robust methods
            try:
                # Method 1: Check for error message first
                error_found = False
                try:
                    # Try XPath first
                    error_element = self.driver.find_element(By.XPATH, "//td[contains(text(), 'No Short Locate found')]")
                    error_found = True
                except:
                    # Try JavaScript selector for error message
                    error_element = self.get_element_robust(
                        By.ID, "shortlocatemsg",
                        "document.querySelector('#shortlocatemsg')"
                    )
                    if error_element and 'no short locate' in error_element.text.lower():
                        error_found = True
                
                if error_found:
                    return False, "No Short Locate found"
                
                # Method 2: Look for results table
                table = self.get_element_robust(
                    By.ID, "ShortLocateTable",
                    "document.querySelector('#ShortLocateTable')"
                )
                
                if not table:
                    return False, "Results table not found"
                
                # Look for the third column (rate) using robust method
                rate_cell = self.find_table_cell_robust(1, 3, "ShortLocateTable")
                
                if rate_cell:
                    rate_text = rate_cell.text.strip()
                    if rate_text and rate_text != "":
                        return True, f"Available - Rate: {rate_text}"
                    else:
                        return False, "No rate information found"
                else:
                    return False, "Rate cell not found"
                    
            except Exception as e:
                print(f"Error checking results: {e}")
                return False, f"Error checking results: {e}"
                
        except Exception as e:
            print(f"Error in check_locate_availability: {e}")
            return False, f"Error: {e}"

    def wait_and_click(self, by, value, timeout=10, retries=3):
        """Wait and click element with retries"""
        for attempt in range(retries):
            try:
                element = WebDriverWait(self.driver, timeout).until(
                    EC.element_to_be_clickable((by, value))
                )
                element.click()
                time.sleep(1)
                return True
            except (TimeoutException, ElementClickInterceptedException, StaleElementReferenceException) as e:
                if attempt == retries - 1:
                    return False
                time.sleep(1)

    def wait_and_send_keys(self, by, value, keys, timeout=20, retries=5):
        """Wait and send keys with retries - original version"""
        for attempt in range(retries):
            try:
                element = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
                element.clear()
                element.send_keys(keys)
                time.sleep(3)
                if element.get_attribute('value') == keys:
                    return True
                else:
                    print(f"Entered value was cleared. Retrying...")
            except (TimeoutException, StaleElementReferenceException) as e:
                print(f"Attempt {attempt + 1} failed to send keys to element: {value}. Error: {str(e)}")
            if attempt == retries - 1:
                return False
            time.sleep(1)


if __name__ == "__main__":
    tracker = StockTracker()
    tracker.run()
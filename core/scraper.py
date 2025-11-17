import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta
import re
import time
import random
from typing import List, Dict, Any
import os
import logging
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()

# Set up basic logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration Loaded from .env
MENSA_ID = os.getenv("MENSA_ID", "191")
AJAX_URL = os.getenv("AJAX_URL", "https://www.stw.berlin/xhr/speiseplan-wochentag.html")
MENSA_NAME = os.getenv("MENSA_NAME", "Mensa")
LOOKUP_FILE = os.getenv("LOOKUP_FILE", "lookup_tables.json")
MENU_DATA_FILE = os.getenv("MENU_DATA_FILE", "mensa_menu.json")


# Global Lookup Data Loader
def load_lookup_tables() -> Dict[str, Dict[str, str]]:
    """Loads static lookup data from JSON file."""
    try:
        if not os.path.exists(LOOKUP_FILE):
             logger.error(f"CRITICAL ERROR: {LOOKUP_FILE} not found. Please create it.")
             return {"allergens_and_additives": {}, "pictograms": {}}
             
        with open(LOOKUP_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {LOOKUP_FILE}: {e}")
        return {"allergens_and_additives": {}, "pictograms": {}}

LOOKUP_TABLES = load_lookup_tables()
ALLERGEN_LOOKUP = LOOKUP_TABLES.get("allergens_and_additives", {})
ICON_LOOKUP = LOOKUP_TABLES.get("pictograms", {})

class MensaScraper:
    
    ICON_URL_MAP = {
        '/1.png': 'vegetarian',
        '/15.png': 'vegan',
        '/43.png': 'klimaessen',
        '/41.png': 'fairtrade',
        '/38.png': 'sustainable_fish',
        'ampel_gruen': 'ampel_green',
        'ampel_gelb': 'ampel_yellow',
        'ampel_rot': 'ampel_red'
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest'
        })
    
    def parse_price(self, price_text: str) -> Dict[str, str] | None:
        """Parse price text to extract student/employee/guest prices"""
        prices = re.findall(r'[\d,]+', price_text)
        if len(prices) >= 3:
            return {
                'student': prices[0].replace(',', '.'),
                'employee': prices[1].replace(',', '.'),
                'guest': prices[2].replace(',', '.')
            }
        return None
    
    def _extract_details(self, row: BeautifulSoup) -> Dict[str, Any]:
        """Extracts all detailed info (icons, prices, allergens) from a single meal row."""
        meal_details: Dict[str, Any] = {
            'name': 'N/A',
            'price': {},
            'allergens': [],
            'dietary_icons': [],
            'sustainability': []
        }

        # Extract Meal Name
        name_container = row.find('span', class_='bold')
        if name_container:
            meal_details['name'] = name_container.get_text(strip=True)

        # Extract Prices
        price_container = row.find('div', class_='text-right')
        if price_container:
            price_text = price_container.get_text(strip=True)
            meal_details['price'] = self.parse_price(price_text)

        # Extract Allergens (from tooltip table)
        allergen_tooltip = row.find('div', class_='kennz')
        if allergen_tooltip:
            tooltip_table = allergen_tooltip.find('table', class_='tooltip_content')
            if tooltip_table:
                for a_row in tooltip_table.find_all('tr'):
                    cells = a_row.find_all('td')
                    if len(cells) >= 2:
                        code = cells[0].get_text(strip=True)
                        meal_details['allergens'].append({
                            'code': code,
                            'name': ALLERGEN_LOOKUP.get(code, cells[1].get_text(strip=True))
                        })

        # Extract All Icons (Dietary, Ampel, CO2/H2O) and their descriptions
        for element in row.find_all(['img', 'i']):
            icon_name = 'unknown'
            icon_desc = ''

            if element.name == 'img':
                src = element.get('src', '')
                
                for key, val in self.ICON_URL_MAP.items():
                    if key in src:
                        icon_name = val
                        break
                
                if 'CO2_bewertung' in src:
                    rating_letter = src.split('_')[-1].split('.')[0].upper()
                    icon_name = f'co2_rating_{rating_letter}'
                elif 'H2O_bewertung' in src:
                    rating_letter = src.split('_')[-1].split('.')[0].upper()
                    icon_name = f'H2O_rating_{rating_letter}'
                
            elif element.name == 'i':
                if 'glyphicons-temperature-low' in element.get('class', []):
                    icon_name = 'cooled_meal'

            icon_desc = ICON_LOOKUP.get(icon_name)
            
            tooltip_div = element.find_next_sibling('div', class_='shocl_content')
            if tooltip_div:
                 metric_desc = tooltip_div.get_text(' ', strip=True).replace('\n', ' ')
                 if 'CO2' in metric_desc or 'Wasserverbrauch' in metric_desc:
                    meal_details['sustainability'].append(metric_desc)

            if icon_name not in ['unknown'] and not icon_name.startswith('co2_rating_') and not icon_name.startswith('H2O_rating_'):
                meal_details['dietary_icons'].append({'type': icon_name, 'description': icon_desc})

        return meal_details

    def fetch_day_html(self, target_date: datetime) -> str | None:
        """
        Sends the working POST request to fetch the raw HTML for a single day.
        """
        date_str = target_date.strftime('%Y-%m-%d')
        payload = {
            "resources_id": MENSA_ID,
            "date": date_str,
            "week": ""
        }

        logger.info(f"  -> Fetching menu for {date_str}...")
        try:
            resp = requests.post(AJAX_URL, data=payload, headers=self.session.headers, timeout=10)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as e:
            logger.error(f"  -> Error fetching menu for {date_str} via POST: {e}")
            return None

    def parse_day_content(self, html_content: str, date_obj: datetime) -> Dict[str, Any]:
        """Parses the HTML fragment returned by the AJAX call and structures the data."""
        soup = BeautifulSoup(html_content, 'html.parser')
        day_menu: Dict[str, Any] = {
            'day': date_obj.strftime('%A'),
            'date': date_obj.strftime('%Y-%m-%d'),
            'categories': []
        }
        
        current_category: Dict[str, Any] | None = None
        elements = soup.find_all(['div'])
        
        for element in elements:
            if 'splGroup' in element.get('class', []):
                category_name = element.get_text(strip=True)
                current_category = {'name': category_name, 'meals': []}
                day_menu['categories'].append(current_category)

            elif 'splMeal' in element.get('class', []) and current_category is not None:
                meal_data = self._extract_details(element)
                if meal_data['name'] and meal_data['name'] != 'N/A':
                    current_category['meals'].append(meal_data)

        day_menu['categories'] = [cat for cat in day_menu['categories'] if cat['meals']]

        return day_menu

    def scrape_week(self) -> Dict[str, Any]:
        """
        Fetch and parsing of the menu for a rolling 7-day window
        starting from today (Saturday and Sunday is skipped).
        """
        today = datetime.now()
        
        week_data: List[Dict[str, Any]] = []
        target_dates: List[datetime] = []
                
        current_date = today
        days_added = 0
        
        while days_added < 7:
            # Python: Monday is 0, Sunday is 6
            if current_date.weekday() not in [5, 6]: # Skip Saturday and Sunday
                target_dates.append(current_date)
                days_added += 1
            
            current_date += timedelta(days=1)

        logger.info(f"--- Fetching menu for rolling 7-day window (Today: {today.strftime('%Y-%m-%d')}) ---")

        for current_date in target_dates:
            day_name = current_date.strftime('%A')
            
            logger.info(f"-> Scraping {day_name} ({current_date.strftime('%Y-%m-%d')})...")
            
            html_content = self.fetch_day_html(current_date)
            
            if html_content:
                day_menu = self.parse_day_content(html_content, current_date)
                
                if day_menu['categories']:
                    week_data.append(day_menu)
                else:
                    logger.info(f"  -> No menu found for {day_name}.")
            
            time.sleep(random.uniform(0.5, 1.5)) # Add a small delay between requests
        
        return {
            'mensa': MENSA_NAME,
            'week_data': week_data
        }
    
    def save_to_json(self, data: Dict[str, Any]) -> None:
        """Saves scraped data to JSON file."""
        try:
            with open(MENU_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Data successfully saved to {MENU_DATA_FILE}")
        except IOError as e:
            logger.error(f"Error saving file: {e}")


def main():
    """Main function to run the scraper."""
    scraper = MensaScraper()
    
    logger.info(f"Scraping Mensa {MENSA_NAME} for the current week...")
    menu_data = scraper.scrape_week()
    
    scraper.save_to_json(menu_data)
    
    total_meals = sum(len(cat['meals']) for day in menu_data['week_data'] for cat in day['categories'])
    logger.info(f"Scraped {len(menu_data['week_data'])} active days with a total of {total_meals} meals.")


if __name__ == "__main__":
    main()
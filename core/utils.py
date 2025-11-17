ALLERGEN_LOOKUP = LOOKUP_TABLES.get("allergens_and_additives", {})
ICON_LOOKUP = LOOKUP_TABLES.get("pictograms", {})
DIET_PREF_MAP = {'Vegan': 'vegan', 'Vegetarian': 'vegetarian', 'Low CO2': 'low_co2', 'Low H2O': 'low_h2o'}
DIET_PREF_KEYS = list(DIET_PREF_MAP.values())

def is_last_day_of_menu() -> bool:
    """
    Checks if today's date matches the latest menu date available in the JSON file.
    Returns True if today is the last day (or later), indicating a refresh is needed.
    """
    menu_data = load_menu_data()
    if not menu_data or not menu_data.get('week_data'):
        return True

    latest_date_str = max((day['date'] for day in menu_data['week_data']), default=None)
    
    if not latest_date_str:
        return True

    try:
        latest_date = datetime.strptime(latest_date_str, '%Y-%m-%d').date()
        today = datetime.now().date()
        
        return today >= latest_date
    except ValueError:
        logger.error("Error parsing date format in menu file. Forcing refresh.")
        return True
    
def get_next_scheduled_scrape_time() -> datetime:
    """
    Calculates the next scheduled run time (Monday at 06:00:00).
    """
    now = datetime.now()
    
    # 0 = Monday, 6 = Sunday
    target_weekday = 0 
    
    # Calculate days until next Monday
    days_ahead = target_weekday - now.weekday()
    
    if days_ahead <= 0 and now.time() >= time(6, 0, 0):
        days_ahead += 7

    next_run_date = now.date() + timedelta(days=days_ahead)
    
    # Set the time to 6am
    next_run_datetime = datetime(
        next_run_date.year, 
        next_run_date.month, 
        next_run_date.day, 
        hour=6, 
        minute=0, 
        second=0
    )
    
    # Check if the calculated time is in the past
    if next_run_datetime < now:
        next_run_datetime += timedelta(days=7)
        
    return next_run_datetime

def load_user_data() -> Dict[str, Any]:
    """Loads user data from JSON file."""
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_user_data(data: Dict[str, Any]) -> None:
    """Saves user data to JSON file."""
    with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_menu_data() -> Dict[str, Any] | None:
    """Loads the latest menu data from JSON file."""
    if os.path.exists(MENU_DATA_FILE):
        with open(MENU_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def is_admin(user_id: int) -> bool:
    """Checks if the given user ID is the configured admin ID."""
    return user_id == ADMIN_ID

def is_meal_eligible(meal: Dict[str, Any], user_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Checks if a meal is safe (no allergens) and if it matches the user's preferences,
    including hierarchical matching (e.g., Vegan matches Vegetarian).
    
    Returns:
        A dictionary with eligibility status and reasons.
    """
    
    # Initialize eligibility status
    result = {
        'safe': True,
        'matches_pref': False,
        'allergy_violations': [],
        'pref_matches': [],
        'pref_violations': []
    }

    # Allergen Check
    user_allergies = set(user_data.get('allergy_codes', []))
    if user_allergies:
        meal_allergen_codes = {a['code'].lower() for a in meal.get('allergens', [])}
        
        conflicts = user_allergies.intersection(meal_allergen_codes)
        
        if conflicts:
            result['safe'] = False
            result['allergy_violations'] = [
                f"{c.upper()}: {ALLERGEN_LOOKUP.get(c.lower(), 'Unknown')}" 
                for c in conflicts
            ]

    # Preference Check
    user_prefs = set(user_data.get('diet_preferences', []))
    
    meal_icons_fulfilled = set()
    meal_icons = {icon['type'].lower() for icon in meal.get('dietary_icons', [])}
    
    for icon_type in meal_icons:
        if icon_type == 'vegan':
            meal_icons_fulfilled.add('vegan')
            meal_icons_fulfilled.add('vegetarian') # Vegan satisfies Vegetarian
        elif icon_type == 'vegetarian':
            meal_icons_fulfilled.add('vegetarian')
        elif icon_type in DIET_PREF_KEYS:
            meal_icons_fulfilled.add(icon_type)
        
    matches = user_prefs.intersection(meal_icons_fulfilled)
    if matches:
        result['matches_pref'] = True
        result['pref_matches'].extend(matches)
        
    meal_sustainability_text = " ".join(meal.get('sustainability', [])).lower()
    
    if 'low_co2' in user_prefs:
        if any(rating in meal_sustainability_text for rating in ['wesentlich', 'leicht']):
            result['matches_pref'] = True
            result['pref_matches'].append('low_co2')
        else:
            result['pref_violations'].append('Low CO2') # Did not match
            
    if 'low_h2o' in user_prefs:
        if 'unter dem durchschnitt' in meal_sustainability_text:
            result['matches_pref'] = True
            result['pref_matches'].append('low_h2o')
        else:
            result['pref_violations'].append('Low H2O') # Did not match

    requested_prefs = set(DIET_PREF_KEYS).intersection(user_prefs)
    
    unfulfilled_prefs = requested_prefs.difference(set(result['pref_matches']))

    for pref in unfulfilled_prefs:
        if pref.title() not in result['pref_violations']:
             result['pref_violations'].append(pref.title())

    result['pref_matches'] = sorted(list(set(result['pref_matches'])))
    result['pref_violations'] = sorted(list(set(result['pref_violations'])))
        
    if requested_prefs and not result['pref_matches']:
        result['matches_pref'] = False
    elif requested_prefs:
        result['matches_pref'] = True
        
    return result
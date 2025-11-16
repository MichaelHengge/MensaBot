import logging
import json
import os
import re
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
from datetime import datetime, timedelta, time
from dotenv import load_dotenv
from typing import List, Dict, Any
from telegram.error import BadRequest

# Load Environment Variables and Configuration
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
REGISTRATION_PASSWORD = os.getenv("REGISTRATION_PASSWORD")
USER_DATA_FILE = os.getenv("USER_DATA_FILE", "user_data.json")
MENU_DATA_FILE = os.getenv("MENU_DATA_FILE", "mensa_menu.json")
LOOKUP_FILE = os.getenv("LOOKUP_FILE", "lookup_tables.json")

# Import Scraper
from mensa_scraper import main as run_scraper 

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PASSWORD, NAME, STATUS, DIET_PREFS, ALLERGY_PREFS, NOTIFY_KEYWORD, NOTIFY_DELETE_INDEX = range(7)
DELETE_NOTIF_PREFIX = "DEL_NOTIF:"
KEYWORD_FOUND_PREFIX = "KWFOUND:"  
REMINDER_PREFIX = "REMINDER:"

# Global Lookup
def load_lookup_tables():
    """Loads static lookup data from JSON file."""
    try:
        with open(LOOKUP_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"CRITICAL ERROR: {LOOKUP_FILE} not found.")
        return {"allergens_and_additives": {}, "pictograms": {}}
        
LOOKUP_TABLES = load_lookup_tables()
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
    Checks if a meal is safe (no allergens) and if it matches the user's preferences.
    
    Returns:
        A dictionary with status and reasons.
    """
    
    # Initialize status
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
        
        # Check for allergenee conflicts
        conflicts = user_allergies.intersection(meal_allergen_codes)
        
        if conflicts:
            result['safe'] = False
            result['allergy_violations'] = [
                f"{c.upper()}: {ALLERGEN_LOOKUP.get(c.lower(), 'Unknown')}" 
                for c in conflicts
            ]

    # Preference Checks
    user_prefs = set(user_data.get('diet_preferences', []))
    meal_icons = {icon['type'].lower() for icon in meal.get('dietary_icons', [])}
    
    matches = user_prefs.intersection(meal_icons)
    if matches:
        result['matches_pref'] = True
        result['pref_matches'].extend(matches)
        
    meal_sustainability = " ".join(meal.get('sustainability', [])).lower()
    
    if 'low_co2' in user_prefs and ('wesentlich' in meal_sustainability or 'leicht' in meal_sustainability):
        result['matches_pref'] = True
        result['pref_matches'].append('Low CO₂ Metric Match')

    if 'low_h2o' in user_prefs and 'unter dem durchschnitt' in meal_sustainability:
        result['matches_pref'] = True
        result['pref_matches'].append('Low H₂O Metric Match')
        
    if user_prefs and not result['matches_pref']:
        result['pref_violations'] = [p.title() for p in user_prefs]
        
    return result

async def cancel(update: Update, context) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text(
        'Operation cancelled.', reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def menu_stats(update: Update, context):
    """Admin command that returns statistics of the current menu file."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Admin access required.")
        return

    menu_data = load_menu_data()
    if not menu_data or not menu_data.get('week_data'):
        await update.message.reply_text("*⚠️ Menu file is empty or missing.* Run `/refetch_menu` to populate it.", parse_mode="Markdown")
        return

    stats = {
        'total_meals': 0,
        'dietary_counts': {'vegan': 0, 'vegetarian': 0},
        'sustainability_counts': {'klimaessen': 0, 'low_co2': 0, 'low_h2o': 0},
        'dates': []
    }

    # Use a set to track unique dates
    date_set = set()

    for day in menu_data['week_data']:
        date_set.add(day['date'])
        for category in day['categories']:
            for meal in category['meals']:
                stats['total_meals'] += 1

                # Tally Dietary & Sustainability counts
                for icon in meal.get('dietary_icons', []):
                    icon_type = icon['type']
                    if icon_type == 'vegan':
                        stats['dietary_counts']['vegan'] += 1
                    elif icon_type == 'vegetarian':
                        stats['dietary_counts']['vegetarian'] += 1
                    elif icon_type == 'klimaessen':
                        stats['sustainability_counts']['klimaessen'] += 1
                
                for metric in meal.get('sustainability', []):
                    metric_lower = metric.lower()
                    if 'co2' in metric_lower and ('wesentlich' in metric_lower or 'leicht' in metric_lower):
                        stats['sustainability_counts']['low_co2'] += 1
                    if 'wasserverbrauch' in metric_lower and 'unter dem durchschnitt' in metric_lower:
                        stats['sustainability_counts']['low_h2o'] += 1

    if date_set:
        min_date_str = min(date_set)
        max_date_str = max(date_set)
        
        try:
            min_date = datetime.strptime(min_date_str, '%Y-%m-%d').strftime('%A, %b %d')
            max_date = datetime.strptime(max_date_str, '%Y-%m-%d').strftime('%A, %b %d')
            date_range_msg = f"*{min_date}* to *{max_date}*"
        except ValueError:
            date_range_msg = "Error parsing dates."
    else:
        date_range_msg = "No dates found."


    # Final message
    message = (
        f"*📊 Menu Statistics*\n"
        f"---------------------------\n"
        f"🗓️ *Data Range:* {date_range_msg}\n"
        f"🍽️ *Total Meals:* {stats['total_meals']}\n"
        f"---------------------------\n"
        f"🌱 *Dietary Breakdown*\n"
        f" - Vegan: {stats['dietary_counts']['vegan']}\n"
        f" - Vegetarian: {stats['dietary_counts']['vegetarian']}\n"
        f"---------------------------\n"
        f"🌎 *Sustainability Focus*\n"
        f" - Klimaessen (Icon): {stats['sustainability_counts']['klimaessen']}\n"
        f" - Low CO₂ (Metric): {stats['sustainability_counts']['low_co2']}\n"
        f" - Low H₂O (Metric): {stats['sustainability_counts']['low_h2o']}\n"
    )

    await update.message.reply_text(message, parse_mode="Markdown")

async def post_init_notify(application: Application) -> None:
    """Sends a notification to the admin on bot startup."""
    
    next_scrape_time = get_next_scheduled_scrape_time()
    next_scrape_display = next_scrape_time.strftime('%A, %Y-%m-%d at %H:%M CET')
    
    startup_message = (
        f"*🤖 MensaBot STARTUP*\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} CET\n\n"
        f"⏳ **Next Scheduled Menu Fetch:**\n"
        f"*{next_scrape_display}*"
    )
    
    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID, 
            text=startup_message, 
            parse_mode="Markdown"
        )
        logger.info(f"Sent startup notification to Admin ID: {ADMIN_ID}")
    except BadRequest as e:
        if "Chat not found" in str(e):
            logger.warning(
                f"Could not send startup notification to Admin ID {ADMIN_ID}. "
                "The admin must send a message to the bot first to open the chat."
            )
        else:
            logger.error(f"Error sending startup notification: {e}")

async def post_stop_notify(application: Application) -> None:
    """Sends a notification to the admin on bot shutdown."""
    shutdown_message = f"❌ *MensaBot SHUTDOWN*\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} CET"
    
    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID, 
            text=shutdown_message, 
            parse_mode="Markdown"
        )
        logger.info(f"Sent shutdown notification to Admin ID: {ADMIN_ID}")
    except Exception as e:
        logger.warning(f"Could not send shutdown message: {e}")


async def list_allergens(update: Update, context):
    """Displays the full list of allergen/additive codes and descriptions, sorted numerically."""
    message = "*Allergen/Additive Codes:*\n"
    
    def custom_allergen_sort(code: str):
        # Extracts  numerical part and the abc suffix
        match = re.match(r'(\d+)([a-zA-Z]*)', code)
        if match:
            return (int(match.group(1)), match.group(2).lower())
        return (999, code)

    sorted_codes = sorted(
        ALLERGEN_LOOKUP.keys(), 
        key=custom_allergen_sort
    )
    
    for code in sorted_codes:
        message += f"*{code.upper()}*: {ALLERGEN_LOOKUP[code]}\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def start_registration(update: Update, context) -> int:
    """Start the registration process and ask for the password."""
    user_id = str(update.effective_user.id)
    user_data = load_user_data()

    if user_id in user_data:
        await update.message.reply_text("You are already registered! Use /status to see your information.")
        return ConversationHandler.END

    await update.message.reply_text(
        "Welcome to the Mensa Bot Registration. Please enter the registration *password*:"
    )
    return PASSWORD

async def verify_password(update: Update, context) -> int:
    """Verifies the password."""
    if update.message.text.strip() == REGISTRATION_PASSWORD:
        await update.message.reply_text(
            "Password accepted! What is your *name*?",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME
    else:
        await update.message.reply_text("Incorrect password. Please try /start again.")
        return ConversationHandler.END

async def get_name(update: Update, context) -> int:
    """Stores the name and asks for the price status."""
    context.user_data['name'] = update.message.text
    
    reply_keyboard = [['Student', 'Employee', 'Guest']]
    markup = ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, input_field_placeholder="Select your status")

    await update.message.reply_text(
        f"Hello, {context.user_data['name']}! What is your status at the university (for correct pricing)?",
        reply_markup=markup,
    )
    return STATUS

async def get_status(update: Update, context) -> int:
    """Stores the status and asks for basic food preferences."""
    status_map = {'Student': 'student', 'Employee': 'employee', 'Guest': 'guest'}
    status_input = update.message.text.strip()
    
    if status_input not in status_map:
        await update.message.reply_text("Invalid status. Please select one of the options: Student, Employee, or Guest.")
        return STATUS

    context.user_data['status'] = status_map[status_input]
    
    reply_keyboard = [['Vegan', 'Vegetarian'], ['Low CO2', 'Low H2O Use'], ['None']]
    markup = ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, input_field_placeholder="Select primary preferences (or None)")

    await update.message.reply_text(
        "Which dietary or sustainability preferences do you have? (Select all that apply, separated by commas, or 'None')",
        reply_markup=markup,
    )
    return DIET_PREFS

async def get_diet_prefs(update: Update, context) -> int:
    """Stores basic dietary preferences and proceeds to ask about allergies."""
    prefs_text = update.message.text.strip()
    raw_prefs = [p.strip() for p in prefs_text.split(',') if p.strip()]
    final_diet_prefs = []

    for pref in raw_prefs:
        if pref.lower() in [k.lower() for k in DIET_PREF_MAP.keys()] and pref.lower() != 'none':
            final_diet_prefs.append(DIET_PREF_MAP[pref.title()])
        elif pref.lower() == 'none':
             pass
        else:
             await update.message.reply_text(f"Warning: '{pref}' is not a recognized basic preference. Skipping it.")
    
    context.user_data['diet_prefs'] = final_diet_prefs
    
    await update.message.reply_text(
        "Do you need to avoid any specific allergens or additives? "
        "Enter the *codes* separated by commas (e.g., `21a, 26b`).\n"
        "Enter */list_allergens* for the full list of codes, or *None* to finish.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return ALLERGY_PREFS

async def get_allergy_prefs(update: Update, context) -> int:
    """Stores allergy codes and finishes the registration."""
    prefs_text = update.message.text.strip().lower()
    raw_prefs = [p.strip() for p in prefs_text.split(',') if p.strip()]
    final_allergy_prefs = []
    
    if prefs_text == 'none':
         pass
    else:
        for pref in raw_prefs:
            if pref in ALLERGEN_LOOKUP:
                final_allergy_prefs.append(pref)
            else:
                 await update.message.reply_text(f"Warning: Code '{pref}' not found. Please re-enter valid codes or 'None'.")
                 return ALLERGY_PREFS
            
    context.user_data['allergy_prefs'] = final_allergy_prefs
    
    user_id = str(update.effective_user.id)
    is_admin_user = is_admin(update.effective_user.id)
    
    current_user_data = load_user_data().get(user_id, {})
    
    notifications = current_user_data.get('notifications', {})
    
    new_user_entry = {
        'name': context.user_data['name'],
        'status': context.user_data['status'],
        'diet_preferences': context.user_data['diet_prefs'],
        'allergy_codes': context.user_data['allergy_prefs'],
        'notifications': notifications,
        'is_admin': is_admin_user,
        'is_muted': False
    }

    all_users = load_user_data()
    all_users[user_id] = new_user_entry
    save_user_data(all_users)

    admin_msg = "You are set as *Admin*." if is_admin_user else ""
    await update.message.reply_text(
        f"Survey complete! Status: *{new_user_entry['status']}*. {admin_msg}\n"
        f"Dietary Prefs: {', '.join([p.title() for p in new_user_entry['diet_preferences']]) or 'None'}.\n"
        f"Allergy Codes: {', '.join(new_user_entry['allergy_codes']) or 'None'}.\n"
        f"You can now use /menu.",
        parse_mode="Markdown"
    )
    
    context.user_data.clear()
    return ConversationHandler.END

async def redo_survey(update: Update, context):
    """Starts the survey from the basic preferences step for existing users."""
    user_id = str(update.effective_user.id)
    user_data = load_user_data()
    
    if user_id not in user_data:
        await update.message.reply_text("Please register first using /start.")
        return ConversationHandler.END
        
    # load existing data
    user = user_data[user_id]
    context.user_data['name'] = user['name']
    context.user_data['status'] = user['status']
    
    reply_keyboard = [['Vegan', 'Vegetarian'], ['Low CO2', 'Low H2O'], ['None']]
    markup = ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, input_field_placeholder="Select primary preferences (or None)")

    await update.message.reply_text(
        "Starting the survey again. Which dietary or sustainability preferences do you have? (Select all that apply, separated by commas, or 'None')",
        reply_markup=markup,
    )
    return DIET_PREFS


async def list_users(update: Update, context):
    """Admin command to list all registered users."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Admin access required.")
        return

    all_users = load_user_data()
    if not all_users:
        await update.message.reply_text("No users registered.")
        return

    message = "*Registered Users:*\n"
    for user_id, user in all_users.items():
        admin_status = "(ADMIN)" if user['is_admin'] else ""
        mute_status = "(MUTED)" if user['is_muted'] else ""
        
        diet_prefs_display = [p.title() for p in user.get('diet_preferences', [])]
        allergy_display = [f"{c}" for c in user.get('allergy_codes', [])]
                
        message += (
            f"👤 *{user['name']}* {admin_status} {mute_status}\n"
            f"  - ID: `{user_id}`\n"
            f"  - Status: {user['status'].title()}\n"
            f"  - Diet: {', '.join(diet_prefs_display) or 'None'}\n"
            f"  - Allergies: {', '.join(allergy_display) or 'None'}\n"
        )
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def delete_user(update: Update, context):
    """Admin command to delete a user by ID."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Admin access required.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/delete_user <user_id>`")
        return

    target_id = context.args[0].strip()
    all_users = load_user_data()
    
    if target_id in all_users:
        del all_users[target_id]
        save_user_data(all_users)
        await update.message.reply_text(f"User with ID `{target_id}` deleted.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"User with ID `{target_id}` not found.", parse_mode="Markdown")

async def stop_bot(update: Update, context):
    """Admin command to gracefully stop the bot."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Admin access required to stop the bot.")
        return

    await update.message.reply_text("👋 Shutting down the bot gracefully...")
    logger.info(f"Admin {update.effective_user.id} initiated bot shutdown.")

    await context.application.stop()


async def check_and_notify_users(application: Application):
    """
    Checks all users' active notifications against the menu from today's date forward. 
    Only runs after a menu update and if the meal occurrence date is NEWER than the last date the user was notified.
    """
    logger.info("Starting scheduled meal notification check (keyword find).")
    menu_data = load_menu_data()
    all_users = load_user_data()
    users_to_delete = set()
    
    if not menu_data or not menu_data.get('week_data'):
        logger.warning("Notification check skipped: Menu data unavailable.")
        return

    today_date = datetime.now().date()
    
    searchable_meals = []
    for day in menu_data['week_data']:
        meal_date_obj = datetime.strptime(day['date'], '%Y-%m-%d').date()
        
        if meal_date_obj >= today_date:
            for category in day['categories']:
                 for meal in category['meals']:
                     searchable_meals.append({
                         'date': day['date'], 
                         'keyword': meal['name'].lower()
                     })
    
    for user_id_str, user in all_users.items():
        if user.get('is_muted') or not user.get('notifications'):
            continue
        
        notifications_to_save = False
        user_id_int = int(user_id_str)
        
        for notif_id, notif in user['notifications'].items():
            
            if not notif.get('active_for_future', True): 
                continue 

            keyword = notif['keyword'].lower()
            
            last_notified_date = datetime.strptime(
                notif.get('triggered_date', '1900-01-01'), '%Y-%m-%d'
            ).date()
            
            first_occurrence_date_str = next(
                (m['date'] for m in searchable_meals if keyword in m['keyword']), 
                None
            )

            if first_occurrence_date_str:
                first_occurrence_date_obj = datetime.strptime(first_occurrence_date_str, '%Y-%m-%d').date()
                
                if first_occurrence_date_obj <= last_notified_date:
                    logger.debug(f"Keyword '{keyword}' found, but date {first_occurrence_date_str} is not newer than {last_notified_date}. Skipping notification.")
                    continue
                    
                date_display = first_occurrence_date_obj.strftime('%A, %b %d')

                notification_message = (
                    f"🔔 *MEAL ALERT!* 🔔\n"
                    f"Your keyword *{notif['keyword']}* was found in the NEW menu!\n"
                    f"🗓️ *Date:* {date_display}\n\n"
                    "What would you like to do next?"
                )
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔔 Set 10 AM Reminder", callback_data=f"{REMINDER_PREFIX}SET:{notif_id}:{first_occurrence_date_str}"),
                        InlineKeyboardButton("❌ Don't Set Reminder", callback_data=f"{REMINDER_PREFIX}NO:{notif_id}:{first_occurrence_date_str}")
                    ],
                    [
                        InlineKeyboardButton("Keep Active for Future ✅", callback_data=f"{KEYWORD_FOUND_PREFIX}KEEP:{notif_id}"),
                        InlineKeyboardButton("Delete Alert Now 🗑️", callback_data=f"{KEYWORD_FOUND_PREFIX}DELETE:{notif_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    await application.bot.send_message(
                        chat_id=user_id_int, 
                        text=notification_message, 
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                    
                    user['notifications'][notif_id]['triggered_date'] = first_occurrence_date_str
                    user['notifications'][notif_id]['reminder_set'] = False # Reset reminder status
                    user['notifications'][notif_id]['reminder_sent'] = False # Reset reminder sent flag
                    notifications_to_save = True
                    
                except BadRequest as e:
                    if 'Chat not found' in str(e) or 'Forbidden' in str(e):
                        logger.warning(f"Removing user {user_id_str}: Bot was blocked or chat deleted. Error: {e}")
                        users_to_delete.add(user_id_str) 
                        break # Move to the next user
                    else:
                        logger.error(f"Error sending notification to {user_id_str}: {e}")

        if notifications_to_save:
            save_user_data(all_users)
            
    if users_to_delete:
        logger.info(f"Attempting to delete {len(users_to_delete)} stale users.")
        all_users = load_user_data() 
        for stale_id in users_to_delete:
            if stale_id in all_users:
                del all_users[stale_id]
        save_user_data(all_users)
        logger.info("Stale users deleted and user data saved.")
        
    logger.info("Scheduled meal notification check complete.")

async def refetch_menu(update: Update, context):
    """Refetches the menu (Admin only) and runs the notification checker."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Admin access required.")
        return
    
    await update.message.reply_text("Starting the scraper. This may take a moment...")
    logger.info(f"Admin {update.effective_user.id} triggered menu refetch.")
    
    try:
        run_scraper() 
        await update.message.reply_text("✅ Menu refresh complete! New data saved.")
        
        await check_and_notify_users(context.application)
        await update.message.reply_text("✅ Notification check complete.")
        
    except Exception as e:
        logger.error(f"Scraper failed: {e}")
        await update.message.reply_text(f"❌ Scraper failed. Check logs for error: {e}")


async def format_meal_message(meal: Dict[str, Any], user_status: str, eligibility: Dict[str, Any]) -> str:
    """Formats a single meal dictionary into a readable string, including safety and preference flags."""
    
    price = meal.get('price', {})
    price_level_key = {'student': 'student', 'employee': 'employee', 'guest': 'guest'}.get(user_status, 'guest')
    display_price = price.get(price_level_key, 'N/A') if isinstance(price, dict) else 'N/A'
    
    prefix_icon = "🍽️"
    safety_flag = ""
    pref_flag = ""
    
    if not eligibility['safe']:
        prefix_icon = "🛑"
        safety_flag = f"\n   - *DANGER:* CONFLICTS WITH ALLERGY CODES: {', '.join(eligibility['allergy_violations'])}"
    
    if eligibility['matches_pref']:
        pref_flag = f"\n   - *MATCH:* Meets your criteria: {', '.join(eligibility['pref_matches'])}"
    
    elif eligibility['safe'] and eligibility['pref_violations']:
        pref_flag = f"\n   - *NOTE:* Does NOT meet your preferences: {', '.join(eligibility['pref_violations'])}"
        prefix_icon = "🟡"

    # ------------------------------------------------------
    
    icons = []
    for icon in meal.get('dietary_icons', []):
        description = ICON_LOOKUP.get(icon['type'], icon['type'].replace('_', ' ').title())
        icons.append(f"*{description}*")

    allergens = [f"{a['code']}: {a['name']}" for a in meal.get('allergens', [])]
    sustainability = "\n   - " + "\n   - ".join(meal.get('sustainability', [])) if meal.get('sustainability') else ''
    
    msg = (
        f"{prefix_icon} *{meal['name']}*\n"
        f"   - Price ({user_status.title()}): *€ {display_price}*\n"
    )
    
    msg += safety_flag
    msg += pref_flag

    if icons:
        msg += f"\n   - Info: {', '.join(icons)}"
    if allergens:
        msg += f"\n   - Allergens: {', '.join(allergens)}"
    if sustainability:
        msg += f"\n   - Sustainability Metrics: {sustainability}"
    
    return msg

async def show_user_status(update: Update, context):
    """Shows the user's current status and preferences."""
    user_id = str(update.effective_user.id)
    user = load_user_data().get(user_id)
    
    if not user:
        await update.message.reply_text("Please register first using /start.")
        return

    diet_prefs_display = [p.title() for p in user.get('diet_preferences', [])]
    allergy_names = [f"{c}: {ALLERGEN_LOOKUP.get(c, 'Unknown')}" for c in user.get('allergy_codes', [])]
    
    status_message = (
        f"👤 *Your Profile Status*\n"
        f"--- Menu Preferences ---\n"
        f"Status (Pricing): *{user['status'].title()}*\n"
        f"Dietary: {', '.join(diet_prefs_display) or 'None'}\n"
        f"Allergy Codes: {', '.join(allergy_names) or 'None'}\n"
        f"Notifications: {'Muted' if user['is_muted'] else 'Active'}\n"
        f"--- Actions ---\n"
        f"Use */redo_survey* to update your preferences.\n"
        f"Use */show_notifications* to view/manage active meal alerts."
    )
    
    await update.message.reply_text(status_message, parse_mode="Markdown")
    
async def recheck_notifications(update: Update, context):
    """Allows any user to re-run the notification check against the current menu."""
    user_id = str(update.effective_user.id)
    if user_id not in load_user_data():
        await update.message.reply_text("Please register first using /start.")
        return

    await update.message.reply_text("Running notification re-check against current menu data...")
    logger.info(f"User {user_id} triggered notification re-check.")
    
    await check_and_notify_users(context.application)
    await update.message.reply_text("✅ Notification re-check complete. If any meal was found, you will be alerted shortly.")

async def show_today_menu(update: Update, context):
    """Shows the menu for today, sending one message per category."""
    user_id = str(update.effective_user.id)
    user_data = load_user_data().get(user_id)
    
    if not user_data:
        await update.message.reply_text("Please register first using /start.")
        return

    menu_data = load_menu_data()
    if not menu_data or not menu_data.get('week_data'):
        await update.message.reply_text("Menu data is not available. Admin must run /refetch_menu.")
        return

    today_date_str = datetime.now().strftime('%Y-%m-%d')
    today_menu = next((day for day in menu_data['week_data'] if day['date'] == today_date_str), None)
    
    if not today_menu:
        await update.message.reply_text(f"No menu found for today ({today_date_str}).")
        return
        
    header_message = f"*🗓️ Mensa Menu for {today_menu['day']}, {today_menu['date']}*\n"
    await update.message.reply_text(header_message, parse_mode="Markdown")

    user_status = user_data['status']
    
    for category in today_menu['categories']:
        category_message = f"--- *{category['name']}* ---\n\n"
        
        for meal in category['meals']:
            eligibility = is_meal_eligible(meal, user_data)
            
            category_message += await format_meal_message(meal, user_status, eligibility) + "\n\n"

        await update.message.reply_text(category_message, parse_mode="Markdown")

async def get_menu_day(update: Update, context):
    """
    Gets the menu for a specific day of the week. 
    If the requested day has passed this week, it shows the menu for that day next week.
    """
    user_id = str(update.effective_user.id)
    user = load_user_data().get(user_id)

    if not user:
        await update.message.reply_text("Please register first using /start.")
        return

    # Map input day to weekday (Monday=0, Friday=4)
    day_name_map = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 'friday': 4}

    if not context.args or context.args[0].lower() not in day_name_map:
        await update.message.reply_text("Usage: `/menu_day <day>`. Choose from: Monday, Tuesday, Wednesday, Thursday, Friday.")
        return

    target_day_name = context.args[0].title()
    target_day_weekday = day_name_map[target_day_name.lower()]
    today_weekday = datetime.now().weekday()
    
    today = datetime.now().date()
    
    if target_day_weekday >= today_weekday:
        days_to_add = target_day_weekday - today_weekday
    else:
        days_to_add = 7 - today_weekday + target_day_weekday

    target_date = today + timedelta(days=days_to_add)
    target_date_str = target_date.strftime('%Y-%m-%d')
    
    menu_data = load_menu_data()
    
    if not menu_data or not menu_data.get('week_data'):
        await update.message.reply_text("Menu data is not available. Admin must run /refetch_menu.")
        return

    target_menu = next((day for day in menu_data['week_data'] if day['date'] == target_date_str), None)
    
    if not target_menu or not target_menu['categories']:
        await update.message.reply_text(f"No menu found for {target_day_name} ({target_date_str}).")
        return

    header_message = f"*🗓️ Mensa Menu for {target_menu['day']}, {target_menu['date']}*\n"
    await update.message.reply_text(header_message, parse_mode="Markdown")

    user_status = user['status']
    
    for category in target_menu['categories']:
        category_message = f"--- *{category['name']}* ---\n"
        
        for meal in category['meals']:
            eligibility = is_meal_eligible(meal, user)
            
            category_message += await format_meal_message(meal, user_status, eligibility) + "\n\n"

        await update.message.reply_text(category_message, parse_mode="Markdown")
    
async def lookup_allergen(update: Update, context):
    """Looks up allergen/additive text based on code."""
    if context.args and context.args[0].lower() in ['list_allergens', '/list_allergens']:
        await list_allergens(update, context)
        return

    if not context.args:
        await update.message.reply_text("Usage: `/lookup_allergen <code/number>`. E.g., `/lookup_allergen 21a`")
        return

    code = context.args[0].strip().lower()
    description = ALLERGEN_LOOKUP.get(code)

    if description:
        await update.message.reply_text(f"Code *{code.upper()}*: {description}", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Allergen/additive code *{code.upper()}* not found.")

async def mute_notifications(update: Update, context):
    """Toggles the mute status for the user."""
    user_id = str(update.effective_user.id)
    all_users = load_user_data()
    
    if user_id in all_users:
        is_muted = not all_users[user_id]['is_muted']
        all_users[user_id]['is_muted'] = is_muted
        save_user_data(all_users)
        status = "muted" if is_muted else "unmuted"
        await update.message.reply_text(f"Notifications are now *{status}*.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Please register first using /start.")

async def help_command(update: Update, context):
    """Sends a message when the command /help is issued."""
    help_text = (
        "*Available Commands:*\n"
        "1. */start* - Begin user registration (requires password).\n"
        "2. */status* - View your profile, prices, and preferences.\n"
        "3. */redo_survey* - Update your dietary and allergy preferences.\n"
        "4. */menu* - Show today's full menu.\n"
        "5. */menu_day <day>* - Show menu for a specific day (e.g., `/menu_day Monday`).\n"
        "6. */lookup_allergen <code>* - Get the description for an allergen code (e.g., `/lookup_allergen 30`).\n"
        "7. */list_allergens* - Show the full list of codes and descriptions.\n"
        "8. */notify* - Start the process to set a new meal keyword alert.\n"
        "9. */show_notifications* - List your active meal alerts.\n"
        "10. */delete_notification <ID>* - Remove a meal alert.\n"
        "11. */mute* - Toggle meal availability notifications on/off.\n"
        "12. */recheck* - Reruns the notification check against current menu (use after setting a new alert).\n"
    )
    if is_admin(update.effective_user.id):
        help_text += "\n*Admin Commands:*\n"
        help_text += "12. */refetch_menu* - Manually run the scraper and check notifications.\n"
        help_text += "13. */list_users* - List all registered users and their details.\n"
        help_text += "15. */menu_stats* - Returns statistics on the current menu data (date range, meal counts, diet breakdown).\n"
        help_text += "16. */list_users* - List all registered users and their details.\n"
        help_text += "17. */delete_user* <ID> - Remove a user.\n"
        help_text += "18. */stop_bot* - Gracefully stop the bot.\n"
        
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def start_notify_meal(update: Update, context) -> int:
    """Starts the conversation for setting a new meal notification."""
    user_id = str(update.effective_user.id)
    if user_id not in load_user_data():
        await update.message.reply_text("Please register first using /start.")
        return ConversationHandler.END

    await update.message.reply_text(
        "What *keyword* should I look for in a meal name? (e.g., `Pasta`, `Falafel`, `Lachs`)\n"
        "Enter the keyword now:",
        parse_mode="Markdown"
    )
    return NOTIFY_KEYWORD

async def find_first_occurrence(keyword: str) -> str | None:
    """Finds the date of the first occurrence of the keyword in the menu data from today onwards."""
    menu_data = load_menu_data()
    if not menu_data or not menu_data.get('week_data'):
        return None

    today_date = datetime.now().date()
    keyword_lower = keyword.lower()
    
    for day in menu_data['week_data']:
        meal_date_obj = datetime.strptime(day['date'], '%Y-%m-%d').date()
        
        if meal_date_obj >= today_date:
            for category in day['categories']:
                 for meal in category['meals']:
                     if keyword_lower in meal['name'].lower():
                         return day['date']
                         
    return None

async def get_notify_keyword(update: Update, context) -> int:
    """Saves the meal keyword and checks the menu for the first future occurrence."""
    user_id = str(update.effective_user.id)
    keyword = update.message.text.strip()
    
    all_users = load_user_data()
    user = all_users[user_id]
    if not user.get('notifications'):
        user['notifications'] = {}

    current_ids = [int(k) for k in user['notifications'].keys()]
    new_id = str(max(current_ids) + 1 if current_ids else 1)

    user['notifications'][new_id] = {
        'keyword': keyword, 
        'triggered_date': None,
        'reminder_set': False,
        'active_for_future': True 
    }
    save_user_data(all_users)
    
    await update.message.reply_text(
        f"✅ Notification *'{keyword}'* (ID: {new_id}) added. Checking menu instantly...",
        parse_mode="Markdown"
    )

    first_occurrence_date = await find_first_occurrence(keyword)
    
    if first_occurrence_date:
        date_obj = datetime.strptime(first_occurrence_date, '%Y-%m-%d')
        date_display = date_obj.strftime('%A, %b %d')

        notification_message = (
            f"🎉 *MEAL FOUND!* 🎉\n"
            f"Your keyword *{keyword}* is on the menu on:\n"
            f"🗓️ *Date:* {date_display}\n\n"
            "What would you like to do next?"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🔔 Set 10 AM Reminder", callback_data=f"{REMINDER_PREFIX}SET:{new_id}:{first_occurrence_date}"),
                InlineKeyboardButton("❌ Don't Set Reminder", callback_data=f"{REMINDER_PREFIX}NO:{new_id}:{first_occurrence_date}")
            ],
            [
                InlineKeyboardButton("Keep Active for Future ✅", callback_data=f"{KEYWORD_FOUND_PREFIX}KEEP:{new_id}"),
                InlineKeyboardButton("Delete Alert Now 🗑️", callback_data=f"{KEYWORD_FOUND_PREFIX}DELETE:{new_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        user['notifications'][new_id]['triggered_date'] = first_occurrence_date
        save_user_data(all_users)
        
        await update.message.reply_text(
            text=notification_message, 
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        
    else:
        await update.message.reply_text(
            f"🔍 Keyword *'{keyword}'* not found in the current menu.\n"
            f"The alert is active and will check the next time the menu is updated (every Monday morning).",
            parse_mode="Markdown"
        )
        
    return ConversationHandler.END

async def show_notifications(update: Update, context):
    """Shows active meal notifications."""
    user_id = str(update.effective_user.id)
    user = load_user_data().get(user_id)
    
    if not user:
        await update.message.reply_text("Please register first using /start.")
        return
    
    notifications = user.get('notifications', {})
    if not notifications:
        await update.message.reply_text("You have no active meal notifications.")
        return

    message = "*Active Meal Notifications:*\n"
    for idx, notif in notifications.items():
        keyword = notif['keyword']
        triggered_date = notif.get('triggered_date', 'Never')
        reminder_status = "🔔 Reminder SET" if notif.get('reminder_set') else "❌ No Reminder"
        future_status = "✅ Active for Future" if notif.get('active_for_future') else "⚠️ Disabled for Future"
        
        status_line = ""
        if triggered_date and triggered_date != 'Never':
            date_obj = datetime.strptime(triggered_date, '%Y-%m-%d').date()
            if date_obj < datetime.now().date():
                status_line = "(Last Occurred: {date}. Waiting for new menu.)"
            else:
                status_line = f"({triggered_date} - {reminder_status})"

        
        message += (
            f"*{idx}.* `{keyword}` {status_line}\n"
            f"  -> *Status:* {future_status}\n"
        )
    
    message += "\nUse `/delete_notification <ID>` to remove one."
    await update.message.reply_text(message, parse_mode="Markdown")

async def delete_notification(update: Update, context):
    """Deletes a meal notification by ID."""
    user_id = str(update.effective_user.id)
    all_users = load_user_data()
    user = all_users.get(user_id)

    if not user:
        await update.message.reply_text("Please register first using /start.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/delete_notification <ID>`. Use `/show_notifications` to find the ID.")
        return

    target_id = context.args[0].strip()
    
    if target_id in user.get('notifications', {}):
        keyword = user['notifications'][target_id]['keyword']
        del user['notifications'][target_id]
        save_user_data(all_users)
        await update.message.reply_text(f"✅ Notification for *'{keyword}'* (ID: {target_id}) deleted.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Notification ID *{target_id}* not found. Use `/show_notifications` to see active IDs.")

async def default_handler(update: Update, context):
    """Responds to unrecognized commands or non-conversation text."""
    user_message = update.message.text
    logger.info(f"Unrecognized message received from {update.effective_user.id}: {user_message}")

    await update.message.reply_text(
        f"Sorry, I don't recognize the command or keyword: *{user_message}*.\n"
        "Please use a command starting with `/` or try `/help` for a list of available commands.",
        parse_mode="Markdown"
    )
    
async def handle_notification_query(update: Update, context):
    """Handles the button press for managing triggered notifications and reminders."""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    user_id = str(query.from_user.id)
    all_users = load_user_data()
    user = all_users.get(user_id)
    
    if not user:
        await query.edit_message_text("Error: User data not found.", reply_markup=None)
        return

    if callback_data.startswith(REMINDER_PREFIX):
        parts = callback_data.split(':')
        action = parts[1]
        notif_id = parts[2]
        meal_date = parts[3]
        
        if notif_id not in user.get('notifications', {}):
            await query.edit_message_text("Notification is invalid or already processed.", reply_markup=None)
            return

        keyword = user['notifications'][notif_id]['keyword']
        
        if user['notifications'][notif_id]['triggered_date'] != meal_date:
            await query.edit_message_text(f"Reminder status was already set for {meal_date}.", reply_markup=None)
            return

        if action == 'SET':
            user['notifications'][notif_id]['reminder_set'] = True
            save_user_data(all_users)
            
            await query.edit_message_text(
                f"🔔 Reminder set for *'{keyword}'* on {meal_date} at 10:00 AM. \n"
                "You can still choose to keep this keyword active for future menu checks below.",
                parse_mode="Markdown"
            )
        else: # action == 'NO'
            user['notifications'][notif_id]['reminder_set'] = False
            save_user_data(all_users)
            
            await query.edit_message_text(
                f"❌ No reminder set for *'{keyword}'* on {meal_date}. \n"
                "You can still choose to keep this keyword active for future menu checks below.",
                parse_mode="Markdown"
            )
        
        keyboard = query.message.reply_markup.inline_keyboard
        new_keyboard = InlineKeyboardMarkup(keyboard[1:]) 
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        return

    if callback_data.startswith(KEYWORD_FOUND_PREFIX):
        parts = callback_data.split(':')
        action = parts[1]
        notif_id = parts[2]
        
        if notif_id not in user.get('notifications', {}):
            await query.edit_message_text("Notification is invalid or already deleted.", reply_markup=None)
            return
            
        keyword = user['notifications'][notif_id]['keyword']
        
        if action == 'KEEP':
            user['notifications'][notif_id]['active_for_future'] = True
            save_user_data(all_users)
            
            await query.edit_message_text(
                f"✅ Alert for keyword *'{keyword}'* (ID: {notif_id}) will remain **active** for all future menu updates.",
                parse_mode="Markdown",
                reply_markup=None
            )
            
        else: # DELETE
            del user['notifications'][notif_id]
            save_user_data(all_users)
            
            await query.edit_message_text(
                f"🗑️ Alert for keyword *'{keyword}'* (ID: {notif_id}) has been **deleted**.",
                parse_mode="Markdown",
                reply_markup=None
            )
            
        return
    
async def send_daily_reminders(context):
    """
    Sends reminders to users who have set one for today's menu item.
    Includes a check to ensure the meal still exists in the menu file.
    """
    logger.info("--- TRIGGERED DAILY REMINDER CHECK ---")
    menu_data = load_menu_data()
    all_users = load_user_data()
    today_date_str = datetime.now().strftime('%Y-%m-%d')
    users_to_save = False

    today_meals = {}
    
    today_menu = next((day for day in menu_data.get('week_data', []) if day['date'] == today_date_str), None)
    
    if today_menu:
        for category in today_menu['categories']:
            for meal in category['meals']:
                # Store keywords in a set for fast lookup
                today_meals[meal['name'].lower()] = True 
    else:
        logger.warning(f"No menu data found for today ({today_date_str}). Skipping reminders.")
        return


    for user_id_str, user in all_users.items():
        user_id_int = int(user_id_str)
        
        for notif_id, notif in user.get('notifications', {}).items():
            
            # Check if reminder is set for today AND hasn't been sent yet
            if (notif['reminder_set'] and 
                notif['triggered_date'] == today_date_str and 
                not notif.get('reminder_sent', False)):

                keyword = notif['keyword']
                keyword_lower = keyword.lower()
                meal_found_today = False
                
                # Check if the keyword still exists in today's menu
                for meal_name in today_meals.keys():
                    if keyword_lower in meal_name:
                        meal_found_today = True
                        break
                        
                if meal_found_today:
                    reminder_message = (
                        f"⏰ *DAILY MEAL REMINDER!* ⏰\n"
                        f"Your meal, *'{keyword}'*, is on the menu *today* ({today_date_str}).\n"
                        f"Use /menu to view the details!"
                    )
                    
                    try:
                        await context.application.bot.send_message(
                            chat_id=user_id_int, 
                            text=reminder_message, 
                            parse_mode="Markdown"
                        )
                        user['notifications'][notif_id]['reminder_sent'] = True 
                        users_to_save = True
                    except Exception as e:
                        logger.error(f"Error sending reminder to {user_id_str} for {keyword}: {e}")
                        
                else:
                    sad_message = (
                        f"😢 *MENU CHANGE ALERT!* 😢\n"
                        f"We're sorry! The meal containing your keyword *'{keyword}'*, which was scheduled for today ({today_date_str}), appears to have been **removed** from the menu.\n"
                        f"Please check the updated /menu for today's final offerings."
                    )
                    
                    try:
                        await context.application.bot.send_message(
                            chat_id=user_id_int, 
                            text=sad_message, 
                            parse_mode="Markdown"
                        )
                        user['notifications'][notif_id]['reminder_sent'] = True 
                        user['notifications'][notif_id]['reminder_set'] = False 
                        user['notifications'][notif_id]['triggered_date'] = None # Reset for future checks
                        users_to_save = True
                    except Exception as e:
                        logger.error(f"Error sending sad notification to {user_id_str} for {keyword}: {e}")

    if users_to_save:
        save_user_data(all_users)
    logger.info("--- DAILY REMINDER CHECK COMPLETE ---")

def main():
    """Start the bot."""
    if not TOKEN or not ADMIN_ID or not REGISTRATION_PASSWORD:
        logger.error("FATAL: Missing configuration in .env file.")
        return
        
    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init_notify)
        .post_stop(post_stop_notify)
        .build()
    )
    
    job_queue = application.job_queue 

    async def scheduled_scrape_and_notify(context):
        """Scheduler handler: Runs scraper and triggers notification check."""
        logger.info("--- TRIGGERED SCHEDULED WEEKLY SCRAPE ---")
        run_scraper()
        
        await check_and_notify_users(context.application)
        logger.info("--- SCHEDULED SCRAPE COMPLETE ---")

    job_queue.run_daily(
        scheduled_scrape_and_notify, 
        time=time(hour=6, minute=0, second=0),
        days=(0, 1, 2, 3, 4), # Run every weekday morning (Mon-Fri)
        name='Daily Rolling Menu Fetch'
    )
    
    job_queue.run_daily(
        send_daily_reminders, 
        time=time(hour=10, minute=0, second=0),
        days=(0, 1, 2, 3, 4),
        name='Daily Meal Reminder'
    )

    menu_file_missing = not os.path.exists(MENU_DATA_FILE)
    menu_is_stale = is_last_day_of_menu() # Check if today is the last day of the existing menu

    if menu_file_missing:
        logger.info("Menu file not found. Running initial scraper...")
        run_scraper()
    elif menu_is_stale:
        logger.info("Menu data is stale (on the last day or later). Running scraper refresh...")
        run_scraper()
    else:
        logger.info("Menu data is present and current. Skipping startup scrape.")
    
    # Core Utility Commands
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", show_user_status))
    application.add_handler(CommandHandler("menu", show_today_menu))
    application.add_handler(CommandHandler("menu_day", get_menu_day))
    application.add_handler(CommandHandler("mute", mute_notifications))
    application.add_handler(CommandHandler("lookup_allergen", lookup_allergen))
    application.add_handler(CommandHandler("list_allergens", list_allergens))
    application.add_handler(CommandHandler("show_notifications", show_notifications))
    application.add_handler(CommandHandler("delete_notification", delete_notification))
    application.add_handler(CommandHandler("recheck", recheck_notifications))

    # Admin Commands
    application.add_handler(CommandHandler("refetch_menu", refetch_menu))
    application.add_handler(CommandHandler("list_users", list_users))
    application.add_handler(CommandHandler("delete_user", delete_user))
    application.add_handler(CommandHandler("stop_bot", stop_bot))
    application.add_handler(CommandHandler("menu_stats", menu_stats))

    # Conversation Handlers
    
    # Registration/Survey
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_registration)],
        states={
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_password)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_status)],
            DIET_PREFS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_diet_prefs)],
            ALLERGY_PREFS: [MessageHandler(filters.TEXT & filters.Regex(r'^(?!/list_allergens).*$') & ~filters.COMMAND, get_allergy_prefs),
                            CommandHandler("list_allergens", list_allergens)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(registration_handler)

    # Redo Survey (skip name)
    redo_survey_handler = ConversationHandler(
        entry_points=[CommandHandler("redo_survey", redo_survey)],
        states={
            DIET_PREFS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_diet_prefs)],
            ALLERGY_PREFS: [MessageHandler(filters.TEXT & filters.Regex(r'^(?!/list_allergens).*$') & ~filters.COMMAND, get_allergy_prefs),
                            CommandHandler("list_allergens", list_allergens)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END,
        }
    )
    application.add_handler(redo_survey_handler)

    notify_handler = ConversationHandler(
        entry_points=[CommandHandler("notify", start_notify_meal)],
        states={
            NOTIFY_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_notify_keyword)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(notify_handler)
    
    application.add_handler(
        CallbackQueryHandler(handle_notification_query, pattern=f"^{DELETE_NOTIF_PREFIX}") # Match any callback starting with the prefix
    )
    
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, default_handler)
    )

    print("\nBot is running... press Ctrl-C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
# MensaBot

MensaBot is a Telegram bot designed to fetch and display the weekly menu for the **Mensa HU Oase Adlershof** directly from the Studierendenwerk Berlin website.

It features:

* **Secure User Registration** with a shared password.
* **Personalized Pricing** based on user status (Student, Employee, Guest).
* **Personalized Menu Flagging** for dietary and sustainability preferences (Vegan, Low CO2, Allergens).
* **Advanced Meal Notifications** with per‑meal reminders and future tracking.
* **Admin Controls** for maintenance and user management.

---

## 1. Prerequisites & Installation

To run this bot, you must have **Python 3.8+** installed and install all libraries listed in `requirements.txt`.

### 1.1 Create a Virtual Environment (Recommended)

```bash
python -m venv venv

# Activate on Windows
./venv/Scripts/activate

# Activate on macOS / Linux
source venv/bin/activate
```

### 1.2 Install Dependencies

Ensure your `requirements.txt` includes: `requests`, `beautifulsoup4`, `python-telegram-bot`, `python-dotenv`.

```bash
pip install -r requirements.txt
```

### 1.3. Docker Installation

To run the bot using a docker container via compose, you must have Docker Engine and Docker Compose installed on your host machine.

The easiest way to obtain both is by installing Docker Desktop (for Windows, macOS) or following the official installation guides for Docker Engine and Docker Compose (for Linux).

---

## 2. Bot Configuration (The `.env` File)

All configuration variables, including sensitive data and Mensa details, are loaded from a `.env` file you must create in the project root.

### 2.1 Required Values

| Value                     | How to Get It                                                   |
| ------------------------- | --------------------------------------------------------------- |
| **TELEGRAM_BOT_TOKEN**    | Create a new bot with BotFather on Telegram.                    |
| **ADMIN_ID**              | Message `@userinfobot` on Telegram to get your numeric User ID. |
| **REGISTRATION_PASSWORD** | Create any secret password required for new user registration.  |

### 2.2 Create and Populate `.env`

```python
# --- Core Mensa Configuration ---
MENSA_ID="191"
AJAX_URL="https://www.stw.berlin/xhr/speiseplan-wochentag.html"
MENSA_NAME="HU Oase Adlershof"

# --- File Paths ---
LOOKUP_FILE="lookup_tables.json"
MENU_DATA_FILE="mensa_menu.json"
USER_DATA_FILE="user_data.json"

# --- Security Credentials (REQUIRED) ---
TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN_FROM_BOTFATHER"
ADMIN_ID="YOUR_ADMIN_TELEGRAM_USER_ID"
REGISTRATION_PASSWORD="Your-Secret-Registration-Password-Here"
```

---

## 3. Scraper Data Setup (`lookup_tables.json`)

The bot requires a lookup table of allergens, additives, and icon mappings to correctly interpret meal data. Ensure `lookup_tables.json` exists in the project root and contains the full mapping used by the scraper.

---

## 4. First-Time Startup & Operation

### 4.1 Important First Step (Admin Contact)

Before starting the bot, the user whose ID matches `ADMIN_ID` must send **any message** to the bot. This creates the chat session required for startup notifications.

### 4.2 Run the Bot

```bash
python mensabot.py
```

On startup, the bot will:

* Send a confirmation message to the admin.
* Automatically run `mensa_scraper.py` to fetch the weekly menu (if missing or outdated).
* Schedule a fresh menu scrape every Monday–Friday morning at 06:00.
* Schedule a meal‑alert (and recheck availability) check Monday–Friday at 10:00.

### 4.3. Running the Bot via Docker Compose

Use docker compose to build the image, create the necessary volume, and start the bot service in the background.

```bash
# 1. Build the image and start the container
docker compose up --build -d

# 2. Check the logs to ensure successful startup and menu fetch
docker compose logs -f mensabot
```

Upon startup, the bot will:

1. Send a startup confirmation message to the ADMIN_ID.
2. Automatically run the scraper to fetch the rolling 7-day menu (if data is missing or stale).
3. Schedule the daily menu refresh and reminder jobs.

---

## 5. Bot Commands

### User Commands

| Command                   | Access           | Description                                    |
| ------------------------- | ---------------- | ---------------------------------------------- |
| `/start`                  | All              | Begin registration (requires password).        |
| `/status`                 | Registered Users | Show your profile, pricing, and preferences.   |
| `/redo_survey`            | Registered Users | Update dietary preferences and allergy codes.  |
| `/menu`                   | Registered Users | Show today’s menu with personalized flags.     |
| `/menu_day <day>`         | Registered Users | Show the menu for a chosen weekday.            |
| `/notify`                 | Registered Users | Start setup for a new meal‑keyword alert.      |
| `/show_notifications`     | Registered Users | List all active meal alerts.                   |
| `/delete_notification`    | Registered Users | Remove a specific alert by ID.                 |
| `/recheck`                | Registered Users | Manually check alerts against the latest menu. |
| `/mute`                   | Registered Users | Toggle all notifications on/off.               |
| `/lookup_allergen <code>` | Registered Users | Show description for an allergen code.         |
| `/list_allergens`         | Registered Users | Show the full allergen/additive list.          |
| `/help`                   | All              | Display available commands.                    |

### Admin Commands

| Command             | Description                                                 |
| ------------------- | ----------------------------------------------------------- |
| `/stop_bot`         | Shut down the bot.                                          |
| `/refetch_menu`     | Re-scrape the menu and run notification checks immediately. |
| `/menu_stats`       | Show statistics on the current menu data.                   |
| `/list_users`       | Display all registered users.                               |
| `/delete_user <ID>` | Remove a user by Telegram ID.                               |

---

## 6. Contributing

Contributions are welcome! If you'd like to help improve MensaBot, feel free to:

* Open issues for bugs, feature requests, or questions
* Submit pull requests with clear descriptions

Before contributing, please:

1. Fork the repository
2. Create a feature branch (git checkout -b feature-name)
3. Commit your changes (git commit -m "Description")
4. Push to your fork and submit a pull request

---

## 7. License

This project is open source and licensed under the MIT License, a permissive and widely used license that allows modification, distribution, and private or commercial use as long as the original license notice is included.
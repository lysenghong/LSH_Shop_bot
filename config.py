import os

# Auto-load .env file if present
env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# Telegram Bot Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "1017751722").split(",") if x.strip()]

# Supplier Configuration (bulkmail.shop)
SUPPLIER_BASE_URL = os.getenv("SUPPLIER_BASE_URL", "https://bulkmail.shop")
DEFAULT_SUPPLIER_API_KEY = os.getenv("SUPPLIER_API_KEY", "")

# Bakong KHQR Configuration
BAKONG_TOKEN = os.getenv("BAKONG_TOKEN", "")
BAKONG_ACCOUNT_ID = os.getenv("BAKONG_ACCOUNT_ID", "ngim_bunrith1@bkrt")
BAKONG_MERCHANT_NAME = os.getenv("BAKONG_MERCHANT_NAME", "BUNRITH NGIM")
BAKONG_MERCHANT_CITY = os.getenv("BAKONG_MERCHANT_CITY", "Phnom Penh")

# Database & API Server Config
DB_FILE = os.getenv("DB_FILE", "lsh_shop.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("PORT", "8085"))

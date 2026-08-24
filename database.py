import secrets
import time
import aiosqlite
from config import DB_FILE, DEFAULT_SUPPLIER_API_KEY, BAKONG_TOKEN, BAKONG_ACCOUNT_ID, BAKONG_MERCHANT_NAME

def generate_api_key():
    return f"lsh_sk_{secrets.token_hex(16)}"

def format_price(price: float) -> str:
    """Formats float price/balance with micro-precision support for values like $4.995, $1.00002, $0.00001 without rounding."""
    if price is None or price == 0:
        return "$0.00"
    val = float(price)
    formatted = f"{val:.6f}".rstrip('0')
    if formatted.endswith('.'):
        formatted += '00'
    elif len(formatted.split('.')[1]) == 1:
        formatted += '0'
    return f"${formatted}"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0.0,
                api_key TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_product_id TEXT NOT NULL,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                description TEXT DEFAULT '',
                status INTEGER DEFAULT 1,
                api_enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Ensure api_enabled column exists if upgrading existing DB
        try:
            await db.execute("ALTER TABLE products ADD COLUMN api_enabled INTEGER DEFAULT 1")
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                supplier_order_id TEXT,
                quantity INTEGER DEFAULT 1,
                unit_price REAL NOT NULL,
                total_price REAL NOT NULL,
                result_data TEXT DEFAULT '',
                status TEXT DEFAULT 'COMPLETED',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                md5_hash TEXT UNIQUE NOT NULL,
                qr_code_str TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        defaults = {
            "supplier_api_key": DEFAULT_SUPPLIER_API_KEY,
            "bakong_token": BAKONG_TOKEN,
            "bakong_account_id": BAKONG_ACCOUNT_ID,
            "bakong_merchant_name": BAKONG_MERCHANT_NAME
        }
        for k, v in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        await db.commit()

async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()

async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            user = await cursor.fetchone()
            if user:
                if username != user["username"] or first_name != user["first_name"]:
                    await db.execute(
                        "UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
                        (username, first_name, telegram_id)
                    )
                    await db.commit()
                return dict(user)
        
        api_key = generate_api_key()
        await db.execute(
            "INSERT INTO users (telegram_id, username, first_name, balance, api_key) VALUES (?, ?, ?, 0.0, ?)",
            (telegram_id, username, first_name, api_key)
        )
        await db.commit()
        
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            new_user = await cursor.fetchone()
            return dict(new_user)

async def get_user_by_api_key(api_key: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_user_by_telegram_id(telegram_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_user_by_db_id(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_user_by_username(username: str):
    clean_username = username.lstrip("@").strip()
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (clean_username,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def find_user_by_any(identifier: str):
    clean_id = identifier.strip()
    if clean_id.startswith("#") and clean_id[1:].isdigit():
        return await get_user_by_db_id(int(clean_id[1:]))
    if clean_id.isdigit():
        user = await get_user_by_telegram_id(int(clean_id))
        if not user:
            user = await get_user_by_db_id(int(clean_id))
        return user
    return await get_user_by_username(clean_id)

async def regenerate_api_key(telegram_id: int) -> str:
    new_key = generate_api_key()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET api_key = ? WHERE telegram_id = ?", (new_key, telegram_id))
        await db.commit()
    return new_key

async def update_user_balance(user_id: int, amount: float) -> float:
    """Atomically updates user balance with strict overdraft prevention."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA busy_timeout = 5000;")
        if amount < 0:
            cursor = await db.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ? AND (balance + ? >= -0.000001)",
                (amount, user_id, amount)
            )
            if cursor.rowcount == 0:
                await db.rollback()
                raise ValueError("Insufficient balance for operation")
        else:
            await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0.0

async def get_active_products():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE status = 1 ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_api_active_products():
    """Returns products that are active AND enabled for API purchasing."""
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE status = 1 AND api_enabled = 1 ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_all_products():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_product_by_id(product_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE id = ?", (product_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def add_product(supplier_product_id: str, name: str, price: float, description: str = "", api_enabled: int = 1):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "INSERT INTO products (supplier_product_id, name, price, description, status, api_enabled) VALUES (?, ?, ?, ?, 1, ?)",
            (str(supplier_product_id), name, float(price), description, int(api_enabled))
        )
        await db.commit()
        return cursor.lastrowid

async def update_product_price(product_id: int, new_price: float):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE products SET price = ? WHERE id = ?", (float(new_price), product_id))
        await db.commit()

async def toggle_product_status(product_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE products SET status = CASE WHEN status = 1 THEN 0 ELSE 1 END WHERE id = ?", (product_id,))
        await db.commit()

async def toggle_product_api_status(product_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE products SET api_enabled = CASE WHEN api_enabled = 1 THEN 0 ELSE 1 END WHERE id = ?", (product_id,))
        await db.commit()

async def delete_product(product_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await db.commit()

async def create_order(user_id: int, product_id: int, supplier_order_id: str, quantity: int, unit_price: float, total_price: float, result_data: str, status: str = "COMPLETED"):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            """INSERT INTO orders (user_id, product_id, supplier_order_id, quantity, unit_price, total_price, result_data, status) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, product_id, supplier_order_id, quantity, unit_price, total_price, result_data, status)
        )
        await db.commit()
        return cursor.lastrowid

async def get_user_orders(user_id: int, limit: int = 20):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT o.*, p.name as product_name 
               FROM orders o 
               LEFT JOIN products p ON o.product_id = p.id 
               WHERE o.user_id = ? 
               ORDER BY o.id DESC LIMIT ?""", 
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def create_deposit(user_id: int, amount: float, md5_hash: str, qr_code_str: str):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "INSERT INTO deposits (user_id, amount, md5_hash, qr_code_str, status) VALUES (?, ?, ?, ?, 'PENDING')",
            (user_id, amount, md5_hash, qr_code_str)
        )
        await db.commit()
        return cursor.lastrowid

async def get_pending_deposits():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT d.*, u.telegram_id FROM deposits d JOIN users u ON d.user_id = u.id WHERE d.status = 'PENDING'") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_deposit_by_id(deposit_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def mark_deposit_paid(deposit_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE deposits SET status = 'SUCCESS', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (deposit_id,))
        await db.commit()

async def cancel_deposit(deposit_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE deposits SET status = 'CANCELLED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (deposit_id,))
        await db.commit()

async def get_all_users_count():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_sales_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*), COALESCE(SUM(total_price), 0.0) FROM orders WHERE status = 'COMPLETED'") as cursor:
            row = await cursor.fetchone()
            total_orders = row[0] if row else 0
            total_sales = row[1] if row else 0.0
            return {"total_orders": total_orders, "total_sales": total_sales}

import secrets
import time
import os
import re
import aiosqlite
from config import DB_FILE, DATABASE_URL, DEFAULT_SUPPLIER_API_KEY, BAKONG_TOKEN, BAKONG_ACCOUNT_ID, BAKONG_MERCHANT_NAME

# Optional asyncpg import for PostgreSQL support
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

PG_POOL = None

def get_database_url() -> str:
    url = DATABASE_URL or os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def is_postgres() -> bool:
    return bool(get_database_url()) and HAS_ASYNCPG

def generate_api_key():
    return f"lsh_sk_{secrets.token_hex(16)}"

def format_price(price: float) -> str:
    """Formats float price/balance with micro-precision support."""
    if price is None or price == 0:
        return "$0.00"
    val = float(price)
    formatted = f"{val:.6f}".rstrip('0')
    if formatted.endswith('.'):
        formatted += '00'
    elif len(formatted.split('.')[1]) == 1:
        formatted += '0'
    return f"${formatted}"

def _convert_query(query: str) -> str:
    """Converts SQLite query syntax to PostgreSQL syntax ($1, $2, ...)."""
    # Replace SQLite ON CONFLICT constructs
    query = re.sub(
        r"INSERT\s+OR\s+IGNORE\s+INTO\s+settings\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
        r"INSERT INTO settings (\1) VALUES (\2) ON CONFLICT (key) DO NOTHING",
        query, flags=re.IGNORECASE
    )
    query = re.sub(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+settings\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
        r"INSERT INTO settings (\1) VALUES (\2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        query, flags=re.IGNORECASE
    )
    
    # Convert ? placeholders to $1, $2, $3...
    parts = query.split("?")
    if len(parts) > 1:
        new_query = parts[0]
        for i, part in enumerate(parts[1:], 1):
            new_query += f"${i}" + part
        query = new_query

    return query

async def init_db():
    global PG_POOL
    pg_url = get_database_url()
    if pg_url and HAS_ASYNCPG:
        print(f"Connecting to Cloud PostgreSQL Database: {pg_url.split('@')[-1] if '@' in pg_url else 'PostgreSQL'}...")
        try:
            PG_POOL = await asyncpg.create_pool(dsn=pg_url, min_size=1, max_size=10, timeout=15.0)
            async with PG_POOL.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT UNIQUE NOT NULL,
                        username TEXT,
                        first_name TEXT,
                        balance DOUBLE PRECISION DEFAULT 0.0,
                        api_key TEXT UNIQUE NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS products (
                        id SERIAL PRIMARY KEY,
                        supplier_product_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        price DOUBLE PRECISION NOT NULL,
                        description TEXT DEFAULT '',
                        status INTEGER DEFAULT 1,
                        api_enabled INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS orders (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        product_id INTEGER NOT NULL,
                        supplier_order_id TEXT,
                        quantity INTEGER DEFAULT 1,
                        unit_price DOUBLE PRECISION NOT NULL,
                        total_price DOUBLE PRECISION NOT NULL,
                        result_data TEXT DEFAULT '',
                        status TEXT DEFAULT 'COMPLETED',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(id),
                        FOREIGN KEY(product_id) REFERENCES products(id)
                    );
                    CREATE TABLE IF NOT EXISTS deposits (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        amount DOUBLE PRECISION NOT NULL,
                        md5_hash TEXT UNIQUE NOT NULL,
                        qr_code_str TEXT,
                        status TEXT DEFAULT 'PENDING',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    );
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                """)
                
                defaults = {
                    "supplier_api_key": DEFAULT_SUPPLIER_API_KEY,
                    "bakong_token": BAKONG_TOKEN,
                    "bakong_account_id": BAKONG_ACCOUNT_ID,
                    "bakong_merchant_name": BAKONG_MERCHANT_NAME
                }
                for k, v in defaults.items():
                    await conn.execute(
                        "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING",
                        k, str(v or "")
                    )
            print("Cloud PostgreSQL Database initialized successfully!")
            return
        except Exception as e:
            print(f"Warning: Failed to initialize PostgreSQL ({e}). Falling back to local SQLite.")

    # SQLite fallback
    print(f"Using SQLite database: {DB_FILE}")
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
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, str(v or "")))
        await db.commit()

async def db_fetch_one(query: str, params: tuple = ()):
    if PG_POOL:
        async with PG_POOL.acquire() as conn:
            row = await conn.fetchrow(_convert_query(query), *params)
            return dict(row) if row else None
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

async def db_fetch_all(query: str, params: tuple = ()):
    if PG_POOL:
        async with PG_POOL.acquire() as conn:
            rows = await conn.fetch(_convert_query(query), *params)
            return [dict(r) for r in rows]
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

async def db_execute(query: str, params: tuple = ()):
    if PG_POOL:
        async with PG_POOL.acquire() as conn:
            return await conn.execute(_convert_query(query), *params)
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor

async def db_insert(query: str, params: tuple = (), id_column: str = "id") -> int:
    """Executes INSERT statement and returns newly generated primary key integer ID."""
    if PG_POOL:
        pg_query = _convert_query(query) + f" RETURNING {id_column}"
        async with PG_POOL.acquire() as conn:
            return await conn.fetchval(pg_query, *params)
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.lastrowid

# API & Bot Database Handler Functions

async def get_setting(key: str, default: str = "") -> str:
    row = await db_fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default

async def set_setting(key: str, value: str):
    if PG_POOL:
        await db_execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, str(value))
        )
    else:
        await db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None):
    user = await db_fetch_one("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    if user:
        if username != user.get("username") or first_name != user.get("first_name"):
            await db_execute(
                "UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
                (username, first_name, telegram_id)
            )
        return user

    api_key = generate_api_key()
    await db_execute(
        "INSERT INTO users (telegram_id, username, first_name, balance, api_key) VALUES (?, ?, ?, 0.0, ?)",
        (telegram_id, username, first_name, api_key)
    )
    return await db_fetch_one("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))

async def get_user_by_api_key(api_key: str):
    return await db_fetch_one("SELECT * FROM users WHERE api_key = ?", (api_key,))

async def get_user_by_telegram_id(telegram_id: int):
    return await db_fetch_one("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))

async def get_user_by_db_id(user_id: int):
    return await db_fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

async def get_user_by_username(username: str):
    clean_username = username.lstrip("@").strip()
    return await db_fetch_one("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (clean_username,))

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
    await db_execute("UPDATE users SET api_key = ? WHERE telegram_id = ?", (new_key, telegram_id))
    return new_key

async def update_user_balance(user_id: int, amount: float) -> float:
    """Atomically updates user balance with strict overdraft prevention."""
    if PG_POOL:
        async with PG_POOL.acquire() as conn:
            async with conn.transaction():
                if amount < 0:
                    status_str = await conn.execute(
                        "UPDATE users SET balance = balance + $1 WHERE id = $2 AND (balance + $1 >= -0.000001)",
                        amount, user_id
                    )
                    # Check updated count
                    updated_count = int(status_str.split()[-1]) if status_str else 0
                    if updated_count == 0:
                        raise ValueError("Insufficient balance for operation")
                else:
                    await conn.execute("UPDATE users SET balance = balance + $1 WHERE id = $2", amount, user_id)
                new_bal = await conn.fetchval("SELECT balance FROM users WHERE id = $1", user_id)
                return float(new_bal or 0.0)
    else:
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
                return float(row[0]) if row else 0.0

async def get_active_products():
    return await db_fetch_all("SELECT * FROM products WHERE status = 1 ORDER BY id DESC")

async def get_api_active_products():
    return await db_fetch_all("SELECT * FROM products WHERE status = 1 AND api_enabled = 1 ORDER BY id DESC")

async def get_all_products():
    return await db_fetch_all("SELECT * FROM products ORDER BY id DESC")

async def get_product_by_id(product_id: int):
    return await db_fetch_one("SELECT * FROM products WHERE id = ?", (product_id,))

async def add_product(supplier_product_id: str, name: str, price: float, description: str = "", api_enabled: int = 1):
    return await db_insert(
        "INSERT INTO products (supplier_product_id, name, price, description, status, api_enabled) VALUES (?, ?, ?, ?, 1, ?)",
        (str(supplier_product_id), name, float(price), description, int(api_enabled))
    )

async def update_product_price(product_id: int, new_price: float):
    await db_execute("UPDATE products SET price = ? WHERE id = ?", (float(new_price), product_id))

async def toggle_product_status(product_id: int):
    await db_execute("UPDATE products SET status = CASE WHEN status = 1 THEN 0 ELSE 1 END WHERE id = ?", (product_id,))

async def toggle_product_api_status(product_id: int):
    await db_execute("UPDATE products SET api_enabled = CASE WHEN api_enabled = 1 THEN 0 ELSE 1 END WHERE id = ?", (product_id,))

async def delete_product(product_id: int):
    await db_execute("DELETE FROM products WHERE id = ?", (product_id,))

async def create_order(user_id: int, product_id: int, supplier_order_id: str, quantity: int, unit_price: float, total_price: float, result_data: str, status: str = "COMPLETED"):
    return await db_insert(
        """INSERT INTO orders (user_id, product_id, supplier_order_id, quantity, unit_price, total_price, result_data, status) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, product_id, supplier_order_id, quantity, unit_price, total_price, result_data, status)
    )

async def get_user_orders(user_id: int, limit: int = 20):
    return await db_fetch_all(
        """SELECT o.*, p.name as product_name 
           FROM orders o 
           LEFT JOIN products p ON o.product_id = p.id 
           WHERE o.user_id = ? 
           ORDER BY o.id DESC LIMIT ?""",
        (user_id, limit)
    )

async def create_deposit(user_id: int, amount: float, md5_hash: str, qr_code_str: str):
    return await db_insert(
        "INSERT INTO deposits (user_id, amount, md5_hash, qr_code_str, status) VALUES (?, ?, ?, ?, 'PENDING')",
        (user_id, amount, md5_hash, qr_code_str)
    )

async def get_pending_deposits():
    return await db_fetch_all("SELECT d.*, u.telegram_id FROM deposits d JOIN users u ON d.user_id = u.id WHERE d.status = 'PENDING'")

async def get_deposit_by_id(deposit_id: int):
    return await db_fetch_one("SELECT * FROM deposits WHERE id = ?", (deposit_id,))

async def mark_deposit_paid(deposit_id: int):
    await db_execute("UPDATE deposits SET status = 'SUCCESS', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (deposit_id,))

async def cancel_deposit(deposit_id: int):
    await db_execute("UPDATE deposits SET status = 'CANCELLED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (deposit_id,))

async def get_all_users_count():
    row = await db_fetch_one("SELECT COUNT(*) as count FROM users")
    if not row:
        return 0
    return row.get("count", row.get("count(*)", 0))

async def get_sales_stats():
    row = await db_fetch_one("SELECT COUNT(*) as total_orders, COALESCE(SUM(total_price), 0.0) as total_sales FROM orders WHERE status = 'COMPLETED'")
    if not row:
        return {"total_orders": 0, "total_sales": 0.0}
    return {
        "total_orders": row.get("total_orders", 0),
        "total_sales": float(row.get("total_sales", 0.0) or 0.0)
    }

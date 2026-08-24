import os
import requests
from fastapi import FastAPI, HTTPException, Query, Depends, status
from pydantic import BaseModel
from typing import Optional, List

from database import (
    get_user_by_api_key, get_product_by_id, get_api_active_products,
    update_user_balance, create_order, get_user_orders, get_setting, format_price
)
from supplier_api import buy_supplier_product, fetch_supplier_products
from config import BOT_TOKEN

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(
    title="LSH_Shop API",
    description="Automated API for purchasing mail accounts & digital products",
    version="1.0.0"
)

# Enable CORS for external tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enforce HTTPS Security Headers Middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

app.add_middleware(SecurityHeadersMiddleware)

def send_telegram_notify(telegram_id: int, message: str):
    """Sends asynchronous notification to user's Telegram chat."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": telegram_id,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Failed to send Telegram notify: {e}")

import time
from typing import Optional, List, Dict

# In-memory sliding window rate limiter: Configurable high capacity (Default: 500 req/sec)
API_KEY_RATE_LIMITS: Dict[str, List[float]] = {}
MAX_REQUESTS_PER_SECOND = int(os.getenv("RATE_LIMIT_PER_SEC", "500"))

def enforce_rate_limit(api_key: str):
    """Enforces high-capacity rate limit (500 req/sec) supporting multi-PC multi-thread power buyers."""
    if not api_key:
        return
    now = time.time()
    timestamps = API_KEY_RATE_LIMITS.get(api_key, [])
    # Remove timestamps older than 1 second
    timestamps = [t for t in timestamps if now - t < 1.0]
    
    if len(timestamps) >= MAX_REQUESTS_PER_SECOND:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {MAX_REQUESTS_PER_SECOND} requests per second allowed per API Key."
        )
    timestamps.append(now)
    API_KEY_RATE_LIMITS[api_key] = timestamps

class BuyRequest(BaseModel):
    api_key: str
    product_id: int
    quantity: Optional[int] = 1

@app.get("/health")
async def health_check():
    """Keep-alive health endpoint for 24/7 Render hosting."""
    return {"status": "ok", "service": "LSH_Shop API", "timestamp": os.getenv("PORT", "8085")}

@app.get("/api/v1/user/info")
async def get_user_info(api_key: str = Query(..., description="Your LSH_Shop API Key")):
    enforce_rate_limit(api_key)
    user = await get_user_by_api_key(api_key)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    return {
        "status": "success",
        "telegram_id": user["telegram_id"],
        "username": user["username"],
        "balance": user["balance"],
        "formatted_balance": format_price(user["balance"]),
        "api_key": user["api_key"]
    }

@app.get("/api/v1/products")
async def list_products(api_key: str = Query(..., description="Your LSH_Shop API Key")):
    enforce_rate_limit(api_key)
    user = await get_user_by_api_key(api_key)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    
    # 1. Get products enabled for API
    api_products = await get_api_active_products()
    
    # 2. Fetch live stock from bulkmail.shop
    supp_res = await fetch_supplier_products()
    supp_items = {p["id"]: p for p in supp_res.get("all", [])}
    
    result_list = []
    for p in api_products:
        supp_info = supp_items.get(str(p["supplier_product_id"]), {})
        live_stock = supp_info.get("stock", 999)
        result_list.append({
            "id": p["id"],
            "supplier_product_id": p["supplier_product_id"],
            "name": p["name"],
            "price": p["price"],
            "formatted_price": format_price(p["price"]),
            "stock": live_stock,
            "in_stock": live_stock > 0,
            "description": p["description"]
        })

    return {
        "status": "success",
        "count": len(result_list),
        "products": result_list
    }

@app.get("/api/v1/orders")
async def list_user_orders(api_key: str = Query(..., description="Your LSH_Shop API Key")):
    enforce_rate_limit(api_key)
    user = await get_user_by_api_key(api_key)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    
    orders = await get_user_orders(user["id"])
    return {
        "status": "success",
        "count": len(orders),
        "orders": orders
    }

@app.post("/api/v1/buy")
async def buy_product(req: BuyRequest):
    enforce_rate_limit(req.api_key)
    # 1. Authenticate user via API key
    user = await get_user_by_api_key(req.api_key)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")

    qty = max(1, req.quantity or 1)
    
    # 2. Check product availability
    product = await get_product_by_id(req.product_id)
    if not product or product["status"] != 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found or unavailable")
        
    # Check if enabled for API
    if product.get("api_enabled", 1) != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This product is disabled for API purchasing. Please buy via Telegram Bot."
        )

    unit_price = product["price"]
    total_cost = unit_price * qty
    
    # 3. Check user balance
    if user["balance"] < total_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"Insufficient balance ({format_price(user['balance'])}). Required: {format_price(total_cost)}. Please deposit funds via Telegram Bot."
        )

    # 4. Deduct balance first
    new_bal = await update_user_balance(user["id"], -total_cost)
    
    # 5. Call Supplier API (bulkmail.shop) using Admin API key
    supplier_api_key = await get_setting("supplier_api_key", "")
    success, supplier_ord_id, delivered_data, err_msg = await buy_supplier_product(
        product["supplier_product_id"], qty, supplier_api_key
    )
    
    if not success:
        # Refund user balance if purchase failed
        await update_user_balance(user["id"], total_cost)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Purchase Failed: Product temporary out of stock or processing error. Your balance ({format_price(total_cost)}) has been fully refunded."
        )
        
    # 6. Record successful order in database
    order_id = await create_order(
        user_id=user["id"],
        product_id=product["id"],
        supplier_order_id=supplier_ord_id,
        quantity=qty,
        unit_price=unit_price,
        total_price=total_cost,
        result_data=delivered_data,
        status="COMPLETED"
    )

    # 7. Send receipt Telegram message to user
    items_snippet = "\n".join(delivered_data.split("\n")[:5])
    if len(delivered_data.split("\n")) > 5:
        items_snippet += f"\n... (+{len(delivered_data.split('\n')) - 5} more items)"
        
    msg = (
        f"⚡ <b>New Purchase via API!</b>\n\n"
        f"📦 <b>Item:</b> {product['name']}\n"
        f"🔢 <b>Quantity:</b> {qty}\n"
        f"💵 <b>Total Paid:</b> {format_price(total_cost)}\n"
        f"💳 <b>Remaining Balance:</b> {format_price(new_bal)}\n\n"
        f"🔑 <b>Delivered Data:</b>\n<code>{items_snippet}</code>"
    )
    send_telegram_notify(user["telegram_id"], msg)

    return {
        "status": "success",
        "order_id": order_id,
        "product": product["name"],
        "quantity": qty,
        "total_paid": total_cost,
        "formatted_total_paid": format_price(total_cost),
        "remaining_balance": new_bal,
        "formatted_remaining_balance": format_price(new_bal),
        "delivered_items": delivered_data
    }

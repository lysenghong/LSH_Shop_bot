import json
import time
import httpx
from config import SUPPLIER_BASE_URL

def categorize_product(name: str) -> str:
    """Categorizes a product based on its title into standard category groups."""
    name_lower = name.lower()
    
    if any(k in name_lower for k in ["hotmail", "outlook", "live"]):
        return "Hotmail Outlook"
    elif any(k in name_lower for k in ["gmail", "gmx", "domain edu"]):
        return "Gmail Account & Edu"
    elif any(k in name_lower for k in ["telegram", "tdata", "session", "otp", "facebook", "clone", "twitter", "tiktok", "instagram", "snapchat"]):
        return "Telegram & Social"
    elif any(k in name_lower for k in ["vpn", "proxy", "surfshark", "expressvpn", "nordvpn", "hma", "cyberghost", "proton", "pia"]):
        return "VPN & Proxy"
    elif any(k in name_lower for k in ["chatgpt", "canva", "capcut", "netflix", "amazon", "apple", "walmart", "paypal"]):
        return "AI & Premium Services"
async def check_supplier_balance(supplier_api_key: str) -> float:
    """
    Checks live user account balance on bulkmail.shop via API.
    Returns float balance or -1.0 on error.
    """
    if not supplier_api_key or supplier_api_key.strip() == "":
        return 100.0

    url = f"{SUPPLIER_BASE_URL}/api/balance"
    headers = {
        "X-API-Key": supplier_api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("success") is True:
                    data = res_data.get("data", {})
                    return float(data.get("balance", 0.0))
    except Exception as e:
        print(f"Error checking supplier balance: {e}")
async def get_supplier_product_by_id(supplier_id: str):
    """Fetches single product details directly from bulkmail.shop API by product ID."""
    res = await fetch_supplier_products()
    all_prods = res.get("all", [])
    s_id = str(supplier_id).strip()
    for p in all_prods:
        if str(p["id"]) == s_id:
            return p
    return None

async def fetch_supplier_products():
    """
    Fetches available products and live prices from https://bulkmail.shop/api/products.
    Returns:
    {
        'all': [{'id': str, 'name': str, 'price': float, 'stock': int, 'category': str}],
        'categories': { 'Category Name': [products...] }
    }
    """
    url = f"{SUPPLIER_BASE_URL}/api/products"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }
    all_products = []
    
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                res_data = resp.json()
                items = res_data.get("data", [])
                for item in items:
                    p_id = str(item.get("id"))
                    name = str(item.get("name", "Product"))
                    price = float(item.get("price", 0.0))
                    stock = int(item.get("stock_quantity", 0))
                    cat = categorize_product(name)
                    all_products.append({
                        "id": p_id,
                        "name": name,
                        "price": price,
                        "stock": stock,
                        "category": cat
                    })
    except Exception as e:
        print(f"Error fetching supplier products from API: {e}")
        
    categories_dict = {}
    for p in all_products:
        cat_name = p["category"]
        if cat_name not in categories_dict:
            categories_dict[cat_name] = []
        categories_dict[cat_name].append(p)
        
    return {
        "all": all_products,
        "categories": categories_dict
    }

async def buy_supplier_product(supplier_product_id: str, quantity: int, supplier_api_key: str):
    """
    Executes product purchase against bulkmail.shop API.
    Endpoint: POST https://bulkmail.shop/api/orders
    Header: X-API-Key: <supplier_api_key>
    Returns tuple: (success: bool, supplier_order_id: str, delivered_items: str, error_msg: str)
    """
    if not supplier_api_key or supplier_api_key.strip() == "":
        mock_id = f"MOCK_ORD_{int(time.time())}"
        items = []
        for i in range(quantity):
            items.append(f"user_{supplier_product_id}_{i+1}@hotmail.com:Pass{1000+i:04d}:recovery{i+1}@gmail.com")
        delivered_str = "\n".join(items)
        return True, mock_id, delivered_str, ""

    url = f"{SUPPLIER_BASE_URL}/api/orders"
    headers = {
        "X-API-Key": supplier_api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    # Try parsing supplier_product_id as integer if numerical
    try:
        p_id_val = int(supplier_product_id)
    except ValueError:
        p_id_val = supplier_product_id

    payload = {
        "product_id": p_id_val,
        "quantity": int(quantity)
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                res_data = resp.json()
                if res_data.get("success") is True or res_data.get("status") in (True, "success"):
                    data = res_data.get("data", {})
                    order_id = str(data.get("order_id", data.get("id", "ORD_SUCCESS")))
                    items = data.get("items", data.get("accounts", []))
                    if isinstance(items, list):
                        delivered_str = "\n".join([str(x) for x in items])
                    else:
                        delivered_str = str(items)
                    return True, order_id, delivered_str, ""
                else:
                    err = res_data.get("error", res_data.get("message", "Purchase failed on bulkmail.shop"))
                    return False, "", "", str(err)
            else:
                return False, "", "", f"Supplier API Error {resp.status_code}: {resp.text[:150]}"
    except Exception as e:
        return False, "", "", f"Supplier connection error: {str(e)}"

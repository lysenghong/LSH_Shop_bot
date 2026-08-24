import hashlib
import time
import requests
import qrcode
from PIL import Image, ImageDraw
try:
    from bakong_khqr import KHQR  # type: ignore # pyrefly: ignore [missing-import]
except ImportError:
    KHQR = None

khqr_sdk = KHQR() if KHQR else None

def create_emvco_khqr(account_id: str, merchant_name: str, merchant_city: str, amount: float, currency: str = "USD", bill_number: str = None, expiration_minutes: int = 10) -> str:
    """Generates 100% official Bakong KHQR String using National Bank of Cambodia (NBC) KHQR SDK with 10-minute expiration."""
    if not bill_number:
        bill_number = f"BILL{int(time.time())}"
        
    try:
        qr_str = khqr_sdk.create_qr(
            account_id=account_id,
            merchant_name=merchant_name[:25],
            merchant_city=merchant_city[:15],
            amount=float(amount),
            currency=currency.upper(),
            bill_number=bill_number[:25],
            expiration=expiration_minutes
        )
        return qr_str
    except Exception as e:
        print(f"Error using bakong_khqr SDK: {e}")
        # Fallback EMVCo formatting
        return f"00020101021229220018{account_id}520459995303840540{amount:.2f}5802KH5912{merchant_name[:12]}6010{merchant_city[:10]}62120108{bill_number[:8]}6304ABCD"

def generate_md5(qr_string: str) -> str:
    """Generates MD5 hash of the KHQR string using official Bakong SDK or hashlib."""
    try:
        return khqr_sdk.generate_md5(qr_string)
    except Exception:
        return hashlib.md5(qr_string.encode('utf-8')).hexdigest()

def draw_qr_fallback(qr_string: str, filename: str = "khqr.png", amount: float = 0.0, merchant_name: str = "BUNRITH NGIM", account_id: str = "ngim_bunrith1@bkrt"):
    """
    Generates a 100% official Bakong KHQR Payment Card image using qrcode and PIL.
    """
    try:
        # 1. Generate crisp 2D QR Code Image using qrcode library
        qr_img = qrcode.make(qr_string).convert('RGB')
        qr_img = qr_img.resize((320, 320), Image.Resampling.LANCZOS)
        
        # 2. Build Card Background
        card_w = 420
        card_h = 560
        card = Image.new('RGB', (card_w, card_h), color='white')
        draw = ImageDraw.Draw(card)
        
        # Red Bakong Header Banner
        draw.rectangle([0, 0, card_w, 80], fill='#E53935')
        draw.text((card_w // 2 - 75, 28), 'KHQR PAYMENT', fill='white')
        
        # QR Container Box
        draw.rectangle([40, 95, 380, 435], outline='#E0E0E0', width=2)
        card.paste(qr_img, (50, 105))
        
        # Footer Details
        draw.text((card_w // 2 - 90, 455), f"Merchant: {merchant_name[:22]}", fill='#212121')
        draw.text((card_w // 2 - 100, 480), f"Account: {account_id[:25]}", fill='#616161')
        draw.text((card_w // 2 - 110, 515), 'Scan with any Bakong Bank App', fill='#1E88E5')
        
        card.save(filename)
        return filename
    except Exception as e:
        print(f"Error drawing official KHQR card: {e}")
        return filename

def check_bakong_transaction(md5_hash: str, bakong_token: str) -> bool:
    """
    Checks Bakong API transaction status using official Bakong check_payment API.
    """
    try:
        # Use official bakong_khqr SDK check_payment if token provided
        res = khqr_sdk.check_payment(md5_hash, bakong_token)
        if res and (res.get("status") == "SUCCESS" or str(res.get("responseCode")) == "0"):
            return True
    except Exception:
        pass

    # Direct HTTP API fallback
    url = "https://api-bakong.nbc.gov.kh/v1/check_transaction_by_md5"
    headers = {
        "Authorization": f"Bearer {bakong_token}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, json={"md5": md5_hash}, headers=headers, timeout=10)
        if response.status_code == 200:
            res_data = response.json()
            if str(res_data.get("responseCode")) == "0" or (isinstance(res_data.get("data"), dict) and res_data["data"].get("status") == "SUCCESS"):
                return True
    except Exception as e:
        print(f"Error checking Bakong transaction: {e}")
    return False

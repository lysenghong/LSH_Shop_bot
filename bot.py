import os
import time
import asyncio
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
)
from telegram.request import HTTPXRequest

from config import BOT_TOKEN, ADMIN_IDS
from database import (
    init_db, get_or_create_user, get_user_by_telegram_id, get_user_by_db_id, find_user_by_any,
    update_user_balance, regenerate_api_key, get_active_products, get_all_products,
    get_product_by_id, add_product, update_product_price, delete_product, toggle_product_status,
    create_order, get_user_orders, create_deposit, get_pending_deposits, get_deposit_by_id, mark_deposit_paid, cancel_deposit,
    get_setting, set_setting, get_all_users_count, get_sales_stats, format_price
)
from khqr_service import create_emvco_khqr, generate_md5, draw_qr_fallback, check_bakong_transaction
from supplier_api import fetch_supplier_products, buy_supplier_product

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# State trackers for conversation inputs
USER_STATES = {}
CACHE_SUPPLIER_PRODUCTS = {}

# Main Keyboard Markup
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("👤 គណនីរបស់ខ្ញុំ (Profile)"), KeyboardButton("🛒 ទិញទំនិញ (Products)")],
        [KeyboardButton("💳 ដាក់ប្រាក់ (Deposit KHQR)"), KeyboardButton("📜 ប្រវត្តិទិញ (Orders)")],
        [KeyboardButton("🔑 API Docs & Key")]
    ], resize_keyboard=True)

# /start Handler
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = update.effective_user
    user = await get_or_create_user(user_data.id, user_data.username, user_data.first_name)
    
    welcome_msg = (
        f"👋 <b>ជម្រាបសួរ {user_data.first_name}!</b>\n"
        f"សូមស្វាគមន៍មកកាន់ <b>LSH_Shop Bot</b> — ហាងលក់ Mail & Digital Accounts ស្វ័យប្រវត្តិ!\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{user['telegram_id']}</code>\n"
        f"💵 <b>តុល្យភាពលុយ (Balance):</b> <code>{format_price(user['balance'])}</code>\n"
        f"🔑 <b>API Key របស់អ្នក:</b> <code>{user['api_key']}</code>\n\n"
        f"សូមជ្រើសរើស menu ខាងក្រោមដើម្បីទិញទំនិញ ឬដាក់ប្រាក់ ៖"
    )
    await update.message.reply_html(welcome_msg, reply_markup=get_main_keyboard())

# Profile Handler
async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_by_telegram_id(update.effective_user.id)
    if not user:
        user = await get_or_create_user(update.effective_user.id)
        
    msg = (
        f"👤 <b>ព័ត៌មានគណនីរបស់អ្នក (My Profile)</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{user['telegram_id']}</code>\n"
        f"👤 <b>ឈ្មោះ:</b> {user['first_name'] or 'N/A'}\n"
        f"💵 <b>តុល្យភាពលុយ:</b> <code>{format_price(user['balance'])}</code>\n\n"
        f"🔑 <b>API Key សម្រាប់ External Tool:</b>\n"
        f"<code>{user['api_key']}</code>\n\n"
        f"<i>លោកអ្នកអាចយក API Key នេះទៅកំណត់ក្នុង Tool របស់លោកអ្នកដើម្បីផ្ញើ Request ទិញទំនិញស្វ័យប្រវត្តិ។</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 បង្កើត API Key ថ្មី (Regenerate Key)", callback_data="regen_api_key")]
    ])
    await update.message.reply_html(msg, reply_markup=keyboard)

# Callback Query Handler
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    if data == "regen_api_key":
        new_key = await regenerate_api_key(user_id)
        await query.edit_message_text(
            f"✅ <b>បង្កើត API Key ថ្មីជោគជ័យ!</b>\n\n🔑 <b>API Key ថ្មីរបស់អ្នក:</b>\n<code>{new_key}</code>",
            parse_mode="HTML"
        )
    elif data.startswith("buy_prod_"):
        prod_id = int(data.split("_")[-1])
        product = await get_product_by_id(prod_id)
        if not product:
            await query.edit_message_text("❌ ទំនិញនេះមិនមានក្នុងស្តុកទៀតទេ!")
            return
            
        user = await get_user_by_telegram_id(user_id)
        msg = (
            f"🛒 <b>ទិញទំនិញ: {product['name']}</b>\n\n"
            f"💵 <b>តម្លៃ/1:</b> {format_price(product['price'])}\n"
            f"💵 <b>លុយរបស់អ្នក:</b> {format_price(user['balance'])}\n"
            f"📝 <b>ការពិពណ៌នា:</b> {product['description'] or 'គ្មាន'}\n\n"
            f"សូមជ្រើសរើសចំនួនដែលចង់ទិញ ៖"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("1 គណនី", callback_data=f"confirm_buy_{prod_id}_1"),
                InlineKeyboardButton("5 គណនី", callback_data=f"confirm_buy_{prod_id}_5"),
                InlineKeyboardButton("10 គណនី", callback_data=f"confirm_buy_{prod_id}_10")
            ],
            [InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data="list_user_products")]
        ])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)
        
    elif data == "list_user_products":
        await show_user_products(query)
        
    elif data.startswith("confirm_buy_"):
        parts = data.split("_")
        prod_id = int(parts[2])
        qty = int(parts[3])
        await execute_bot_purchase(query, user_id, prod_id, qty)
        
    elif data.startswith("check_dep_"):
        dep_id = int(data.split("_")[-1])
        dep = await get_deposit_by_id(dep_id)
        if not dep:
            await query.answer("❌ រកមិនឃើញបង្កាន់ដៃនេះឡើយ!", show_alert=True)
            return
            
        if dep["status"] == "SUCCESS":
            await query.answer("🎉 ទទួលបានប្រាក់ទូទាត់រួចរាល់ហើយ!", show_alert=True)
            return
        elif dep["status"] == "CANCELLED":
            await query.answer("❌ បង្កាន់ដៃនេះត្រូវបានបោះបង់រួចហើយ!", show_alert=True)
            return
            
        # Check live transaction status with Bakong API
        token = await get_setting("bakong_token", "")
        is_paid = check_bakong_transaction(dep["md5_hash"], token)
        if is_paid:
            await mark_deposit_paid(dep["id"])
            new_bal = await update_user_balance(dep["user_id"], dep["amount"])
            await query.answer("🎉 ទទួលបានប្រាក់ទូទាត់ជោគជ័យ!", show_alert=True)
            notify_text = (
                f"🎉 <b>ទទួលបានប្រាក់ទូទាត់ជោគជ័យ!</b>\n\n"
                f"💵 <b>ប្រាក់ដាក់ចូល:</b> <code>+{format_price(dep['amount'])} USD</code>\n"
                f"💳 <b>តុល្យភាពលុយថ្មី:</b> <code>{format_price(new_bal)} USD</code>\n\n"
                f"អរគុណសម្រាប់ការប្រើប្រាស់សេវាកម្ម <b>LSH_Shop Bot</b>!"
            )
            try:
                await query.message.reply_html(notify_text)
            except Exception:
                pass
        else:
            await query.answer("⏳ មិនទាន់ទទួលបានប្រាក់ទូទាត់នៅឡើយទេ! សូម Scan KHQR និងវេរប្រាក់ រួចចុចពិនិត្យម្តងទៀត។", show_alert=True)

    elif data.startswith("cancel_dep_"):
        dep_id = int(data.split("_")[-1])
        dep = await get_deposit_by_id(dep_id)
        if dep and dep["status"] == "PENDING":
            await cancel_deposit(dep_id)
            await query.answer("❌ បានបោះបង់បង្កាន់ដៃជោគជ័យ!", show_alert=True)
            try:
                expired_caption = (
                    f"❌ <b>បង្កាន់ដៃ Deposit #{dep_id} ត្រូវ បានបោះបង់! (CANCELLED)</b>\n\n"
                    f"💵 <b>ចំនួនប្រាក់:</b> <s>{format_price(dep['amount'])} USD</s>\n\n"
                    f"💡 <i>សូមចុចប៊ូតុង 💳 <b>ដាក់ប្រាក់ (Deposit KHQR)</b> ឡើងវិញដើម្បីបង្កើតបង្កាន់ដៃថ្មី!</i>"
                )
                await query.message.edit_caption(caption=expired_caption, parse_mode="HTML")
            except Exception:
                pass
        else:
            await query.answer("❌ បង្កាន់ដៃនេះមិនអាចបោះបង់បានឡើយ!", show_alert=True)
        
    elif data.startswith("admin_approve_dep_"):
        if user_id not in ADMIN_IDS:
            await query.answer("❌ លោកអ្នកគ្មានសិទ្ធិអនុវត្តមុខងារនេះទេ!", show_alert=True)
            return
        dep_id = int(data.split("_")[-1])
        dep = await get_deposit_by_id(dep_id)
        if dep and dep["status"] == "PENDING":
            await mark_deposit_paid(dep["id"])
            new_bal = await update_user_balance(dep["user_id"], dep["amount"])
            await query.answer("✅ បានអនុម័តប្រាក់ជោគជ័យ!", show_alert=True)
            approved_text = (
                f"✅ <b>បានអនុម័តប្រាក់ KHQR Deposit #{dep_id} ជោគជ័យ!</b>\n\n"
                f"💵 <b>ចំនួនប្រាក់:</b> <code>{format_price(dep['amount'])} USD</code>\n"
                f"💳 <b>តុល្យភាពថ្មីរបស់ម៉ូយ:</b> <code>{format_price(new_bal)} USD</code>"
            )
            await query.edit_message_text(approved_text, parse_mode="HTML")
            
            notify_text = (
                f"🎉 <b>ទទួលបានប្រាក់ទូទាត់ជោគជ័យ!</b>\n\n"
                f"💵 <b>ប្រាក់ដាក់ចូល:</b> <code>+{format_price(dep['amount'])} USD</code>\n"
                f"💳 <b>តុល្យភាពលុយថ្មី:</b> <code>{format_price(new_bal)} USD</code>\n\n"
                f"អរគុណសម្រាប់ការប្រើប្រាស់សេវាកម្ម <b>LSH_Shop Bot</b>!"
            )
            try:
                user_obj = await get_user_by_db_id(dep["user_id"])
                if user_obj:
                    await context.bot.send_message(chat_id=user_obj["telegram_id"], text=notify_text, parse_mode="HTML")
            except Exception as e:
                print(f"Failed to notify user: {e}")
        else:
            await query.answer("⚠️ បង្កាន់ដៃនេះត្រូវបានអនុម័ត ឬបោះបង់រួចហើយ!", show_alert=True)

    elif data.startswith("admin_reject_dep_"):
        if user_id not in ADMIN_IDS:
            await query.answer("❌ លោកអ្នកគ្មានសិទ្ធិអនុវត្តមុខងារនេះទេ!", show_alert=True)
            return
        dep_id = int(data.split("_")[-1])
        dep = await get_deposit_by_id(dep_id)
        if dep and dep["status"] == "PENDING":
            await cancel_deposit(dep_id)
            await query.answer("❌ បានបដិសេធបង្កាន់ដៃជោគជ័យ!", show_alert=True)
            await query.edit_message_text(f"❌ <b>បានបដិសេធបង្កាន់ដៃ Deposit #{dep_id}</b>", parse_mode="HTML")

    # --- ADMIN CALLBACKS ---
    elif data == "admin_live_categories":
        await show_admin_categories(query)
    elif data.startswith("admin_cat_"):
        cat_name = data.replace("admin_cat_", "")
        await show_admin_category_products(query, cat_name)
    elif data == "admin_manage_products":
        await show_admin_manage_products(query)
    elif data.startswith("admin_edit_price_"):
        prod_id = int(data.split("_")[-1])
        product = await get_product_by_id(prod_id)
        if product:
            USER_STATES[user_id] = {"action": "admin_editing_price", "product_id": prod_id}
            await query.message.reply_html(
                f"✏️ <b>កែប្រែតម្លៃទំនិញ: {product['name']}</b>\n\n"
                f"តម្លៃបច្ចុប្បន្ន: <code>{format_price(product['price'])}</code>\n\n"
                f"សូមវាយតម្លៃថ្មី (ឧទាហរណ៍: <code>0.0002</code> ឬ <code>0.05</code>) ៖"
            )
    elif data.startswith("admin_toggle_prod_"):
        prod_id = int(data.split("_")[-1])
        await toggle_product_status(prod_id)
        await show_admin_manage_products(query)
    elif data.startswith("admin_toggle_api_"):
        prod_id = int(data.split("_")[-1])
        from database import toggle_product_api_status
        await toggle_product_api_status(prod_id)
        await query.answer("⚡ បានផ្លាស់ប្តូរ API Access ជោគជ័យ!", show_alert=True)
        await show_admin_manage_products(query)
    elif data.startswith("admin_delete_prod_"):
        prod_id = int(data.split("_")[-1])
        product = await get_product_by_id(prod_id)
        if product:
            await delete_product(prod_id)
            await query.answer("🗑 បានលុបទំនិញជោគជ័យ!", show_alert=True)
        await show_admin_manage_products(query)
    elif data.startswith("admin_add_supp_"):
        supp_id = data.replace("admin_add_supp_", "")
        USER_STATES[user_id] = {"action": "admin_adding_supplier_prod", "supplier_id": supp_id}
        await query.message.reply_html(
            f"➕ <b>បន្ថែមទំនិញចូល Bot (Supplier ID: {supp_id})</b>\n\n"
            f"សូមវាយ <b>ឈ្មោះទំនិញ | តម្លៃលក់($)</b>\n"
            f"<i>ឧទាហរណ៍ ៖</i> <code>Outlook Short Live | 0.0035</code>"
        )
    elif data == "admin_add_by_id":
        USER_STATES[user_id] = {"action": "admin_entering_supplier_id"}
        await query.message.reply_html(
            "🔍 <b>បន្ថែមទំនិញតាម Supplier Product ID</b>\n\n"
            "សូមវាយ <b>Supplier Product ID</b> លើ bulkmail.shop ៖\n"
            "<i>(ឧទាហរណ៍ ៖ <code>8</code> សម្រាប់ Short Live Outlook ឬ <code>5</code> សម្រាប់ Short Live Hotmail)</i>\n\n"
            "<i>ឬវាយទម្រង់ ១ បន្ទាត់ ៖</i> <code>8 | 0.0035</code>"
        )
    elif data == "admin_add_balance":
        USER_STATES[user_id] = {"action": "admin_add_balance_step1"}
        await query.message.reply_html(
            "💵 <b>បញ្ចូលលុយឱ្យម៉ូយ (Add Balance):</b>\n\n"
            "សូមវាយ Telegram ID (ឧទាហរណ៍: <code>1017751722</code> ឬ <code>2038134173</code>) ឬ Username (ឧទាហរណ៍: <code>@username</code>) ៖"
        )
    elif data == "admin_set_supplier_key":
        USER_STATES[user_id] = {"action": "admin_set_supplier_key"}
        curr_key = await get_setting("supplier_api_key", "មិនទាន់កំណត់")
        await query.message.reply_html(f"🔑 <b>កំណត់ Supplier API Key (bulkmail.shop):</b>\n\nKey បច្ចុប្បន្ន: <code>{curr_key}</code>\n\nសូមផ្ញើ API Key ថ្មីមកកាន់ Chat នេះ ៖")
    elif data == "admin_pause_supplier_alert":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏱️ 1 ម៉ោង", callback_data="admin_set_alert_pause_3600"),
                InlineKeyboardButton("⏱️ 6 ម៉ោង", callback_data="admin_set_alert_pause_21600")
            ],
            [
                InlineKeyboardButton("⏱️ 12 ម៉ោង", callback_data="admin_set_alert_pause_43200"),
                InlineKeyboardButton("⏱️ 24 ម៉ោង", callback_data="admin_set_alert_pause_86400")
            ],
            [
                InlineKeyboardButton("⛔ បិទការព្រមានរហូត", callback_data="admin_set_alert_pause_-1"),
                InlineKeyboardButton("🟢 បើកការព្រមានឡើងវិញ", callback_data="admin_set_alert_pause_0")
            ]
        ])
        await query.message.reply_html(
            "🔕 <b>ជ្រើសរើសរយៈពេលផ្អាកការព្រមានតុល្យភាព (Pause Alert Duration) ៖</b>",
            reply_markup=keyboard
        )
    elif data.startswith("admin_set_alert_pause_"):
        secs = int(data.replace("admin_set_alert_pause_", ""))
        now = time.time()
        if secs == -1:
            await set_setting("supplier_alert_pause_until", "-1")
            msg = "⛔ <b>បានបិទការព្រមានតុល្យភាព Supplier រហូត!</b>\n\n<i>(លោកអ្នកអាចចូលទៅបើកឡើងវិញបានតាមរយៈ Admin Menu)</i>"
        elif secs == 0:
            await set_setting("supplier_alert_pause_until", "0")
            msg = "🟢 <b>បានបើកការព្រមានតុល្យភាព Supplier ឡើងវិញរួចរាល់ហើយ!</b>"
        else:
            pause_until_ts = now + secs
            await set_setting("supplier_alert_pause_until", str(pause_until_ts))
            hours = secs // 3600
            msg = f"✅ <b>បានផ្អាកការព្រមានតុល្យភាព Supplier រយៈពេល {hours} ម៉ោង រួចរាល់ហើយ!</b>"
            
        await query.answer("✅ បានកំណត់រយៈពេលផ្អាកជោគជ័យ!", show_alert=True)
        await query.message.reply_html(msg)

# Execute Purchase inside Bot
async def execute_bot_purchase(query, telegram_id: int, product_id: int, quantity: int):
    user = await get_user_by_telegram_id(telegram_id)
    product = await get_product_by_id(product_id)
    
    if not product or product["status"] != 1:
        await query.edit_message_text("❌ ទំនិញនេះមិនមានក្នុងស្តុកទេ!")
        return
        
    total_cost = product["price"] * quantity
    if user["balance"] < total_cost:
        await query.edit_message_text(
            f"❌ <b>តុល្យភាពលុយមិនគ្រប់គ្រាន់!</b>\n\n"
            f"លោកអ្នកមាន: <code>{format_price(user['balance'])}</code>\n"
            f"ត្រូវបង់: <code>{format_price(total_cost)}</code>\n\n"
            f"សូមចុចប៊ូតុង <b>💳 ដាក់ប្រាក់ (Deposit KHQR)</b> ដើម្បីបញ្ចូលលុយ!",
            parse_mode="HTML"
        )
        return

    await query.edit_message_text("⏳ <b>កំពុងដំណើរការចេញទំនិញជូនលោកអ្នក... សូមរង់ចាំមួយភ្លែត! ⚡</b>", parse_mode="HTML")
    
    # 1. Deduct balance
    new_bal = await update_user_balance(user["id"], -total_cost)
    
    # 2. Call Supplier API
    supplier_api_key = await get_setting("supplier_api_key", "")
    success, supplier_ord_id, delivered_data, err_msg = await buy_supplier_product(
        product["supplier_product_id"], quantity, supplier_api_key
    )
    
    if not success:
        # Refund on failure
        await update_user_balance(user["id"], total_cost)
        await query.edit_message_text(
            f"❌ <b>ការទិញមិនជោគជ័យ ៖</b> ទំនិញកំពុងរៀបចំស្តុកឡើងវិញ ឬបណ្តោះអាសន្នមិនអាចទិញបានឡើយ។\n\n"
            f"<i>ប្រព័ន្ធបានសងប្រាក់ {format_price(total_cost)} ចូល Account វិញរួចរាល់ហើយ។</i>",
            parse_mode="HTML"
        )
        return

    # Record Order
    order_id = await create_order(
        user_id=user["id"],
        product_id=product["id"],
        supplier_order_id=supplier_ord_id,
        quantity=quantity,
        unit_price=product["price"],
        total_price=total_cost,
        result_data=delivered_data,
        status="COMPLETED"
    )
    
    result_msg = (
        f"✅ <b>ទិញទំនិញជោគជ័យ! (Order #{order_id})</b>\n\n"
        f"📦 <b>ទំនិញ:</b> {product['name']}\n"
        f"🔢 <b>ចំនួន:</b> {quantity}\n"
        f"💵 <b>ប្រាក់សរុប:</b> {format_price(total_cost)}\n"
        f"💳 <b>លុយនៅសល់:</b> {format_price(new_bal)}\n\n"
        f"🔑 <b>ទិន្នន័យទទួលបាន (Credentials):</b>\n"
        f"<code>{delivered_data}</code>"
    )
    await query.edit_message_text(result_msg, parse_mode="HTML")

# Show User Products List
async def show_user_products(target):
    products = await get_active_products()
    if not products:
        msg = "🛒 <b>បច្ចុប្បន្នមិនទាន់មានទំនិញក្នុងស្តុកឡើយ!</b>"
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(msg, parse_mode="HTML")
        else:
            await target.reply_html(msg)
        return

    msg = "🛒 <b>បញ្ជីទំនិញដែលមានក្នុងហាង (Available Products):</b>\n\nសូមចុចលើទំនិញដែលលោកអ្នកចង់ទិញ ៖"
    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(f"{p['name']} — {format_price(p['price'])}", callback_data=f"buy_prod_{p['id']}")])
        
    keyboard = InlineKeyboardMarkup(buttons)
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)
    else:
        await target.reply_html(msg, reply_markup=keyboard)

# Deposit KHQR Handler
async def deposit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_STATES[user_id] = {"action": "waiting_deposit_amount"}
    await update.message.reply_html(
        "💳 <b>ដាក់ប្រាក់តាម Bakong KHQR (Deposit Funds)</b>\n\n"
        "សូមវាយបញ្ចូលចំនួនប្រាក់ ($ USD) ដែលលោកអ្នកចង់ដាក់ ៖\n"
        "<i>(ឧទាហរណ៍: 1, 5, 10, 50, 100)</i>"
    )

# Orders History Handler
async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_by_telegram_id(update.effective_user.id)
    if not user:
        await update.message.reply_html("❌ រកមិនឃើញគណនីរបស់អ្នកទេ!")
        return
        
    orders = await get_user_orders(user["id"], limit=10)
    if not orders:
        await update.message.reply_html("📜 <b>លោកអ្នកមិនទាន់មានប្រវត្តិទិញទំនិញនៅឡើយទេ!</b>")
        return
        
    msg = "📜 <b>ប្រវត្តិទិញទំនិញ ១០ ដងចុងក្រោយរបស់អ្នក ៖</b>\n\n"
    for o in orders:
        msg += (
            f"🔹 <b>Order #{o['id']}</b> ({o['created_at'][:19]})\n"
            f"📦 ទំនិញ: {o['product_name'] or 'N/A'} (x{o['quantity']})\n"
            f"💵 តម្លៃ: {format_price(o['total_price'])}\n"
            f"🔑 ទិន្នន័យ: <code>{o['result_data'][:60]}...</code>\n\n"
        )
    await update.message.reply_html(msg)

# API Documentation Handler
async def api_docs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_by_telegram_id(update.effective_user.id)
    api_key = user["api_key"] if user else "YOUR_API_KEY"
    
    docs_msg = (
        f"🔑 <b>ការប្រើប្រាស់ API សម្រាប់ External Tool / Software</b>\n\n"
        f"លោកអ្នកអាចយក API Key របស់លោកអ្នកទៅប្រើប្រាស់ក្នុង Tool ដើម្បីផ្ញើ Request ទិញទំនិញស្វ័យប្រវត្តិ ៖\n"
        f"<b>API Key របស់អ្នក:</b> <code>{api_key}</code>\n\n"
        f"🌐 <b>Interactive Swagger API Docs:</b>\n"
        f"<code>http://YOUR_SERVER_IP:8085/docs</code>\n\n"
        f"📡 <b>1. Endpoint ទិញទំនិញ (POST):</b>\n"
        f"<code>http://YOUR_SERVER_IP:8085/api/v1/buy</code>\n"
        f"<b>cURL Example:</b>\n"
        f"<code>curl -X POST \"http://YOUR_SERVER_IP:8085/api/v1/buy\" -H \"Content-Type: application/json\" -d '{{\"api_key\": \"{api_key}\", \"product_id\": 6, \"quantity\": 1}}'</code>\n\n"
        f"💡 <b>ឧទាហរណ៍កូដ Python:</b>\n"
        f"<pre>"
        f"import requests\n\n"
        f"url = 'http://YOUR_SERVER_IP:8085/api/v1/buy'\n"
        f"payload = {{\n"
        f"    'api_key': '{api_key}',\n"
        f"    'product_id': 6,\n"
        f"    'quantity': 1\n"
        f"}}\n"
        f"res = requests.post(url, json=payload)\n"
        f"print(res.json())\n"
        f"</pre>\n\n"
        f"📡 <b>2. Endpoint ពិនិត្យមើលលុយ (GET):</b>\n"
        f"<code>http://YOUR_SERVER_IP:8085/api/v1/user/info?api_key={api_key}</code>\n\n"
        f"📡 <b>3. Endpoint មើលបញ្ជីទំនិញ (GET):</b>\n"
        f"<code>http://YOUR_SERVER_IP:8085/api/v1/products?api_key={api_key}</code>"
    )
    await update.message.reply_html(docs_msg)

# Text Message Router
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if text == "👤 គណនីរបស់ខ្ញុំ (Profile)":
        await profile_handler(update, context)
        return
    elif text == "🛒 ទិញទំនិញ (Products)":
        await show_user_products(update.message)
        return
    elif text == "💳 ដាក់ប្រាក់ (Deposit KHQR)":
        await deposit_handler(update, context)
        return
    elif text == "📜 ប្រវត្តិទិញ (Orders)":
        await orders_handler(update, context)
        return
    elif text == "🔑 API Docs & Key":
        await api_docs_handler(update, context)
        return
        
    state = USER_STATES.get(user_id)
    if state:
        action = state.get("action")
        
        if action == "waiting_deposit_amount":
            try:
                amount = float(text)
                if amount < 0.0001:
                    await update.message.reply_html("❌ ចំនួនប្រាក់មិនត្រឹមត្រូវ!")
                    return
            except ValueError:
                await update.message.reply_html("❌ សូមវាយបញ្ចូលលេខប្រាក់ត្រឹមត្រូវ!")
                return
                
            USER_STATES.pop(user_id, None)
            await generate_and_send_deposit_qr(update, user_id, amount)
            return

        elif action == "admin_add_balance_step1":
            target_user = await find_user_by_any(text)
            if not target_user:
                await update.message.reply_html("❌ រកមិនឃើញម៉ូយម្នាក់នេះឡើយ! សូមពិនិត្យ Telegram ID, Username ឬ User DB ID (#1) ឡើងវិញ។")
                USER_STATES.pop(user_id, None)
                return
                
            USER_STATES[user_id] = {
                "action": "admin_add_balance_step2",
                "target_id": target_user["id"],
                "target_tg_id": target_user["telegram_id"],
                "target_name": target_user["first_name"] or target_user["username"] or str(target_user["telegram_id"])
            }
            await update.message.reply_html(
                f"👤 <b>ម៉ូយ:</b> {USER_STATES[user_id]['target_name']}\n"
                f"🆔 <b>Telegram ID:</b> <code>{target_user['telegram_id']}</code>\n"
                f"💵 <b>លុយបច្ចុប្បន្ន:</b> <code>{format_price(target_user['balance'])}</code>\n\n"
                f"សូមវាយចំនួនប្រាក់ដែលចង់ថែម (ឧទាហរណ៍: <code>10</code>) ឬដក (ឧទាហរណ៍: <code>-5</code>) ៖"
            )
            return

        elif action == "admin_add_balance_step2":
            try:
                amt = float(text)
            except ValueError:
                await update.message.reply_html("❌ សូមវាយបញ្ចូលចំនួនលេខឱ្យបានត្រឹមត្រូវ!")
                return
                
            target_id = state.get("target_id")
            target_tg_id = state.get("target_tg_id")
            target_name = state.get("target_name")
            new_bal = await update_user_balance(target_id, amt)
            
            USER_STATES.pop(user_id, None)
            await update.message.reply_html(
                f"✅ <b>បញ្ចូល/កែប្រែប្រាក់ជោគជ័យ!</b>\n\n"
                f"👤 <b>ម៉ូយ:</b> {target_name}\n"
                f"🆔 <b>Telegram ID:</b> <code>{target_tg_id}</code>\n"
                f"➕ <b>ចំនួន:</b> {format_price(amt)}\n"
                f"💵 <b>តុល្យភាពថ្មី:</b> <code>{format_price(new_bal)}</code>"
            )
            
            # Send notification to target user's Telegram Chat
            try:
                alert_text = (
                    f"🔔 <b>តុល្យភាពលុយរបស់អ្នកត្រូវបានកែប្រែ!</b>\n\n"
                    f"➕ <b>ចំនួនប្រាក់:</b> {format_price(amt)}\n"
                    f"💳 <b>តុល្យភាពលុយថ្មី:</b> <code>{format_price(new_bal)}</code>"
                )
                await context.bot.send_message(chat_id=target_tg_id, text=alert_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to alert user of balance change: {e}")
            return

        elif action == "admin_editing_price":
            try:
                new_p = float(text)
            except ValueError:
                await update.message.reply_html("❌ តម្លៃត្រូវតែជាលេខ (ឧទាហរណ៍: 0.0002)!")
                return
                
            prod_id = state.get("product_id")
            await update_product_price(prod_id, new_p)
            USER_STATES.pop(user_id, None)
            await update.message.reply_html(f"✅ <b>កែប្រែតម្លៃទំនិញជោគជ័យ!</b>\n\nតម្លៃថ្មី: <code>{format_price(new_p)}</code>")
            return

        elif action == "admin_entering_supplier_id":
            parts = [x.strip() for x in text.split("|")]
            from supplier_api import get_supplier_product_by_id
            
            if len(parts) == 1:
                supp_id = parts[0]
                supp_prod = await get_supplier_product_by_id(supp_id)
                if not supp_prod:
                    await update.message.reply_html(
                        f"❌ រកមិនឃើញទំនិញ ID: <code>{supp_id}</code> លើ bulkmail.shop ឡើយ!\n\n"
                        f"សូមពិនិត្យមើល Supplier Product ID ឡើងវិញ ឬវាយ ៖ <code>{supp_id} | ឈ្មោះទំនិញ | តម្លៃលក់</code>"
                    )
                    return
                
                USER_STATES[user_id] = {
                    "action": "admin_entering_selling_price",
                    "supp_id": supp_id,
                    "name": supp_prod["name"],
                    "cost": supp_prod["price"]
                }
                await update.message.reply_html(
                    f"✅ <b>រកឃើញទំនិញ ID: {supp_id}</b>\n\n"
                    f"📦 <b>ឈ្មោះទំនិញ ៖</b> {supp_prod['name']}\n"
                    f"💵 <b>តម្លៃដើម Supplier ៖</b> {format_price(supp_prod['price'])}\n"
                    f"📦 <b>ស្តុកបច្ចុប្បន្ន ៖</b> {supp_prod['stock']} pcs\n\n"
                    f"សូមវាយ <b>តម្លៃលក់($)</b> ដែលលោកអ្នកចង់លក់ ៖\n"
                    f"<i>(ឧទាហរណ៍ ៖ <code>{supp_prod['price']}</code> ឬ <code>0.005</code>)</i>"
                )
                return
            elif len(parts) == 2:
                supp_id, price_str = parts[0], parts[1]
                supp_prod = await get_supplier_product_by_id(supp_id)
                name = supp_prod["name"] if supp_prod else f"Product #{supp_id}"
                try:
                    price = float(price_str)
                except ValueError:
                    await update.message.reply_html("❌ តម្លៃត្រូវតែជាលេខ!")
                    return
                await add_product(supp_id, name, price)
                USER_STATES.pop(user_id, None)
                await update.message.reply_html(
                    f"✅ <b>បន្ថែមទំនិញចូល Bot ជោគជ័យ!</b>\n\n"
                    f"📦 <b>ទំនិញ ៖</b> {name}\n"
                    f"🆔 <b>Supplier ID ៖</b> <code>{supp_id}</code>\n"
                    f"💵 <b>តម្លៃលក់ ៖</b> {format_price(price)}"
                )
                return
            elif len(parts) >= 3:
                supp_id, name, price_str = parts[0], parts[1], parts[2]
                try:
                    price = float(price_str)
                except ValueError:
                    await update.message.reply_html("❌ តម្លៃត្រូវតែជាលេខ!")
                    return
                await add_product(supp_id, name, price)
                USER_STATES.pop(user_id, None)
                await update.message.reply_html(
                    f"✅ <b>បន្ថែមទំនិញចូល Bot ជោគជ័យ!</b>\n\n"
                    f"📦 <b>ទំនិញ ៖</b> {name}\n"
                    f"🆔 <b>Supplier ID ៖</b> <code>{supp_id}</code>\n"
                    f"💵 <b>តម្លៃលក់ ៖</b> {format_price(price)}"
                )
                return

        elif action == "admin_entering_selling_price":
            supp_id = state.get("supp_id")
            name = state.get("name")
            try:
                price = float(text)
            except ValueError:
                await update.message.reply_html("❌ តម្លៃត្រូវតែជាលេខ!")
                return
            await add_product(supp_id, name, price)
            USER_STATES.pop(user_id, None)
            await update.message.reply_html(
                f"✅ <b>បន្ថែមទំនិញចូល Bot ជោគជ័យ!</b>\n\n"
                f"📦 <b>ទំនិញ ៖</b> {name}\n"
                f"🆔 <b>Supplier ID ៖</b> <code>{supp_id}</code>\n"
                f"💵 <b>តម្លៃលក់ ៖</b> {format_price(price)}"
            )
            return

        elif action == "admin_adding_supplier_prod":
            supp_id = state.get("supplier_id")
            parts = [x.strip() for x in text.split("|")]
            if len(parts) < 2:
                await update.message.reply_html("❌ សូមវាយ ៖ <code>ឈ្មោះទំនិញ | តម្លៃលក់($)</code>")
                return
            name, price_str = parts[0], parts[1]
            try:
                price = float(price_str)
            except ValueError:
                await update.message.reply_html("❌ តម្លៃត្រូវតែជាលេខ!")
                return
                
            await add_product(supp_id, name, price)
            USER_STATES.pop(user_id, None)
            await update.message.reply_html(
                f"✅ <b>បន្ថែមទំនិញចូល Bot ជោគជ័យ!</b>\n\n"
                f"📦 <b>ទំនិញ:</b> {name}\n"
                f"🆔 <b>Supplier ID:</b> <code>{supp_id}</code>\n"
                f"💵 <b>តម្លៃលក់:</b> {format_price(price)}"
            )
            return

        elif action == "admin_set_supplier_key":
            await set_setting("supplier_api_key", text)
            USER_STATES.pop(user_id, None)
            await update.message.reply_html(f"✅ <b>បាន រក្សាទុក Supplier API Key រួចរាល់!</b>\n\nKey: <code>{text}</code>")
            return

async def deposit_countdown_timer(message, amount: float, dep_id: int, merchant_name: str, account_id: str):
    """Background task updating live deposit countdown timer every 20s until paid, cancelled or 10m expires."""
    from database import get_deposit_by_id
    total_seconds = 600 # 10 minutes
    
    deposit_btns = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 ពិនិត្យការទូទាត់", callback_data=f"check_dep_{dep_id}"),
            InlineKeyboardButton("❌ បោះបង់", callback_data=f"cancel_dep_{dep_id}")
        ]
    ])
    
    while total_seconds > 0:
        await asyncio.sleep(20) # 20 seconds interval update
        total_seconds -= 20
        
        mins = total_seconds // 60
        secs = total_seconds % 60
        time_str = f"{mins:02d}:{secs:02d}"
        
        dep = await get_deposit_by_id(dep_id)
        if dep and dep["status"] in ("SUCCESS", "CANCELLED"):
            return
            
        updated_caption = (
            f"💳 <b>បង្កាន់ដៃ Bakong KHQR Deposit #{dep_id}</b>\n\n"
            f"💵 <b>ចំនួនប្រាក់:</b> <code>{format_price(amount)} USD</code>\n"
            f"👤 <b>អ្នកទទួល:</b> {merchant_name} ({account_id})\n"
            f"⏳ <b>សុពលភាពនៅសល់:</b> <code>{time_str}</code> (10-Min Expiration)\n\n"
            f"🔔 <i>សូម Scan KHQR នេះតាមរយៈ App ធនាគារ Bakong/ABA/Sathapana ក្នុងអំឡុងពេលនេះ...</i>\n"
            f"⚡ <i>នៅពេល Scan វេររួច ប្រព័ន្ធនឹងបញ្ចូលប្រាក់ {format_price(amount)} ចូល Account ដោយស្វ័យប្រវត្តិ!</i>"
        )
        try:
            await message.edit_caption(caption=updated_caption, parse_mode="HTML", reply_markup=deposit_btns)
        except Exception:
            pass
            
    expired_caption = (
        f"⏰ <b>បង្កាន់ដៃ Deposit #{dep_id} នេះបានផុតកំណត់ 10 នាទីហើយ! (EXPIRED)</b>\n\n"
        f"💵 <b>ចំនួនប្រាក់:</b> <s>{format_price(amount)} USD</s>\n\n"
        f"💡 <i>សូមចុចប៊ូតុង 💳 <b>ដាក់ប្រាក់ (Deposit KHQR)</b> ឡើងវិញដើម្បីបង្កើតបង្កាន់ដៃថ្មី!</i>"
    )
    try:
        await message.edit_caption(caption=expired_caption, parse_mode="HTML")
    except Exception:
        pass

# Generate & Send Bakong KHQR Deposit
async def generate_and_send_deposit_qr(update: Update, telegram_id: int, amount: float):
    user = await get_user_by_telegram_id(telegram_id)
    account_id = await get_setting("bakong_account_id", "ngim_bunrith1@bkrt")
    merchant_name = await get_setting("bakong_merchant_name", "BUNRITH NGIM")
    from config import BAKONG_TOKEN
    token = await get_setting("bakong_token", BAKONG_TOKEN)
    
    qr_str = create_emvco_khqr(account_id, merchant_name, "Phnom Penh", amount, "USD", bakong_token=token)
    md5_hash = generate_md5(qr_str, bakong_token=token)
    dep_id = await create_deposit(user["id"], amount, md5_hash, qr_str)
    
    filename = f"khqr_{dep_id}.png"
    draw_qr_fallback(qr_str, filename)
    
    caption = (
        f"💳 <b>បង្កាន់ដៃ Bakong KHQR Deposit #{dep_id}</b>\n\n"
        f"💵 <b>ចំនួនប្រាក់:</b> <code>{format_price(amount)} USD</code>\n"
        f"👤 <b>អ្នកទទួល:</b> {merchant_name} ({account_id})\n"
        f"⏳ <b>សុពលភាពនៅសល់:</b> <code>10:00</code> (10-Min Expiration)\n\n"
        f"🔔 <i>សូម Scan KHQR នេះតាមរយៈ App ធនាគារ Bakong/ABA/Sathapana ក្នុងអំឡុងពេលនេះ...</i>\n"
        f"⚡ <i>នៅពេល Scan វេររួច ប្រព័ន្ធនឹងបញ្ចូលប្រាក់ {format_price(amount)} ចូល Account ដោយស្វ័យប្រវត្តិ!</i>"
    )
    
    deposit_btns = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 ពិនិត្យការទូទាត់", callback_data=f"check_dep_{dep_id}"),
            InlineKeyboardButton("❌ បោះបង់", callback_data=f"cancel_dep_{dep_id}")
        ]
    ])
    
    with open(filename, "rb") as photo:
        msg = await update.message.reply_photo(photo=photo, caption=caption, parse_mode="HTML", reply_markup=deposit_btns)
        
    try:
        os.remove(filename)
    except Exception:
        pass
        
    # Start live countdown timer on this message
    asyncio.create_task(deposit_countdown_timer(msg, amount, dep_id, merchant_name, account_id))

    # Send instant alert to Admin Telegram for 1-click manual approval
    user_tg_id = user['telegram_id']
    username = user.get('username', '')
    first_name = user.get('first_name', '') or 'User'

    if username:
        user_link = f"<a href='https://t.me/{username}'>@{username}</a> ({first_name})"
        chat_url = f"https://t.me/{username}"
    else:
        user_link = f"<a href='tg://user?id={user_tg_id}'>{first_name}</a>"
        chat_url = f"tg://user?id={user_tg_id}"

    admin_msg = (
        f"📥 <b>សំណើដាក់ប្រាក់ KHQR ថ្មី! (Deposit #{dep_id})</b>\n\n"
        f"👤 <b>អតិថិជន:</b> {user_link}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_tg_id}</code>\n"
        f"💵 <b>ចំនួនប្រាក់:</b> <code>{format_price(amount)} USD</code>\n"
        f"🕒 <b>ស្ថានភាព:</b> <code>PENDING</code>\n\n"
        f"💡 <i>ប្រសិនបើអតិថិជនបាន Scan វេរប្រាក់រួច លោកអ្នកអាចចុចប៊ូតុងខាងក្រោមដើម្បីអនុម័ត ឬចុច Chat ទៅកាន់ម៉ូយ ៖</i>"
    )
    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ អនុម័ត {format_price(amount)}", callback_data=f"admin_approve_dep_{dep_id}"),
            InlineKeyboardButton("❌ បដិសេធ", callback_data=f"admin_reject_dep_{dep_id}")
        ],
        [
            InlineKeyboardButton("💬 Chat ជាមួយម៉ូយម្នាក់នេះ", url=chat_url)
        ]
    ])
    for admin_id in ADMIN_IDS:
        try:
            await update.get_bot().send_message(chat_id=admin_id, text=admin_msg, parse_mode="HTML", reply_markup=admin_kb)
        except Exception as e:
            print(f"Failed to alert admin of new deposit: {e}")

# Admin Command (/admin)
async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_html("❌ លោកអ្នកគ្មានសិទ្ធិចូលកាន់ Admin Panel ឡើយ!")
        return

    users_count = await get_all_users_count()
    stats = await get_sales_stats()
    supplier_key = await get_setting("supplier_api_key", "")
    key_status = "✅ កំណត់រួច" if supplier_key else "⚠️ មិនទាន់កំណត់"
    
    msg = (
        f"🛠 <b>ADMIN CONTROL PANEL (ផ្ទាំងគ្រប់គ្រងហាង)</b>\n\n"
        f"👥 <b>អតិថិជនសរុប:</b> {users_count} នាក់\n"
        f"📦 <b>ការលក់សរុប:</b> {stats['total_orders']} ដង\n"
        f"💵 <b>ចំណូលសរុប:</b> {format_price(stats['total_sales'])}\n"
        f"🔑 <b>Supplier API Key:</b> {key_status}\n\n"
        f"សូមជ្រើសរើសមុខងារគ្រប់គ្រងខាងក្រោម ៖"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ បន្ថែមទំនិញតាម Supplier Product ID", callback_data="admin_add_by_id")],
        [InlineKeyboardButton("🛒 មើលទំនិញលើ Web (តាម Group/ប្រភេទ)", callback_data="admin_live_categories")],
        [InlineKeyboardButton("🛠 គ្រប់គ្រង/លុបទំនិញក្នុង Bot & API", callback_data="admin_manage_products")],
        [InlineKeyboardButton("💵 បញ្ចូលលុយឱ្យម៉ូយ (Add Balance)", callback_data="admin_add_balance")],
        [InlineKeyboardButton("🔑 កំណត់ Supplier API Key", callback_data="admin_set_supplier_key")]
    ])
    await update.message.reply_html(msg, reply_markup=keyboard)

# Admin Categories View
async def show_admin_categories(query):
    global CACHE_SUPPLIER_PRODUCTS
    await query.edit_message_text("⏳ កំពុងទាញយកប្រភេទទំនិញ (Categories) ពី https://bulkmail.shop ... សូមរង់ចាំ!")
    res = await fetch_supplier_products()
    CACHE_SUPPLIER_PRODUCTS = res
    
    categories = res.get("categories", {})
    if not categories:
        await query.edit_message_text("❌ មិនអាចទាញយកប្រភេទទំនិញបានឡើយ! សូមពិនិត្យ Connection។")
        return

    msg = "🛒 <b>ជ្រើសរើសប្រភេទទំនិញ (Category Groups) លើ Web ផ្គត់ផ្គង់ ៖</b>\n\n"
    buttons = []
    for cat_name, items in categories.items():
        buttons.append([InlineKeyboardButton(f"{cat_name} ({len(items)} ទំនិញ)", callback_data=f"admin_cat_{cat_name}")])
        
    keyboard = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)

# Admin Category Products List
async def show_admin_category_products(query, category_name: str):
    categories = CACHE_SUPPLIER_PRODUCTS.get("categories", {})
    items = categories.get(category_name, [])
    
    if not items:
        await query.edit_message_text(f"❌ មិនមានទំនិញក្នុងប្រភេទ {category_name} ឡើយ!")
        return
        
    msg = f"📂 <b>ប្រភេទទំនិញ: {category_name}</b>\n<i>(សរុប {len(items)} ទំនិញ)</i>\n\n"
    buttons = []
    for p in items[:10]: # Top 10 items
        msg += f"🔹 <b>{p['name']}</b>\n  ID: <code>{p['id']}</code> | តម្លៃដើម: <b>{format_price(p['price'])}</b>\n\n"
        buttons.append([InlineKeyboardButton(f"➕ បន្ថែម: {p['name'][:25]}...", callback_data=f"admin_add_supp_{p['id']}")])
        
    buttons.append([InlineKeyboardButton("⬅️ ត្រឡប់ទៅ Categories វិញ", callback_data="admin_live_categories")])
    keyboard = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)

# Admin Interactive Product Management (Edit / Delete / Toggle / API Filter)
async def show_admin_manage_products(query):
    bot_products = await get_all_products()
    msg = (
        "🛠 <b>គ្រប់គ្រងទំនិញក្នុង Bot & API (Product Control) ៖</b>\n\n"
        "<i>លោកអ្នកអាចបិទ/បើកសិទ្ធិឱ្យទិញតាម API (⚡ API: ✅/❌), កែប្រែតម្លៃ ឬលុបទំនិញ ៖</i>\n\n"
    )
    
    if not bot_products:
        msg += "<i>បច្ចុប្បន្នមិនទាន់មានទំនិញក្នុង Bot ឡើយ។</i>"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 មើលទំនិញលើ Web ដើម្បីបន្ថែម", callback_data="admin_live_categories")]
        ])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)
        return

    buttons = []
    for p in bot_products:
        st_icon = "🟢" if p["status"] == 1 else "🔴"
        api_st = "⚡ API: ✅ On" if p.get("api_enabled", 1) == 1 else "⚡ API: ❌ Off"
        
        # Row 1: Status & Name with price
        buttons.append([
            InlineKeyboardButton(f"{st_icon} {p['name']} ({format_price(p['price'])})", callback_data=f"admin_edit_price_{p['id']}")
        ])
        # Row 2: API Toggle | Edit Price | Delete
        buttons.append([
            InlineKeyboardButton(api_st, callback_data=f"admin_toggle_api_{p['id']}"),
            InlineKeyboardButton("✏️ កែតម្លៃ", callback_data=f"admin_edit_price_{p['id']}"),
            InlineKeyboardButton("🗑 លុប", callback_data=f"admin_delete_prod_{p['id']}")
        ])
        
    buttons.append([InlineKeyboardButton("🛒 បន្ថែមទំនិញពី Web", callback_data="admin_live_categories")])
    keyboard = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)

# Async Background Task: Verify Bakong Deposits
async def bakong_deposit_verifier_loop(app: Application):
    while True:
        try:
            pending = await get_pending_deposits()
            if pending:
                token = await get_setting("bakong_token", "")
                for dep in pending:
                    is_paid = check_bakong_transaction(dep["md5_hash"], token)
                    if is_paid:
                        await mark_deposit_paid(dep["id"])
                        new_bal = await update_user_balance(dep["user_id"], dep["amount"])
                        notify_text = (
                            f"🎉 <b>ទទួលបានប្រាក់ទូទាត់ជោគជ័យ!</b>\n\n"
                            f"💵 <b>ប្រាក់ដាក់ចូល:</b> <code>+{format_price(dep['amount'])} USD</code>\n"
                            f"💳 <b>តុល្យភាពលុយថ្មី:</b> <code>{format_price(new_bal)} USD</code>\n\n"
                            f"អរគុណសម្រាប់ការប្រើប្រាស់សេវាកម្ម <b>LSH_Shop Bot</b>!"
                        )
                        try:
                            await app.bot.send_message(chat_id=dep["telegram_id"], text=notify_text, parse_mode="HTML")
                        except Exception as e:
                            print(f"Failed to notify user deposit: {e}")
        except Exception as e:
            print(f"Error in Bakong deposit verifier loop: {e}")
            
        await asyncio.sleep(5)

# Async Background Task: Monitor Supplier Balance & Alert Admin if < $20 (Max 5 alerts then 1h pause, with interactive pause control)
async def supplier_balance_monitor_loop(app: Application):
    from supplier_api import check_supplier_balance
    from config import DEFAULT_SUPPLIER_API_KEY, ADMIN_IDS
    alert_count = 0
    pause_until = 0
    
    while True:
        try:
            current_time = time.time()
            # Read interactive pause setting from DB
            db_pause_str = await get_setting("supplier_alert_pause_until", "0")
            try:
                db_pause = float(db_pause_str)
            except ValueError:
                db_pause = 0.0
                
            # If muted indefinitely (-1) or still in DB pause duration
            if db_pause == -1.0 or (db_pause > 0 and current_time < db_pause):
                await asyncio.sleep(600)
                continue
                
            if current_time >= pause_until:
                supplier_api_key = await get_setting("supplier_api_key", DEFAULT_SUPPLIER_API_KEY)
                if supplier_api_key:
                    bal = await check_supplier_balance(supplier_api_key)
                    if bal >= 0 and bal < 20.0:
                        alert_count += 1
                        alert_msg = (
                            f"⚠️ <b>ព្រមាន ៖ តុល្យភាពលុយលើ Supplier ជិតអស់ហើយ! ({alert_count}/5)</b>\n\n"
                            f"🏢 <b>Supplier Site:</b> bulkmail.shop\n"
                            f"💵 <b>លុយនៅសល់បច្ចុប្បន្ន:</b> <code>{format_price(bal)} USD</code>\n"
                            f"🚨 <b>កម្រិតព្រមាន:</b> <code>$20.00 USD</code>\n\n"
                            f"💡 <i>សូមប្រញាប់បញ្ចូលប្រាក់បន្ថែមក្នុងគណនី bulkmail.shop របស់លោកអ្នក ដើម្បីធានាថាប្រព័ន្ធអាចរត់ទិញស្វ័យប្រវត្តិកុំឱ្យទាក់ស្ទះ!</i>"
                        )
                        pause_btn = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔕 ផ្អាកការព្រមាន (Pause Alert)", callback_data="admin_pause_supplier_alert")]
                        ])
                        for admin_id in ADMIN_IDS:
                            try:
                                await app.bot.send_message(chat_id=admin_id, text=alert_msg, parse_mode="HTML", reply_markup=pause_btn)
                            except Exception as e:
                                print(f"Failed to send supplier balance alert to admin {admin_id}: {e}")
                        
                        # If sent 5 times in a row, pause for 1 hour (3600 seconds)
                        if alert_count >= 5:
                            alert_count = 0
                            pause_until = current_time + 3600
                    else:
                        # Balance restored to >= $20, reset alert count and pause timer
                        alert_count = 0
                        pause_until = 0
        except Exception as e:
            print(f"Error in supplier balance monitor loop: {e}")
            
        # Check every 10 minutes
        await asyncio.sleep(600)

# Error handler for unexpected network glitches / timeouts
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update:", exc_info=context.error)

# Create Telegram Bot Application Instance
def create_bot_app() -> Application:
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0, pool_timeout=60.0)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_error_handler(global_error_handler)
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("admin", admin_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    async def message_filter_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u_id = update.effective_user.id
        state = USER_STATES.get(u_id)
        if state and state.get("action") == "admin_add_product_input":
            parts = [x.strip() for x in update.message.text.split("|")]
            if len(parts) >= 3:
                await add_product(parts[0], parts[1], float(parts[2]))
                USER_STATES.pop(u_id, None)
                await update.message.reply_html("✅ បានបន្ថែមទំនិញជោគជ័យ!")
                return
        await text_message_handler(update, context)

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_filter_wrapper))
    return app
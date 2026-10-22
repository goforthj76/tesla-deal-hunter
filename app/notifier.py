import os
import re
import logging
import httpx
from typing import Any

log = logging.getLogger('hunter')

def escape_md(text: Any) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    if text is None:
        return ''
    s = str(text)
    # Characters that need escaping in Telegram MarkdownV2: _ * [ ] ( ) ~ ` > # + - = | { } . !
    return re.sub(r'([\\_*\[\]()~`>#+\-=|{}.!])', r'\\\1', s)

def is_configured() -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    return bool(token and chat_id)

async def test_connection() -> dict:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return {'success': False, 'error': 'TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env'}
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': "🚗 *Tesla Deal Hunter connected\\!*\nPush notifications are active for zip 75022\\.",
        'parse_mode': 'MarkdownV2'
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return {'success': True}
            return {'success': False, 'error': f"Telegram API error {resp.status_code}: {resp.text}"}
    except Exception as e:
        log.error(f"Telegram connection test failed: {e}")
        return {'success': False, 'error': str(e)}

async def send_deal_alert(listing: dict, score_result: dict) -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return False

    score = score_result.get('score', 0)
    category = score_result.get('category', 'DEAL')
    year = listing.get('year') or ''
    model = listing.get('model') or 'Tesla'
    trim = listing.get('trim') or ''
    price = listing.get('price')
    mileage = listing.get('mileage')
    dist = score_result.get('distance') or listing.get('distance')
    dealer = listing.get('dealer') or listing.get('source') or 'Dealership'
    hw4 = score_result.get('hw4', '')
    url = listing.get('url', '')

    price_str = f"${int(price):,}" if price else "Price Unlisted"
    mile_str = f"{int(mileage):,} mi" if mileage else "Miles unknown"
    dist_str = f"{dist} mi away" if dist else "Distance unverified"

    title = f"{year} Tesla {model} {trim}".strip()
    reasons_text = "\n".join(f"• {escape_md(r)}" for r in score_result.get('reasons', []))

    text = (
        f"🔥 *{escape_md(category)} — Score: {score}/100*\n\n"
        f"*{escape_md(title)}*\n"
        f"💰 *{escape_md(price_str)}* · {escape_md(mile_str)} · {escape_md(dist_str)}\n"
        f"🏪 {escape_md(dealer)} · _{escape_md(hw4)}_\n\n"
    )
    if reasons_text:
        text += f"*Why this is a deal:*\n{reasons_text}\n"

    payload: dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'MarkdownV2'
    }

    if url and url.startswith(('http://', 'https://')):
        payload['reply_markup'] = {
            'inline_keyboard': [[
                {'text': '🔗 View Listing On Site', 'url': url}
            ]]
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
            if resp.status_code == 200:
                log.info(f"Telegram alert sent for VIN {listing.get('vin')}")
                return True
            else:
                log.warning(f"Telegram failed ({resp.status_code}): {resp.text}")
                return False
    except Exception as e:
        log.error(f"Error sending Telegram alert: {e}")
        return False

async def send_price_drop_alert(listing: dict, old_price: float, new_price: float) -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return False

    drop = old_price - new_price
    title = f"{listing.get('year', '')} Tesla {listing.get('model', '')} {listing.get('trim', '')}".strip()
    url = listing.get('url', '')

    text = (
        f"📉 *PRICE DROP DETECTED\\!*\n\n"
        f"*{escape_md(title)}*\n"
        f"🔻 Dropped *${int(drop):,}*\n"
        f"💰 Was ~${int(old_price):,}~ ➡️ Now *${int(new_price):,}*\n"
        f"🏪 {escape_md(listing.get('dealer', 'Dealer'))}\n"
    )

    payload: dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'MarkdownV2'
    }

    if url and url.startswith(('http://', 'https://')):
        payload['reply_markup'] = {
            'inline_keyboard': [[
                {'text': '🔗 Open Listing', 'url': url}
            ]]
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
            return resp.status_code == 200
    except Exception as e:
        log.error(f"Error sending price drop alert: {e}")
        return False

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select

from .database import ROOT, Session, Vehicle, Listing
from .scoring import check_hw4

REPO_DIR = ROOT / "data" / "tesla-feed-repo"

def get_all_active_deals():
    with Session() as s:
        all_listings = list(s.scalars(select(Listing).where(Listing.status == 'ACTIVE')))
        deals = []
        seen_keys = set()

        for l in all_listings:
            data = l.data or {}
            vin = l.vin or data.get('vin')
            if not vin:
                continue

            price = data.get('price')
            year = data.get('year') or 0
            is_hw4, tag = check_hw4(data)
            hw4_qualified = (year >= 2024) or (year == 2023 and is_hw4)

            title_str = str(data.get('title', '')).lower()
            desc_str = str(data.get('description', '')).lower()
            dealer_str = str(data.get('dealer', '')).lower()
            carfax_clean = data.get('carfax_clean_title')
            
            is_adverse = False
            if 'autosavvy' in dealer_str:
                is_adverse = True
            elif carfax_clean is False:
                is_adverse = True
            elif any(b in f"{title_str} {desc_str} {dealer_str}" for b in ['salvage', 'rebuilt', 'lemon', 'flood', 'total loss', 'frame damage', 'branded', 'junk', 'structural', 'trueframe']):
                is_adverse = True

            is_clean = not is_adverse

            key = (vin, l.source)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            deals.append({
                "vin": vin,
                "year": year,
                "model": data.get('model', 'Model Y'),
                "trim": data.get('trim', 'Standard'),
                "price": price,
                "mileage": data.get('mileage', 0),
                "dealer": data.get('dealer', 'Private/Dealer'),
                "city": data.get('city', ''),
                "state": data.get('state', 'TX'),
                "is_hw4": bool(hw4_qualified),
                "hw4_tag": tag,
                "under_33k": bool(price is not None and price <= 33000),
                "deal_score": data.get('deal_score', l.deal_score or 50),
                "title_status": "Clean" if is_clean else "Adverse",
                "url": data.get('url', l.url),
                "source": l.source
            })

    # Sort: Best deals first (HW4 under 33k on top, then by deal score descending)
    deals.sort(key=lambda x: (
        1 if (x['is_hw4'] and x['under_33k'] and x['title_status'] == 'Clean') else 0,
        x['deal_score'] or 0,
        -(x['price'] or 999999)
    ), reverse=True)

    return deals

def build_feed_files():
    deals = get_all_active_deals()
    now_iso = datetime.now(timezone.utc).isoformat()

    clean_deals = [d for d in deals if d['title_status'] == 'Clean']
    trash_vins = [d for d in deals if d['title_status'] != 'Clean']
    hw4_under_33k = [d for d in clean_deals if d['is_hw4'] and d['under_33k']]

    json_payload = {
        "updated_at": now_iso,
        "location": "North Texas / DFW (75022, 100mi radius)",
        "total_listings": len(deals),
        "total_hw4_under_33k": len(hw4_under_33k),
        "total_clean_listings": len(clean_deals),
        "total_trash_vins": len(trash_vins),
        "priority_hw4_under_33k": hw4_under_33k,
        "clean_listings": clean_deals,
        "trash_vins": trash_vins
    }

    # Markdown generation
    md_lines = [
        "# Used Tesla Deal Hunter - Live DFW Market Feed",
        f"**Last Sync**: {now_iso} (UTC)",
        f"**Total Listings**: {len(deals)} | **HW4 Under $33k Deals**: {len(hw4_under_33k)} | **Clean Titles**: {len(clean_deals)} | **Trash VINs Flagged**: {len(trash_vins)}",
        "",
        "## ⭐ Priority: Verified HW4 Under $33k (Clean Title)",
        "",
        "| Year/Model | Trim | Price | Mileage | Dealer & Location | HW4 Verification | Deal Score | Direct Dealer URL |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ]

    for d in hw4_under_33k:
        yr_mod = f"{d['year']} {d['model']}"
        pr = f"${d['price']:,}" if d['price'] else "N/A"
        mi = f"{d['mileage']:,} mi" if d['mileage'] else "N/A"
        loc = f"{d['city']}, {d['state']}".strip(', ')
        dlr = f"{d['dealer']} ({loc})" if loc else d['dealer']
        url = d['url']
        link_md = f"[View Listing]({url})" if url else "N/A"
        md_lines.append(f"| {yr_mod} | {d['trim']} | {pr} | {mi} | {dlr} | {d['hw4_tag']} | {d['deal_score']}/100 | {link_md} |")

    md_lines.extend([
        "",
        "## 🚗 Active Clean Market Inventory",
        "",
        "| Year/Model | Trim | Price | Mileage | HW4? | Dealer & Location | Direct Dealer URL |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ])

    for d in clean_deals:
        yr_mod = f"{d['year']} {d['model']}"
        pr = f"${d['price']:,}" if d['price'] else "Call"
        mi = f"{d['mileage']:,} mi" if d['mileage'] else "N/A"
        hw4_badge = "✅ HW4" if d['is_hw4'] else "HW3"
        loc = f"{d['city']}, {d['state']}".strip(', ')
        dlr = f"{d['dealer']} ({loc})" if loc else d['dealer']
        url = d['url']
        link_md = f"[View Listing]({url})" if url else "N/A"
        md_lines.append(f"| {yr_mod} | {d['trim']} | {pr} | {mi} | {hw4_badge} | {dlr} | {link_md} |")

    if trash_vins:
        md_lines.extend([
            "",
            "## 🗑️ Trash VINs (Quarantined: Salvage / Rebuilt / Lemon / Flood)",
            "> **DO NOT BUY**: These vehicles were flagged with adverse titles, total loss, salvage, or lemon status.",
            "",
            "| Year/Model | VIN | Price | Title Status | Dealer | URL |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"
        ])
        for d in trash_vins:
            yr_mod = f"{d['year']} {d['model']}"
            pr = f"${d['price']:,}" if d['price'] else "N/A"
            url = d['url']
            link_md = f"[View Listing]({url})" if url else "N/A"
            md_lines.append(f"| {yr_mod} | `{d['vin']}` | {pr} | ⚠️ {d['title_status']} | {d['dealer']} | {link_md} |")

    REPO_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPO_DIR / "tesla-feed.json"
    md_path = REPO_DIR / "tesla-feed.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    return json_path, md_path, len(deals), len(hw4_under_33k)

def publish_to_repo():
    json_path, md_path, total, hw4_count = build_feed_files()

    try:
        # Check if git is initialized in REPO_DIR
        if not (REPO_DIR / ".git").exists():
            subprocess.run(["git", "clone", "https://github.com/goforthj76/tesla-feed.git", str(REPO_DIR)], check=True)

        subprocess.run(["git", "-C", str(REPO_DIR), "add", "tesla-feed.json", "tesla-feed.md"], check=True)
        # Check if there are changes to commit
        diff_res = subprocess.run(["git", "-C", str(REPO_DIR), "diff", "--staged", "--quiet"])
        if diff_res.returncode != 0:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            subprocess.run(["git", "-C", str(REPO_DIR), "commit", "-m", f"Auto-sync feed: {total} listings ({hw4_count} HW4 < $33k) at {now_str}"], check=True)
            subprocess.run(["git", "-C", str(REPO_DIR), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(REPO_DIR), "push", "-u", "origin", "main"], check=True)

        return {
            "status": "success",
            "total_listings": total,
            "hw4_under_33k": hw4_count,
            "raw_md_url": "https://raw.githubusercontent.com/goforthj76/tesla-feed/main/tesla-feed.md",
            "raw_json_url": "https://raw.githubusercontent.com/goforthj76/tesla-feed/main/tesla-feed.json",
            "repo_url": "https://github.com/goforthj76/tesla-feed"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == '__main__':
    res = publish_to_repo()
    print(json.dumps(res, indent=2))

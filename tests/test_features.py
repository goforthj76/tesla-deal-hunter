import pytest
from app.scoring import score_deal, compute_market_averages
from app.notifier import escape_md

def test_telegram_markdown_v2_escaping():
    text = "Tesla Model 3 - $24,500! [Great Deal] (75022)"
    escaped = escape_md(text)
    assert r"\-" in escaped
    assert r"\!" in escaped
    assert r"\[" in escaped
    assert r"\]" in escaped
    assert r"\(" in escaped
    assert r"\)" in escaped

def test_deal_scoring_engine():
    comps = {
        ('Model 3', 2023): {'avg_price': 29000, 'avg_mileage': 35000, 'sample_count': 10}
    }
    settings = {
        'max_price': 32000,
        'ideal_max': 28000,
        'radius': 100
    }
    
    # Hot deal: $22,000 for a 2023 Model 3 with 18,000 miles, nearby, Tesla CPO
    hot_listing = {
        'vin': '5YJ3E1EA9SF900678',
        'year': 2023,
        'model': 'Model 3',
        'price': 22000,
        'mileage': 18000,
        'distance': 15,
        'dealer': 'Tesla Dallas',
        'source': 'Tesla.com',
        'description': 'Clean Tesla pre-owned'
    }
    res = score_deal(hot_listing, comps, settings)
    assert res['score'] >= 80
    assert res['category'] in ('HOT DEAL', 'GREAT DEAL')
    assert any('below market' in r for r in res['reasons'])

def test_deal_scoring_salvage_penalty():
    settings = {'max_price': 32000, 'ideal_max': 28000, 'radius': 100}
    salvage_listing = {
        'vin': '5YJ3E1EA9SF900678',
        'year': 2023,
        'model': 'Model 3',
        'price': 19000,
        'mileage': 25000,
        'description': 'Clean condition rebuilt salvage title from flood'
    }
    res = score_deal(salvage_listing, {}, settings)
    assert res['category'] == 'REJECTED'
    assert any('Title/accident' in r for r in res['risks'])

def test_market_averages_calculation():
    listings = [
        {'model': 'Model Y', 'year': 2023, 'price': 30000, 'mileage': 20000},
        {'model': 'Model Y', 'year': 2023, 'price': 32000, 'mileage': 30000},
        {'model': 'Model 3', 'year': 2022, 'price': 24000, 'mileage': 40000},
    ]
    averages = compute_market_averages(listings)
    assert ('Model Y', 2023) in averages
    assert averages[('Model Y', 2023)]['avg_price'] == 31000.0
    assert averages[('Model Y', 2023)]['avg_mileage'] == 25000.0
    assert averages[('Model Y', 2023)]['sample_count'] == 2

def test_hw4_notification_criteria():
    from app.scoring import check_hw4
    
    # 2024 Model 3 -> HW4 confirmed
    is_hw4, tag = check_hw4({'year': 2024, 'model': 'Model 3'})
    assert is_hw4
    assert 'HW4' in tag

    # 2023 Model Y with Austin HW4 VIN -> HW4 inferred
    is_hw4, tag = check_hw4({'year': 2023, 'model': 'Model Y', 'vin': '7SAYGDEE4PA195817'})
    assert is_hw4

    # 2023 Model 3 standard -> HW3 likely
    is_hw4, tag = check_hw4({'year': 2023, 'model': 'Model 3', 'vin': '5YJ3E1EA9PF410698'})
    assert not is_hw4

    # 2023 Model 3 with explicit dealer HW4 mention -> HW4 confirmed
    is_hw4, tag = check_hw4({'year': 2023, 'model': 'Model 3', 'description': 'Equipped with HW4 Autopilot hardware'})
    assert is_hw4


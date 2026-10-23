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
    assert res['category'] in ('REJECTED', 'TRASH VIN')
    assert any('TRASH VIN' in r or 'Title/accident' in r for r in res['risks'])

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



def test_autosavvy_branded_title_disqualification():
    from app.investigator import investigate
    p = {'latitude': 33.0146, 'longitude': -97.0969, 'radius': 50}
    obs = [{'source': 'MarketCheck', 'url': 'https://autosavvy.com/car', 'observed': '2026-10-22T01:00:00+00:00', 'data': {'vin': '7SAYGDEF1RA285373', 'dealer': 'Autosavvy Duncanville', 'price': 34800, 'trim': 'Performance'}}]
    res = investigate(obs, [], p)
    assert res['purchase_readiness'] == 'HOLD'
    assert any('AutoSavvy' in w for w in res['warnings'])
    assert not any('PERFORMANCE' in o for o in res['opportunities'])

def test_auction_salvage_disqualification():
    from app.investigator import investigate
    p = {'latitude': 33.0146, 'longitude': -97.0969, 'radius': 50}
    obs = [{'source': 'Dealer', 'url': 'https://example.com/car', 'observed': '2026-10-22T01:00:00+00:00', 'data': {'vin': '5YJ3E1EA1RF752665', 'dealer': 'Rideline', 'price': 29997, 'trim': 'Base'}}]
    claims = [{'field': 'history', 'value': 'Auction salvage/damage record detected: https://vehileaks.com/vehicle/2024-Tesla-Model-3-Standard-Range-5YJ3E1EA1RF752665', 'kind': 'history_report'}]
    res = investigate(obs, claims, p)
    assert res['purchase_readiness'] == 'HOLD'
    assert any('Adverse history' in w for w in res['warnings'])
    assert not any('MISIDENTIFIED' in o for o in res['opportunities'])

def test_vin_single_motor_trim_conflict():
    from app.investigator import investigate
    p = {'latitude': 33.0146, 'longitude': -97.0969, 'radius': 50}
    # 5YJ3E1EA4RF871911 has 8th char A (Single Motor RWD)
    obs = [{'source': 'Dealer', 'url': 'https://example.com/car', 'observed': '2026-10-22T01:00:00+00:00', 'data': {'vin': '5YJ3E1EA4RF871911', 'dealer': 'CarMax', 'price': 35998, 'trim': 'Base'}}]
    claims = [{'field': 'trim', 'value': 'Performance', 'kind': 'aggregator', 'source': 'Web snippet'}]
    res = investigate(obs, claims, p)
    assert any('CONFIGURATION CONFLICT' in w for w in res['warnings'])
    assert not any('Factory VIN motor code (A) indicates Performance' in o for o in res['opportunities'])

def test_highland_lr_awd_confirmation():
    from app.investigator import investigate
    p = {'latitude': 33.0146, 'longitude': -97.0969, 'radius': 50}
    # 5YJ3E1EB4RF833863 has 8th char B (Dual Motor Long Range AWD)
    obs = [{'source': 'Dealer', 'url': 'https://example.com/car', 'observed': '2026-10-22T01:00:00+00:00', 'data': {'vin': '5YJ3E1EB4RF833863', 'dealer': 'Rideline', 'price': 29997, 'trim': 'Base', 'drivetrain': 'RWD'}}]
    res = investigate(obs, [], p)
    assert any('POSSIBLE MISIDENTIFIED LONG RANGE' in o and 'motor code (B)' in o for o in res['opportunities'])

def test_model_y_motor_d_is_rwd():
    from app.investigator import decode_tesla_motor
    # 7SAYGDED8RF026428 has 8th char D
    info = decode_tesla_motor('7SAYGDED8RF026428')
    assert info['drivetrain'] == 'RWD'
    assert info['is_single'] is True
    assert info['is_dual'] is False


def test_lookup_vin_dossier():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    res = c.get('/api/lookup?q=5YJ3E1EB4RF833863')
    assert res.status_code == 200
    d = res.json()
    assert d['type'] == 'vin_dossier'
    assert d['vin'] == '5YJ3E1EB4RF833863'
    assert d['factory']['model'] == '3'
    assert d['factory']['is_highland'] is True
    assert d['factory']['is_dual'] is True
    assert 'comps' in d

def test_lookup_keyword_search():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    res = c.get('/api/lookup?q=Model 3')
    assert res.status_code == 200
    d = res.json()
    assert d['type'] == 'search_results'
    assert d['count'] > 0

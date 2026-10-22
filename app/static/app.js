let state = null;
let currentFilter = 'ALL';
const $ = (id) => document.getElementById(id);

const money = (n) =>
  n == null
    ? 'Price unknown'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n);

async function api(url, options) {
  const r = await fetch(url, options);
  if (!r.ok) throw Error(`Request failed (${r.status})`);
  return r.json();
}

async function refresh() {
  try {
    state = await api('/api/state');
    render();
  } catch (err) {
    $('status').textContent = 'Error connecting';
    $('status').style.color = '#f85149';
  }
}

function render() {
  if (!state) return;

  // Header status
  $('status').textContent = state.scanning ? `Scanning ${state.scanning}...` : 'Monitoring Active';
  $('status').style.color = state.scanning ? '#d29922' : '#3fb950';

  if (state.settings) {
    $('summary-sub').textContent = `Zip ${state.settings.zip_code || '75022'} · ${state.settings.radius}mi · Max ${money(state.settings.max_price)}`;
  }

  // Telegram status banner
  const tgBanner = $('tg-banner');
  const tgText = $('tg-status-text');
  if (state.telegram_configured) {
    tgText.textContent = `📱 Telegram Connected: Buzzing on deals with score >= ${state.settings?.telegram_alert_threshold || 70}`;
    tgBanner.style.borderColor = '#238636';
  } else {
    tgText.textContent = '⚠️ Telegram not configured in .env (add TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID for phone alerts)';
    tgBanner.style.borderColor = '#d29922';
  }

  // Stats Grid
  const statsEl = $('stats');
  statsEl.innerHTML = `
    <div class="stat-card"><strong>${state.stats?.listings_scanned || 0}</strong><span>Listings Tracked</span></div>
    <div class="stat-card"><strong>${state.stats?.unique_vins || 0}</strong><span>Unique VINs</span></div>
    <div class="stat-card"><strong>${state.vehicles?.filter(v => (v.deal_score || 0) >= 70).length || 0}</strong><span>Deals (Score 70+)</span></div>
    <div class="stat-card"><strong>${state.sources?.filter(s => s.status === 'HEALTHY' || s.status === 'SCANNING').length || 0} / ${state.sources?.length || 0}</strong><span>Sources Active</span></div>
  `;

  // Filters
  const filtersEl = $('filters');
  const categories = ['ALL', '⚡ HW4 / 2024+', 'HOT DEAL', 'GREAT DEAL', 'GOOD DEAL', 'FAIR', 'STARRED'];
  filtersEl.innerHTML = categories
    .map((c) => {
      let count = 0;
      if (c === 'ALL') count = state.vehicles.length;
      else if (c === 'STARRED') count = state.vehicles.filter((v) => v.watched).length;
      else if (c === '⚡ HW4 / 2024+') count = state.vehicles.filter((v) => (v.year && v.year >= 2024) || (v.hw4 && v.hw4.includes('HW4'))).length;
      else count = state.vehicles.filter((v) => v.category === c).length;
      return `<button class="filter-chip ${c === currentFilter ? 'selected' : ''}" data-cat="${c}">${c} (${count})</button>`;
    })
    .join('');

  // Vehicles List
  renderVehicles();

  // Sources tab
  const sourcesEl = $('source-list');
  if (sourcesEl && state.sources) {
    sourcesEl.innerHTML = state.sources
      .map(
        (s) => `
        <div class="source-card">
          <h4>${s.name}</h4>
          <p style="font-size:0.8rem; color:var(--muted)">Status: <b style="color:${s.status === 'HEALTHY' ? '#3fb950' : '#8b949e'}">${s.status}</b></p>
          <p style="font-size:0.8rem; color:var(--muted)">Found: ${s.observations} listings</p>
          <button class="sm-btn" style="margin-top:6px;" onclick="triggerScan('${s.name}')">Scan Now</button>
        </div>
      `
      )
      .join('');
  }

  // Activity Feed
  const actEl = $('activity-feed');
  if (actEl && state.activity) {
    actEl.innerHTML = state.activity
      .map((a) => `<div class="act-row"><span>[${a.at.slice(11, 19)}]</span> ${a.message}</div>`)
      .join('');
  }
}

function renderVehicles() {
  const query = ($('search')?.value || '').toLowerCase();
  const sort = $('sort')?.value || 'score';

  let list = state.vehicles.filter((v) => {
    if (currentFilter === 'STARRED') return v.watched;
    if (currentFilter === '⚡ HW4 / 2024+') {
      return (v.year && v.year >= 2024) || (v.hw4 && v.hw4.includes('HW4'));
    }
    if (currentFilter !== 'ALL' && v.category !== currentFilter) return false;
    if (!query) return true;
    const hay = [v.vin, v.model, v.trim, v.dealer, v.year, v.source].join(' ').toLowerCase();
    return hay.includes(query);
  });

  list.sort((a, b) => {
    if (sort === 'score') return (b.deal_score || 0) - (a.deal_score || 0);
    if (sort === 'price') return (a.price || 999999) - (b.price || 999999);
    if (sort === 'mileage') return (a.mileage || 999999) - (b.mileage || 999999);
    if (sort === 'newest') return new Date(b.first_seen) - new Date(a.first_seen);
    return 0;
  });

  $('count').textContent = `${list.length} Tesla deal(s) matching criteria`;

  const carsEl = $('cars');
  if (!list.length) {
    carsEl.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--muted)">No listings found matching this filter yet. Hit "⚡ Scan All Now" to pull fresh listings!</div>`;
    return;
  }

  carsEl.innerHTML = list
    .map((v) => {
      const score = v.deal_score || 50;
      let scoreClass = 'score-fair';
      if (score >= 82) scoreClass = 'score-hot';
      else if (score >= 70) scoreClass = 'score-great';
      else if (score >= 55) scoreClass = 'score-good';

      const reasonsPreview = (v.reasons || []).slice(0, 2).join(' · ');
      const img = v.image || 'https://digitalassets.tesla.com/compositor/?model=m3&view=STUD_FRONT34&size=600';

      return `
      <article class="car-card">
        <div class="car-img-wrap">
          <img src="${img}" alt="${v.year} ${v.model}" loading="lazy" onerror="this.src='https://digitalassets.tesla.com/compositor/?model=m3&view=STUD_FRONT34&size=600'">
          <div class="deal-score-badge ${scoreClass}">
            ★ ${score}/100
          </div>
        </div>
        <div class="car-body">
          <div class="price-row">
            <span class="car-price">${money(v.price)}</span>
            <span style="font-size:0.75rem; color:var(--muted)">${v.source}</span>
          </div>
          <h3>${v.year || ''} Tesla ${v.model}</h3>
          <p class="car-meta">${v.trim || 'Standard Range'}</p>
          <p class="car-meta">🛣️ ${v.mileage ? v.mileage.toLocaleString() + ' mi' : 'Unknown miles'} · 📍 ${v.distance ? v.distance + ' mi away' : 'Distance unverified'}</p>
          <div class="car-tags">
            <span class="tag">${v.category}</span>
            <span class="tag">${v.hw4 || 'Hardware info'}</span>
            <span class="tag">${v.dealer || 'Dealer'}</span>
          </div>
          ${reasonsPreview ? `<div class="reasons-box">💡 ${reasonsPreview}</div>` : ''}
          <div class="card-actions">
            <a href="${v.url}" target="_blank" rel="noopener noreferrer" class="link-btn">🔗 Go To Listing</a>
            <button class="star-btn" onclick="toggleWatch('${v.vin}')">${v.watched ? '★' : '☆'}</button>
          </div>
        </div>
      </article>
    `;
    })
    .join('');
}

async function toggleWatch(vin) {
  try {
    await api(`/api/vehicles/${vin}/watch`, { method: 'POST' });
    await refresh();
  } catch (err) {
    alert(err.message);
  }
}

async function triggerScan(source) {
  try {
    await api(`/api/scan/${source}`, { method: 'POST' });
    alert(`Started scan for ${source}`);
    refresh();
  } catch (err) {
    alert(err.message);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  refresh();
  setInterval(refresh, 5000);

  // Tab switching
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('selected'));
      document.querySelectorAll('.view-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('selected');
      const target = btn.dataset.view;
      if ($(target)) $(target).classList.add('active');
    });
  });

  // Filters delegation
  $('filters').addEventListener('click', (e) => {
    if (e.target.dataset.cat) {
      currentFilter = e.target.dataset.cat;
      render();
    }
  });

  $('search')?.addEventListener('input', renderVehicles);
  $('sort')?.addEventListener('change', renderVehicles);

  // Scan all button
  $('btn-scan-now')?.addEventListener('click', async () => {
    $('btn-scan-now').disabled = true;
    $('btn-scan-now').textContent = 'Scanning All...';
    try {
      await api('/api/scan-all', { method: 'POST' });
      setTimeout(refresh, 2000);
    } catch (err) {
      alert(err.message);
    } finally {
      $('btn-scan-now').disabled = false;
      $('btn-scan-now').textContent = '⚡ Scan All Now';
    }
  });

  // Telegram test button
  $('tg-test-btn')?.addEventListener('click', async () => {
    $('tg-test-btn').textContent = 'Testing...';
    try {
      const res = await api('/api/telegram/test', { method: 'POST' });
      if (res.success) {
        alert('✅ Test alert sent to Telegram! Check your phone.');
      } else {
        alert('❌ Telegram test failed: ' + res.error);
      }
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      $('tg-test-btn').textContent = 'Send Test Alert';
    }
  });

  // MarketCheck API key button
  const btnSaveMk = $('btn-save-mk');
  if (btnSaveMk) {
    btnSaveMk.onclick = async () => {
      const keyInput = $('pref-marketcheck-key');
      const key = keyInput ? keyInput.value.trim() : '';
      if (!key) return alert('Please enter a valid MarketCheck API key');
      try {
        btnSaveMk.textContent = 'Connecting...';
        await api('/api/marketcheck-key', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: key })
        });
        alert('MarketCheck API Key saved! Live dealership scan started.');
        $('mk-status-text').textContent = '✅ Connected! MarketCheck scan actively running in background.';
        $('mk-status-text').style.color = '#3fb950';
        btnSaveMk.textContent = 'Saved';
        setTimeout(refresh, 2000);
      } catch (e) {
        alert('Failed to save MarketCheck key: ' + e.message);
        btnSaveMk.textContent = 'Save Key';
      }
    };
  }

  // Load MarketCheck key status
  api('/api/marketcheck-key').then((r) => {
    if (r.configured) {
      if ($('mk-status-text')) {
        $('mk-status-text').textContent = `✅ Connected (${r.masked}). Live dealership scanning active.`;
        $('mk-status-text').style.color = '#3fb950';
      }
      if ($('pref-marketcheck-key')) {
        $('pref-marketcheck-key').placeholder = 'API Key Active (' + r.masked + ')';
      }
    }
  }).catch(() => {});

  // Settings form
  $('settings-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      zip_code: $('pref-zip').value,
      radius: parseInt($('pref-radius').value, 10),
      max_price: parseInt($('pref-max-price').value, 10),
      ideal_max: parseInt($('pref-ideal-price').value, 10),
      telegram_alert_threshold: parseInt($('pref-tg-threshold')?.value || '70', 10),
    };
    try {
      await api('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      alert('Settings saved!');
      refresh();
    } catch (err) {
      alert('Error saving settings: ' + err.message);
    }
  });
});

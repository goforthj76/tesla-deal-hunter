let state = null;
let currentFilter = 'ALL';
const $ = (id) => document.getElementById(id);

const money = (n) =>
  n == null
    ? 'Price unknown'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n);

function bangForBuck(v) {
  const price = v.price || 0;
  if (price <= 0 || price > 36000) return 0;
  if (v.category === 'TRASH VIN' || v.category === 'HOLD / REBUILT TITLE') return 0;
  if (v.investigation?.purchase_readiness === 'HOLD') return 0;
  const score = v.deal_score || 50;
  if (score < 60) return 0;
  const year = v.year || 2021;
  if (year < 2022) return 0;
  const mileage = v.mileage != null ? v.mileage : 60000;
  const yearFactor = Math.max(0.5, 1 + (year - 2022) * 0.25);
  const mileageFactor = Math.max(0.2, 1 - Math.min(mileage, 100000) / 250000);
  return (score * 1000 / price) * yearFactor * mileageFactor;
}

function hasOpportunity(v, keyword) {
  if (v.investigation?.purchase_readiness === 'HOLD' && keyword !== 'FSD') return false;
  return (v.investigation?.opportunities || []).some(o => o.toUpperCase().includes(keyword.toUpperCase()));
}

function isHiddenGem(v) {
  if (v.investigation?.purchase_readiness === 'HOLD') return false;
  if (v.category === 'HOLD / REBUILT TITLE' || v.category === 'TRASH VIN') return false;
  const opps = v.investigation?.opportunities || [];
  return opps.some(o => o.includes('PERFORMANCE') || o.includes('LONG RANGE') || o.includes('MISIDENTIFIED') || o.includes('AWD') || o.includes('DUAL MOTOR') || o.includes('FSD')) || (v.fsd && v.fsd.includes('PAID'));
}

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
    <div class="stat-card"><strong>${state.sources?.filter(s => s.automatically_scheduled && (s.status === 'HEALTHY' || s.status === 'SCANNING')).length || 0} / ${state.sources?.length || 0}</strong><span>Scheduled & Healthy</span></div>
  `;

  // Filters
  const filtersEl = $('filters');
  const categories = ['ALL', '✨ 2024 Only', '💎 Hidden Gems', '🚀 Hidden Performance', '⚡ Hidden AWD / LR', '🧠 Paid FSD Included', '🎯 Best Bang for Buck', '⚡ HW4 / 2024+', 'WATCH', 'MANUAL REVIEW', 'REJECTED', '🗑️ Trash VINs', 'STARRED'];
  filtersEl.innerHTML = categories
    .map((c) => {
      let count = 0;
      if (c === 'ALL') count = state.vehicles.filter((v) => v.category !== 'TRASH VIN').length;
      else if (c === 'STARRED') count = state.vehicles.filter((v) => v.watched).length;
      else if (c === '✨ 2024 Only') count = state.vehicles.filter((v) => v.year === 2024).length;
      else if (c === '💎 Hidden Gems') count = state.vehicles.filter(isHiddenGem).length;
      else if (c === '🚀 Hidden Performance') count = state.vehicles.filter(v => hasOpportunity(v, 'PERFORMANCE')).length;
      else if (c === '⚡ Hidden AWD / LR') count = state.vehicles.filter(v => hasOpportunity(v, 'AWD') || hasOpportunity(v, 'DUAL MOTOR') || hasOpportunity(v, 'LONG RANGE') || hasOpportunity(v, 'MISIDENTIFIED')).length;
      else if (c === '🧠 Paid FSD Included') count = state.vehicles.filter(v => hasOpportunity(v, 'FSD') || (v.fsd && v.fsd.includes('PAID'))).length;
      else if (c === '🎯 Best Bang for Buck') count = state.vehicles.filter((v) => bangForBuck(v) >= 1.1).length;
      else if (c === '⚡ HW4 / 2024+') count = state.vehicles.filter((v) => v.category !== 'TRASH VIN' && ((v.year && v.year >= 2024) || (v.hw4 && v.hw4.includes('HW4')))).length;
      else if (c === '🗑️ Trash VINs') count = state.vehicles.filter((v) => v.category === 'TRASH VIN').length;
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
          <p style="font-size:0.8rem; color:var(--muted)">${s.automatically_scheduled ? "Scheduled" : "Manual only"} · ${s.freshness || "UNKNOWN"} · ${s.observations} observations</p>
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
    if (currentFilter === '🗑️ Trash VINs') return v.category === 'TRASH VIN';
    if (v.category === 'TRASH VIN') return false;
    if (currentFilter === '✨ 2024 Only') {
      return v.year === 2024;
    }
    if (currentFilter === '💎 Hidden Gems') {
      return isHiddenGem(v);
    }
    if (currentFilter === '🚀 Hidden Performance') {
      return hasOpportunity(v, 'PERFORMANCE');
    }
    if (currentFilter === '⚡ Hidden AWD / LR') {
      return hasOpportunity(v, 'AWD') || hasOpportunity(v, 'DUAL MOTOR') || hasOpportunity(v, 'LONG RANGE') || hasOpportunity(v, 'MISIDENTIFIED');
    }
    if (currentFilter === '🧠 Paid FSD Included') {
      return hasOpportunity(v, 'FSD') || (v.fsd && v.fsd.includes('PAID'));
    }
    if (currentFilter === '🎯 Best Bang for Buck') {
      return bangForBuck(v) >= 1.1;
    }
    if (currentFilter === '⚡ HW4 / 2024+') {
      return (v.year && v.year >= 2024) || (v.hw4 && v.hw4.includes('HW4'));
    }
    if (currentFilter !== 'ALL' && v.category !== currentFilter) return false;
    if (!query) return true;
    const hay = [v.vin, v.model, v.trim, v.dealer, v.year, v.source].join(' ').toLowerCase();
    return hay.includes(query);
  });

  list.sort((a, b) => {
    if (sort === 'hidden_gems') {
      const aG = isHiddenGem(a) ? 1 : 0;
      const bG = isHiddenGem(b) ? 1 : 0;
      return (bG - aG) || ((b.deal_score || 0) - (a.deal_score || 0));
    }
    if (sort === 'performance') {
      const aP = hasOpportunity(a, 'PERFORMANCE') ? 1 : 0;
      const bP = hasOpportunity(b, 'PERFORMANCE') ? 1 : 0;
      return (bP - aP) || ((b.deal_score || 0) - (a.deal_score || 0));
    }
    if (sort === 'bang') return bangForBuck(b) - bangForBuck(a);
    if (sort === 'score') return Number(a.investigation?.purchase_readiness === 'HOLD') - Number(b.investigation?.purchase_readiness === 'HOLD') || (b.deal_score || 0) - (a.deal_score || 0);
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
            ${v.investigation?.purchase_readiness === 'HOLD' ? '<span class="tag" style="background:#5c1d1d; color:#ffa198; font-weight:700;">🚨 HOLD</span>' : ''}
            ${hasOpportunity(v, 'PERFORMANCE') ? '<span class="tag" style="background:#5c1d1d; color:#ff7b72; font-weight:700; border:1px solid #f85149;">🚀 Hidden Performance</span>' : ''}
            ${hasOpportunity(v, 'LONG RANGE') || hasOpportunity(v, 'MISIDENTIFIED') ? '<span class="tag" style="background:#1f4b2b; color:#7ee787; font-weight:700; border:1px solid #2ea043;">💎 Hidden Long Range</span>' : ''}
            ${hasOpportunity(v, 'AWD') || hasOpportunity(v, 'DUAL MOTOR') ? '<span class="tag" style="background:#1f3d59; color:#79c0ff; font-weight:700; border:1px solid #388bfd;">⚡ Hidden Dual Motor / AWD</span>' : ''}
            ${hasOpportunity(v, 'FSD') || (v.fsd && v.fsd.includes('PAID')) ? '<span class="tag" style="background:#492652; color:#e3b341; font-weight:700; border:1px solid #d29922;">🧠 Paid FSD Included</span>' : ''}
            ${bangForBuck(v) >= 1.1 ? '<span class="tag" style="background:#0f3822; color:#3fb950; font-weight:700; border:1px solid #238636;">🎯 Best Bang for Buck</span>' : ''}
            <span class="tag">${v.category}</span>
            <span class="tag">${v.hw4 || 'Hardware info'}</span>
            <span class="tag">${v.dealer || 'Dealer'}</span>
          </div>
          ${reasonsPreview ? `<div class="reasons-box">💡 ${reasonsPreview}</div>` : ''}
          <div class="card-actions">
            <a href="${v.url}" target="_blank" rel="noopener noreferrer" class="link-btn">🔗 Go To Listing</a>
            <button class="details-btn" onclick="showDetail('${v.vin}')">🔍 Details</button>
            <button class="star-btn" onclick="toggleWatch('${v.vin}')">${v.watched ? '★' : '☆'}</button>
          </div>
        </div>
      </article>
    `;
    })
    .join('');
}


async function showDetail(vin) {
  const v = (state?.vehicles || []).find(x => x.vin === vin);
  if (!v) return;

  $('modal-title').textContent = `${v.year || ''} Tesla ${v.model || ''} (${v.vin})`;
  const body = $('modal-body');
  const score = v.deal_score || 50;
  const scoreClass = score >= 82 ? 'score-hot' : score >= 70 ? 'score-great' : score >= 55 ? 'score-good' : 'score-fair';

  body.innerHTML = `
    <div style="margin-bottom: 16px;">
      <span class="deal-score-badge ${scoreClass}">
        ★ ${score}/100 — ${v.category}
      </span>
      <span style="font-size: 1.25rem; font-weight: 700; color: var(--green); margin-left: 12px;">${money(v.price)}</span>
    </div>

    <div class="detail-row"><span class="detail-label">VIN</span><span class="detail-val" style="font-family:monospace">${v.vin}</span></div>
    <div class="detail-row"><span class="detail-label">Trim & Drivetrain</span><span class="detail-val">${v.trim || 'Standard'} (${v.drivetrain || 'RWD'})</span></div>
    <div class="detail-row"><span class="detail-label">Mileage</span><span class="detail-val">${v.mileage ? v.mileage.toLocaleString() + ' miles' : 'Unknown'}</span></div>
    <div class="detail-row"><span class="detail-label">Distance</span><span class="detail-val">${v.distance ? v.distance + ' miles away' : 'Distance unverified'}</span></div>
    <div class="detail-row"><span class="detail-label">Dealer</span><span class="detail-val">${v.dealer || 'Private/Unknown'}</span></div>
    <div class="detail-row"><span class="detail-label">Source</span><span class="detail-val">${v.source} (${v.source_count} records)</span></div>
    <div class="detail-row"><span class="detail-label">Autopilot / Hardware</span><span class="detail-val">${v.hw4 || 'Hardware info'}</span></div>
    <div class="detail-row"><span class="detail-label">First Seen</span><span class="detail-val">${v.first_seen ? new Date(v.first_seen).toLocaleString() : 'Recent'}</span></div>

    ${(v.reasons && v.reasons.length) ? `
      <h4>Deal Factors</h4>
      <ul style="padding-left: 20px; color: #7ee787;">
        ${v.reasons.map(r => `<li>${r}</li>`).join('')}
      </ul>
    ` : ''}

    ${(v.risks && v.risks.length) ? `
      <h4>Risks & Inspection Notes</h4>
      <ul style="padding-left: 20px; color: #f85149;">
        ${v.risks.map(r => `<li>${r}</li>`).join('')}
      </ul>
    ` : ''}

    <h4>Observed Price History</h4>
    <div id="modal-price-history" style="color: var(--muted); font-size: 0.85rem;">Loading price history...</div>

    <div style="margin-top: 20px; display: flex; gap: 8px; flex-wrap: wrap;">
      <a href="/investigator?vin=${v.vin}" target="_blank" class="action-btn" style="background:#238636; color:#fff; text-decoration:none; display:inline-flex; align-items:center; padding:10px 14px; border-radius:6px; font-weight:600;">
        🔍 Open in VIN Investigator ↗
      </a>
      <a href="${v.url}" target="_blank" rel="noopener noreferrer" class="primary-btn" style="text-align: center; text-decoration: none; display: block; flex: 1;">
        Visit Original Listing →
      </a>
      <button class="action-btn" onclick="toggleWatch('${v.vin}')">${v.watched ? '★ Saved' : '☆ Save Deal'}</button>
    </div>
  `;

  $('detail-modal').showModal();

  try {
    const prices = await api(`/api/vehicles/${vin}/prices`);
    const histEl = $('modal-price-history');
    if (histEl) {
      if (prices && prices.length) {
        histEl.innerHTML = prices.map(p => `
          <div style="display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #21262d;">
            <span>${new Date(p.observed).toLocaleString()}</span>
            <strong style="color:var(--green)">${money(p.amount)}</strong>
          </div>
        `).join('');
      } else {
        histEl.textContent = 'No price changes recorded yet.';
      }
    }
  } catch (e) {
    if ($('modal-price-history')) $('modal-price-history').textContent = 'Price history unavailable.';
  }
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

async function loadDiscovery() {
  try {
    const disc = await api('/api/discovery');
    const statusEl = $('discovery-status');
    if (statusEl) {
      statusEl.textContent = disc.configured
        ? `Tavily configured. Month: ${disc.month}, Remaining calls: ${disc.remaining}/${disc.monthly_cap}. Status: ${disc.status || 'IDLE'}.`
        : 'Tavily API key not configured in .env (add TAVILY_API_KEY).';
    }
    const leadsEl = $('discovery-leads');
    if (leadsEl && disc.leads) {
      leadsEl.innerHTML = disc.leads.length
        ? disc.leads.slice(0, 20).map(l => `
          <div style="padding:10px; border-bottom:1px solid var(--border); font-size:0.85rem;">
            <a href="${l.url}" target="_blank" style="color:var(--accent); font-weight:600;">${l.title || l.url}</a>
            <p style="color:var(--muted); margin:4px 0;">${l.snippet || ''}</p>
            <span class="tag" style="background:#21262d; padding:2px 8px; border-radius:12px; font-size:0.75rem;">${l.town_query || 'Local'}</span>
            <span class="tag" style="background:#21262d; padding:2px 8px; border-radius:12px; font-size:0.75rem; color:#d29922;">${l.status || 'Unverified'}</span>
          </div>
        `).join('')
        : '<p style="color:var(--muted)">No discovery leads yet. Hit "Search When Due" to start discovery.</p>';
    }
  } catch (e) {}

  try {
    const seeds = await api('/api/research-seeds');
    const seedsEl = $('research-seeds');
    if (seedsEl && Array.isArray(seeds)) {
      seedsEl.innerHTML = seeds.map(s => `
        <div style="background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px;">
          <h4 style="margin-bottom:4px;">${s.dealer || s.name}</h4>
          <p style="font-size:0.8rem; color:var(--muted)">${s.city || ''}, ${s.state || 'TX'}</p>
          <a href="${s.inventory_url || s.url}" target="_blank" style="color:var(--accent); font-size:0.8rem; margin-top:6px; display:inline-block;">Visit Dealer Inventory →</a>
        </div>
      `).join('');
    }
  } catch (e) {}
}

window.addEventListener('DOMContentLoaded', () => {
  $('modal-close')?.addEventListener('click', () => $('detail-modal').close());
  $('detail-modal')?.addEventListener('click', (e) => {
    if (e.target === $('detail-modal')) $('detail-modal').close();
  });

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
      if (target === 'discovery') loadDiscovery();
    });
  });

  // Filters delegation
  $('filters').addEventListener('click', (e) => {
    if (e.target.dataset.cat) {
      currentFilter = e.target.dataset.cat;
      if (currentFilter === '💎 Hidden Gems' && $('sort')) {
        $('sort').value = 'hidden_gems';
      } else if (currentFilter === '🚀 Hidden Performance' && $('sort')) {
        $('sort').value = 'performance';
      } else if (currentFilter === '🎯 Best Bang for Buck' && $('sort')) {
        $('sort').value = 'bang';
      }
      render();
    }
  });

  $('search')?.addEventListener('input', renderVehicles);
  $('sort')?.addEventListener('change', renderVehicles);

  // Discovery run button
  $('discovery-run')?.addEventListener('click', async () => {
    $('discovery-run').disabled = true;
    $('discovery-run').textContent = 'Searching...';
    try {
      const res = await api('/api/discovery/run', { method: 'POST' });
      alert(`Discovery search: ${res.status || 'OK'}`);
      loadDiscovery();
    } catch (e) {
      alert(e.message);
    } finally {
      $('discovery-run').disabled = false;
      $('discovery-run').textContent = 'Search When Due';
    }
  });

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

// catalog-app.js — Catalog page logic

const API = '/api/v1';

const state = {
  cart: loadCart(),
  sessionToken: loadSessionToken(),
  products: [],
  activeCategory: '',
  query: '',
};

document.addEventListener('DOMContentLoaded', () => {
  const urlQ = new URLSearchParams(location.search).get('q') || '';
  if (urlQ) {
    state.query = urlQ;
    document.getElementById('find-bar-input').value = urlQ;
  }
  fetchCatalog();
  bindSearch();
  bindCart();
  renderCart();
});

// ─── Catalog ──────────────────────────────────────────────────────────────────

async function fetchCatalog() {
  try {
    const res = await fetch(`${API}/catalog`, {
      headers: { 'X-Session-Token': state.sessionToken },
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error);
    state.products = json.data.products || [];
    buildCategories();
    renderGrid();
  } catch (err) {
    document.getElementById('catalog-loading').textContent = `Error: ${err.message}`;
  }
}

function buildCategories() {
  const cats = [...new Set(state.products.map(p => p.category).filter(Boolean))].sort();
  const ul = document.getElementById('catalog-categories');
  cats.forEach(cat => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.className = 'cat-btn';
    btn.dataset.cat = cat;
    btn.textContent = cat;
    btn.addEventListener('click', () => setCategory(cat));
    li.appendChild(btn);
    ul.appendChild(li);
  });
  ul.querySelector('[data-cat=""]').addEventListener('click', () => setCategory(''));
}

function setCategory(cat) {
  state.activeCategory = cat;
  document.querySelectorAll('.cat-btn').forEach(b => {
    b.classList.toggle('cat-btn--active', b.dataset.cat === cat);
  });
  renderGrid();
}

function renderGrid() {
  const grid = document.getElementById('catalog-grid');
  const countEl = document.getElementById('catalog-count');
  document.getElementById('catalog-loading').classList.add('hidden');

  grid.querySelectorAll('.catalog-card').forEach(el => el.remove());

  let filtered = state.products;
  if (state.activeCategory) filtered = filtered.filter(p => p.category === state.activeCategory);
  if (state.query) {
    const q = state.query.toLowerCase();
    filtered = filtered.filter(p =>
      (p.name + ' ' + p.brand + ' ' + p.category).toLowerCase().includes(q)
    );
  }

  countEl.textContent = `${filtered.length} producto${filtered.length !== 1 ? 's' : ''}`;

  if (filtered.length === 0) {
    const msg = document.createElement('p');
    msg.className = 'catalog-loading';
    msg.textContent = 'Sin resultados.';
    grid.appendChild(msg);
    return;
  }

  filtered.forEach(p => {
    const card = document.createElement('div');
    card.className = 'catalog-card';
    card.innerHTML = `
      ${p.discount_pct > 0 ? `<span class="catalog-card__discount">-${Math.round(p.discount_pct)}%</span>` : ''}
      <img class="catalog-card__img" src="${esc(p.image_url)}" alt="${esc(p.name)}" loading="lazy" />
      <div class="catalog-card__body">
        <div class="catalog-card__name">${esc(p.name)}</div>
        <div class="catalog-card__brand">${esc(p.brand)}${p.package_size ? ' · ' + esc(p.package_size) : ''}</div>
        <div class="catalog-card__price-row">
          <span class="catalog-card__price">$${p.price.toFixed(2)}</span>
          ${p.discount_pct > 0 ? `<span class="catalog-card__list-price">$${p.list_price.toFixed(2)}</span>` : ''}
        </div>
      </div>
      <button class="catalog-card__add" aria-label="Agregar al carrito">+</button>
    `;
    card.querySelector('.catalog-card__add').addEventListener('click', () => addToCart(p));
    grid.appendChild(card);
  });
}

// ─── Search ───────────────────────────────────────────────────────────────────

function bindSearch() {
  const navInput = document.getElementById('find-bar-input');
  const navBtn   = document.getElementById('find-bar-btn');

  const doSearch = () => {
    state.query = navInput.value.trim();
    renderGrid();
    navInput.blur();
  };

  navBtn.addEventListener('click', doSearch);
  navInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } });
  navInput.addEventListener('input', () => { state.query = navInput.value.trim(); renderGrid(); });
}

// ─── Cart ─────────────────────────────────────────────────────────────────────

function addToCart(p) {
  const existing = state.cart.find(i => i.product_id === p.id);
  if (existing) {
    existing.quantity += 1;
  } else {
    state.cart.push({
      product_id: p.id,
      name: p.name,
      brand: p.brand,
      package_size: p.package_size || '',
      price: p.price,
      image_url: p.image_url,
      quantity: 1,
    });
  }
  saveCart();
  renderCart();
}

function removeFromCart(productId) {
  state.cart = state.cart.filter(i => i.product_id !== productId);
  saveCart();
  renderCart();
}

function updateCartQty(productId, delta) {
  const item = state.cart.find(i => i.product_id === productId);
  if (!item) return;
  item.quantity += delta;
  if (item.quantity <= 0) removeFromCart(productId);
  else { saveCart(); renderCart(); }
}

function renderCart() {
  const panel     = document.getElementById('cart-panel');
  const container = document.getElementById('cart-items');
  const emptyMsg  = document.getElementById('cart-empty-msg');
  const footer    = document.getElementById('cart-footer');
  const badge     = document.getElementById('cart-badge');
  const totalEl   = document.getElementById('cart-total');

  container.querySelectorAll('.cart-item').forEach(el => el.remove());

  const totalCount = state.cart.reduce((s, i) => s + i.quantity, 0);
  badge.textContent = totalCount;
  badge.classList.toggle('hidden', totalCount === 0);
  emptyMsg.classList.toggle('hidden', state.cart.length > 0);
  footer.classList.toggle('hidden', state.cart.length === 0);

  let total = 0;
  state.cart.forEach(item => {
    total += item.price * item.quantity;
    const el = document.createElement('div');
    el.className = 'cart-item';
    el.innerHTML = `
      <img class="cart-item__image" src="${esc(item.image_url)}" alt="${esc(item.name)}" />
      <div class="cart-item__info">
        <div class="cart-item__name">${esc(item.name)}</div>
        <div class="cart-item__brand">${esc(item.brand)} · ${esc(item.package_size)}</div>
        <div class="cart-item__price">$${item.price.toFixed(2)}</div>
      </div>
      <div class="cart-item__qty">
        <button data-action="dec" data-id="${esc(item.product_id)}">−</button>
        <span>${item.quantity}</span>
        <button data-action="inc" data-id="${esc(item.product_id)}">+</button>
      </div>
    `;
    container.insertBefore(el, emptyMsg);
  });

  totalEl.textContent = `$${total.toFixed(2)}`;
}

function bindCart() {
  document.getElementById('cart-items').addEventListener('click', e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    if (btn.dataset.action === 'inc') updateCartQty(btn.dataset.id, 1);
    if (btn.dataset.action === 'dec') updateCartQty(btn.dataset.id, -1);
  });

  document.getElementById('cart-close').addEventListener('click', () => {
    document.getElementById('cart-panel').classList.add('hidden');
  });

  document.getElementById('nav-cart-btn').addEventListener('click', () => {
    document.getElementById('cart-panel').classList.toggle('hidden');
  });

  document.getElementById('btn-checkout').addEventListener('click', checkout);
}

async function checkout() {
  if (state.cart.length === 0) return;
  const btn = document.getElementById('btn-checkout');
  btn.disabled = true;
  try {
    const res = await fetch(`${API}/orders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Token': state.sessionToken },
      body: JSON.stringify({ cart: state.cart }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error);
    state.cart = [];
    saveCart();
    renderCart();
    alert(`¡Pedido confirmado! #${json.data.order_id} — Total: $${json.data.total.toFixed(2)}`);
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function loadCart() {
  try { return JSON.parse(localStorage.getItem('cart') || '[]'); } catch { return []; }
}

function saveCart() {
  localStorage.setItem('cart', JSON.stringify(state.cart));
}

function loadSessionToken() {
  const existing = localStorage.getItem('chatSessionToken');
  if (existing) return existing;
  const created = window.crypto?.randomUUID?.() || `session-${Date.now()}`;
  localStorage.setItem('chatSessionToken', created);
  return created;
}

function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
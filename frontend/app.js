// supershop — Frontend state machine
// Owner: Mariia

const API = '/api/v1';

// ─── State ────────────────────────────────────────────────────────────────────
const state = {
  catalog: [],
  cart: loadCart(),
  chatHistory: [],
  currentTab: 'catalog',
  sessionToken: loadSessionToken(),
  clarification: null,
};

// ─── Boot ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  bindTabs();
  bindChat();
  bindCartEvents();
  bindClarificationForm();
  bindModalCancel();
  renderCart();
  loadCatalog();
});

// ─── Tabs ─────────────────────────────────────────────────────────────────────
function bindTabs() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  state.currentTab = tab;
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('tab--active', b.dataset.tab === tab));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('hidden', !p.id.endsWith(tab)));
}

// ─── Catalog ──────────────────────────────────────────────────────────────────
async function loadCatalog() {
  showCatalogLoading(true);
  try {
    const res = await fetch(`${API}/catalog`);
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || 'Catalog error');
    state.catalog = json.data.products || [];
    renderCatalog(state.catalog);
  } catch (err) {
    showCatalogError(err.message);
  } finally {
    showCatalogLoading(false);
  }
}

function renderCatalog(products) {
  const grid = document.getElementById('product-grid');
  const empty = document.getElementById('catalog-empty');

  // Clear previous cards (keep empty/loading nodes)
  grid.querySelectorAll('.product-card').forEach(el => el.remove());

  empty.classList.toggle('hidden', products.length > 0);

  products.forEach(p => {
    const card = document.createElement('div');
    card.className = 'product-card';
    card.innerHTML = `
      <img class="product-card__image" src="${esc(p.image_url)}" alt="${esc(p.name)}" />
      <div class="product-card__name">${esc(p.name)}</div>
      <div class="product-card__brand">${esc(p.brand)}</div>
      <div class="product-card__size">${esc(p.package_size)}</div>
      <div class="product-card__price">$${p.price.toFixed(2)}</div>
      ${p.discount_pct > 0 ? `<div class="product-card__discount">${p.discount_pct}% OFF</div>` : ''}
      <button class="product-card__add" data-id="${esc(p.id)}">Agregar</button>
    `;
    card.querySelector('button').addEventListener('click', () => addToCart(p));
    grid.appendChild(card);
  });
}

function showCatalogLoading(show) {
  document.getElementById('catalog-loading').classList.toggle('hidden', !show);
}

function showCatalogError(msg) {
  const empty = document.getElementById('catalog-empty');
  empty.textContent = `Error: ${msg}`;
  empty.classList.remove('hidden');
}

// ─── Cart ─────────────────────────────────────────────────────────────────────
function addToCart(product, qty = 1) {
  const existing = state.cart.find(i => i.product_id === product.id);
  if (existing) {
    existing.quantity += qty;
  } else {
    state.cart.push({
      product_id: product.id,
      name: product.name,
      brand: product.brand,
      package_size: product.package_size,
      price: product.price,
      quantity: qty,
      image_url: product.image_url,
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

function setCart(items) {
  // Called when the server returns an authoritative cart
  state.cart = items;
  saveCart();
  renderCart();
}

function renderCart() {
  const container = document.getElementById('cart-items');
  const empty = document.getElementById('cart-empty');
  const footer = document.getElementById('cart-footer');
  const countEl = document.getElementById('cart-count');
  const totalEl = document.getElementById('cart-total');

  container.querySelectorAll('.cart-item').forEach(el => el.remove());

  const totalCount = state.cart.reduce((s, i) => s + i.quantity, 0);
  countEl.textContent = totalCount;

  empty.classList.toggle('hidden', state.cart.length > 0);
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
    container.insertBefore(el, empty);
  });

  totalEl.textContent = `$${total.toFixed(2)}`;
}

function bindCartEvents() {
  document.getElementById('cart-items').addEventListener('click', e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.action === 'inc') updateCartQty(id, 1);
    if (btn.dataset.action === 'dec') updateCartQty(id, -1);
  });

  document.getElementById('btn-checkout').addEventListener('click', checkout);
}

async function checkout() {
  if (state.cart.length === 0) return;
  try {
    const res = await fetch(`${API}/orders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cart: state.cart }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error);
    state.cart = [];
    saveCart();
    renderCart();
    appendChatMsg('assistant', `¡Pedido confirmado! (#${json.data.order_id})`);
    switchTab('chat');
  } catch (err) {
    alert(`Error al confirmar pedido: ${err.message}`);
  }
}

function loadCart() {
  try { return JSON.parse(localStorage.getItem('cart') || '[]'); } catch { return []; }
}

function saveCart() {
  localStorage.setItem('cart', JSON.stringify(state.cart));
}

function loadSessionToken() {
  const existing = localStorage.getItem('chatSessionToken');
  if (existing) return existing;

  const created = window.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  localStorage.setItem('chatSessionToken', created);
  return created;
}

// ─── Chat ─────────────────────────────────────────────────────────────────────
function bindChat() {
  const input = document.getElementById('chat-input');
  const btn = document.getElementById('btn-send');

  btn.addEventListener('click', sendChat);
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } });
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  input.value = '';
  document.getElementById('chat-empty').classList.add('hidden');
  appendChatMsg('user', message);

  const loadingEl = appendChatMsg('loading', 'Escribiendo...');

  state.chatHistory.push({ role: 'user', content: message });

  try {
    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({
        message,
        history: state.chatHistory.slice(-20),
        cart: state.cart,
      }),
    });
    const json = await res.json();
    loadingEl.remove();

    if (!json.ok) throw new Error(json.error);

    const { reply, cart, clarification } = json.data;

    state.chatHistory.push({ role: 'assistant', content: reply });
    appendChatMsg('assistant', reply);

    if (clarification) {
      showClarificationModal(clarification);
    } else if (cart) {
      setCart(cart);
    }
  } catch (err) {
    loadingEl.remove();
    appendChatMsg('assistant', `Error: ${err.message}`);
  }
}

function appendChatMsg(role, text) {
  const thread = document.getElementById('chat-thread');
  const el = document.createElement('div');
  el.className = `chat-msg chat-msg--${role}`;
  el.textContent = text;
  thread.appendChild(el);
  thread.scrollTop = thread.scrollHeight;
  return el;
}

// ─── Clarification modal ──────────────────────────────────────────────────────
function showClarificationModal(clarification) {
  const modal = document.getElementById('clarification-modal');
  const question = document.getElementById('modal-question');
  const options = document.getElementById('modal-options');
  const errorEl = document.getElementById('modal-error');
  const confirmBtn = document.getElementById('modal-confirm');

  state.clarification = {
    ...clarification,
    selectedOptionId: null,
    submitting: false,
  };
  question.textContent = clarification.question;
  options.innerHTML = '';
  errorEl.textContent = '';
  errorEl.classList.add('hidden');
  confirmBtn.disabled = true;

  clarification.options.forEach(opt => {
    const choice = document.createElement('label');
    choice.className = 'modal__choice';
    choice.innerHTML = `
      <input type="radio" name="clarification-option" value="${esc(opt.id)}" />
      <img src="${esc(opt.product?.image_url || '')}" alt="${esc(opt.label)}" />
      <div>
        <div class="modal__option-label">${esc(opt.label)}</div>
        <div class="modal__option-detail">${esc(opt.product?.package_size || '')} · $${(opt.product?.price || 0).toFixed(2)}</div>
      </div>
    `;
    options.appendChild(choice);
  });

  modal.classList.remove('hidden');
  options.querySelector('input[name="clarification-option"]')?.focus();
}

async function resolveClarification() {
  if (!state.clarification || !state.clarification.selectedOptionId || state.clarification.submitting) return;

  const loadingEl = appendChatMsg('loading', 'Procesando selección...');
  const errorEl = document.getElementById('modal-error');
  const confirmBtn = document.getElementById('modal-confirm');
  const cancelBtn = document.getElementById('modal-cancel');
  const radios = document.querySelectorAll('input[name="clarification-option"]');
  const selectedOption = state.clarification.options.find(opt => opt.id === state.clarification.selectedOptionId);

  state.clarification.submitting = true;
  confirmBtn.disabled = true;
  cancelBtn.disabled = true;
  errorEl.textContent = '';
  errorEl.classList.add('hidden');
  radios.forEach(radio => { radio.disabled = true; });

  try {
    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({
        message: selectedOption?.label || '__clarification__',
        history: state.chatHistory.slice(-20),
        cart: state.cart,
        clarification_response: {
          pending_request_id: state.clarification.pending_request_id,
          chosen_option_id: state.clarification.selectedOptionId,
          pending_message: state.clarification.pending_message || '',
        },
      }),
    });
    const json = await res.json();
    loadingEl.remove();
    if (!json.ok) throw new Error(json.error);

    const { reply, cart, clarification } = json.data;
    if (selectedOption) {
      state.chatHistory.push({ role: 'user', content: selectedOption.label });
      appendChatMsg('user', selectedOption.label);
    }
    state.chatHistory.push({ role: 'assistant', content: reply });
    appendChatMsg('assistant', reply);
    if (clarification) {
      showClarificationModal(clarification);
    } else {
      closeClarificationModal();
      if (cart) setCart(cart);
    }
  } catch (err) {
    loadingEl.remove();
    state.clarification.submitting = false;
    confirmBtn.disabled = !state.clarification.selectedOptionId;
    cancelBtn.disabled = false;
    radios.forEach(radio => { radio.disabled = false; });
    errorEl.textContent = err.message;
    errorEl.classList.remove('hidden');
  }
}

function closeClarificationModal() {
  document.getElementById('clarification-modal').classList.add('hidden');
  document.getElementById('modal-error').textContent = '';
  document.getElementById('modal-error').classList.add('hidden');
  document.getElementById('modal-confirm').disabled = true;
  document.getElementById('modal-cancel').disabled = false;
  state.clarification = null;
}

function bindClarificationForm() {
  const form = document.getElementById('clarification-form');
  const options = document.getElementById('modal-options');
  const confirmBtn = document.getElementById('modal-confirm');

  form.addEventListener('submit', e => {
    e.preventDefault();
    resolveClarification();
  });

  options.addEventListener('change', e => {
    const radio = e.target.closest('input[name="clarification-option"]');
    if (!radio || !state.clarification) return;

    state.clarification.selectedOptionId = radio.value;
    confirmBtn.disabled = false;
    options.querySelectorAll('.modal__choice').forEach(choice => {
      const input = choice.querySelector('input[name="clarification-option"]');
      choice.classList.toggle('modal__choice--selected', Boolean(input?.checked));
    });
  });

  document.addEventListener('keydown', e => {
    const modal = document.getElementById('clarification-modal');
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeClarificationModal();
    }
  });
}

function bindModalCancel() {
  document.getElementById('modal-cancel').addEventListener('click', closeClarificationModal);
  document.querySelector('.modal__overlay')?.addEventListener('click', closeClarificationModal);
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function jsonHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Session-Token': state.sessionToken,
  };
}

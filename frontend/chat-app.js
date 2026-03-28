// supershop — Chat page logic
// Owner: Mariia

const API = '/api/v1';

const state = {
  chatHistory: [],
  cart: loadCart(),
  sessionToken: loadSessionToken(),
  clarification: null,
};

document.addEventListener('DOMContentLoaded', () => {
  bindChat();
  bindClarificationForm();
  bindModalCancel();
  bindCartEvents();
  renderCart();
});

// ─── Chat ─────────────────────────────────────────────────────────────────────

function bindChat() {
  const input = document.getElementById('chat-input');
  const btn = document.getElementById('btn-send');
  btn.addEventListener('click', sendChat);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  input.value = '';
  document.getElementById('chat-empty').classList.add('hidden');
  appendChatMsg('user', message);

  const loadingEl = appendChatMsg('loading', 'Pensando...');
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
  const wrapper = thread.closest('.chat-thread-wrapper');
  const el = document.createElement('div');
  el.className = `chat-msg chat-msg--${role}`;
  el.textContent = text;
  thread.appendChild(el);
  if (wrapper) wrapper.scrollTop = wrapper.scrollHeight;
  return el;
}

// ─── Clarification modal ──────────────────────────────────────────────────────

function showClarificationModal(clarification) {
  const modal = document.getElementById('clarification-modal');
  const question = document.getElementById('modal-question');
  const options = document.getElementById('modal-options');
  const errorEl = document.getElementById('modal-error');
  const confirmBtn = document.getElementById('modal-confirm');

  state.clarification = { ...clarification, selectedOptionId: null, submitting: false };
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
  if (!state.clarification?.selectedOptionId || state.clarification.submitting) return;

  const loadingEl = appendChatMsg('loading', 'Procesando selección...');
  const errorEl = document.getElementById('modal-error');
  const confirmBtn = document.getElementById('modal-confirm');
  const cancelBtn = document.getElementById('modal-cancel');
  const radios = document.querySelectorAll('input[name="clarification-option"]');
  const selectedOption = state.clarification.options.find(o => o.id === state.clarification.selectedOptionId);

  state.clarification.submitting = true;
  confirmBtn.disabled = true;
  cancelBtn.disabled = true;
  radios.forEach(r => { r.disabled = true; });

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
    radios.forEach(r => { r.disabled = false; });
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

  form.addEventListener('submit', e => { e.preventDefault(); resolveClarification(); });

  options.addEventListener('change', e => {
    const radio = e.target.closest('input[name="clarification-option"]');
    if (!radio || !state.clarification) return;
    state.clarification.selectedOptionId = radio.value;
    confirmBtn.disabled = false;
    options.querySelectorAll('.modal__choice').forEach(c => {
      c.classList.toggle('modal__choice--selected', Boolean(c.querySelector('input')?.checked));
    });
  });

  document.addEventListener('keydown', e => {
    const modal = document.getElementById('clarification-modal');
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeClarificationModal();
  });
}

function bindModalCancel() {
  document.getElementById('modal-cancel').addEventListener('click', closeClarificationModal);
  document.querySelector('.modal__overlay')?.addEventListener('click', closeClarificationModal);
}

// ─── Cart ─────────────────────────────────────────────────────────────────────

function setCart(items) {
  state.cart = items;
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
  const panel = document.getElementById('cart-panel');
  const container = document.getElementById('cart-items');
  const emptyMsg = document.getElementById('cart-empty-msg');
  const footer = document.getElementById('cart-footer');
  const badge = document.getElementById('cart-badge');
  const totalEl = document.getElementById('cart-total');

  // Remove previous items (keep empty msg node)
  container.querySelectorAll('.cart-item').forEach(el => el.remove());

  const totalCount = state.cart.reduce((s, i) => s + i.quantity, 0);

  // Badge on nav cart icon
  badge.textContent = totalCount;
  badge.classList.toggle('hidden', totalCount === 0);

  // Show/hide panel
  panel.classList.toggle('hidden', state.cart.length === 0);

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

function bindCartEvents() {
  document.getElementById('cart-items').addEventListener('click', e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.action === 'inc') updateCartQty(id, 1);
    if (btn.dataset.action === 'dec') updateCartQty(id, -1);
  });

  document.getElementById('cart-close').addEventListener('click', () => {
    document.getElementById('cart-panel').classList.add('hidden');
  });

  document.getElementById('nav-cart-btn').addEventListener('click', () => {
    if (state.cart.length === 0) return;
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cart: state.cart }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error);
    state.cart = [];
    saveCart();
    renderCart();
    appendChatMsg('assistant', `¡Pedido confirmado! (#${json.data.order_id}) — Total: $${json.data.total.toFixed(2)}`);
  } catch (err) {
    appendChatMsg('assistant', `Error al confirmar pedido: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

function loadCart() {
  try { return JSON.parse(localStorage.getItem('cart') || '[]'); } catch { return []; }
}

function saveCart() {
  localStorage.setItem('cart', JSON.stringify(state.cart));
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function loadSessionToken() {
  const existing = localStorage.getItem('chatSessionToken');
  if (existing) return existing;
  const created = window.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  localStorage.setItem('chatSessionToken', created);
  return created;
}

function jsonHeaders() {
  return { 'Content-Type': 'application/json', 'X-Session-Token': state.sessionToken };
}

function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
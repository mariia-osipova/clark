// supershop — Chat page logic
// Owner: Mariia

const API = '/api/v1';

const state = {
  chatHistory: [],
  cart: loadCart(),
  sessionToken: loadSessionToken(),
  clarification: null,
};


const audioState = {
  mediaRecorder: null,
  chunks: [],
  recording: false,
};


document.addEventListener('DOMContentLoaded', () => {

  bindFindBar();
  bindChat();
  bindClarificationForm();
  bindModalCancel();
  bindCartEvents();
  bindAudio();
  bindImageUpload();
  bindCatalog();
  bindDemoChips();
  renderCart();
});

// ─── Chat ─────────────────────────────────────────────────────────────────────

function bindFindBar() {
  const input = document.getElementById('find-bar-input');
  const btn   = document.getElementById('find-bar-btn');
  if (!input) return;
  const go = () => {
    const q = input.value.trim();
    location.href = `catalog.html${q ? '?q=' + encodeURIComponent(q) : ''}`;
  };
  btn.addEventListener('click', go);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
}

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

    const { reply, cart, clarification, proposed_cart } = json.data;
    state.chatHistory.push({ role: 'assistant', content: reply });
    appendChatMsg('assistant', reply);

    if (clarification) {
      showClarificationModal(clarification);
    } else if (cart) {
      setCart(cart);
    } else if (proposed_cart && proposed_cart.length > 0) {
      setCart(proposed_cart);
      syncCartToServer(proposed_cart);
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

async function removeFromCart(productId) {
  state.cart = state.cart.filter(i => i.product_id !== productId);
  saveCart();
  renderCart();
  // Sync deletion to server so the DB doesn't resurrect the item on next chat turn
  try {
    await fetch(`${API}/cart/remove`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ product_id: productId, session_id: state.sessionToken }),
    });
  } catch (_) { /* best-effort — local state already updated */ }
}

async function updateCartQty(productId, delta) {
  const item = state.cart.find(i => i.product_id === productId);
  if (!item) return;
  item.quantity += delta;
  if (item.quantity <= 0) {
    await removeFromCart(productId);
  } else {
    saveCart();
    renderCart();
  }
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
        <button data-action="remove" data-id="${esc(item.product_id)}" class="cart-item__remove" aria-label="Eliminar">✕</button>
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
    if (btn.dataset.action === 'remove') removeFromCart(id);
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
      headers: jsonHeaders(),
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

async function syncCartToServer(items) {
  try {
    await fetch(`${API}/cart/sync`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ items: items.map(i => ({ product_id: i.product_id, quantity: i.quantity })) }),
    });
  } catch (_) { /* best-effort */ }
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

// ─── Demo chips ───────────────────────────────────────────────────────────────

function bindDemoChips() {
  const chips = document.getElementById('demo-chips');
  if (!chips) return;
  chips.addEventListener('click', e => {
    const chip = e.target.closest('.demo-chip');
    if (!chip) return;
    const msg = chip.dataset.msg;
    if (!msg) return;
    document.getElementById('chat-input').value = msg;
    sendChat();
  });
}

// ─── Catalog ──────────────────────────────────────────────────────────────────

const catalogState = {
  products: [],
  loaded: false,
};

function bindCatalog() {
  document.getElementById('nav-catalog-btn').addEventListener('click', openCatalog);
  document.getElementById('catalog-close').addEventListener('click', closeCatalog);
  document.getElementById('catalog-search').addEventListener('input', () => {
    renderCatalogGrid(catalogState.products);
  });
}

function openCatalog() {
  document.getElementById('catalog-panel').classList.remove('hidden');
  document.getElementById('nav-catalog-btn').classList.add('active');
  if (!catalogState.loaded) loadCatalog();
}

function closeCatalog() {
  document.getElementById('catalog-panel').classList.add('hidden');
  document.getElementById('nav-catalog-btn').classList.remove('active');
}

async function loadCatalog() {
  const emptyEl = document.getElementById('catalog-empty');
  emptyEl.textContent = 'Cargando catálogo...';
  emptyEl.classList.remove('hidden');

  try {
    const res = await fetch(`${API}/catalog`);
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || 'Error al cargar catálogo');
    catalogState.products = json.data.products || [];
    catalogState.loaded = true;
    renderCatalogGrid(catalogState.products);
  } catch (err) {
    emptyEl.textContent = `Error: ${err.message}`;
  }
}

function renderCatalogGrid(products) {
  const grid = document.getElementById('catalog-grid');
  const emptyEl = document.getElementById('catalog-empty');
  const query = document.getElementById('catalog-search').value.trim().toLowerCase();

  // Remove existing cards
  grid.querySelectorAll('.product-card').forEach(el => el.remove());

  const filtered = query
    ? products.filter(p =>
        p.name.toLowerCase().includes(query) ||
        (p.brand || '').toLowerCase().includes(query)
      )
    : products;

  if (filtered.length === 0) {
    emptyEl.textContent = query ? 'Sin resultados.' : 'No hay productos disponibles.';
    emptyEl.classList.remove('hidden');
    return;
  }

  emptyEl.classList.add('hidden');

  filtered.forEach(p => {
    const card = document.createElement('div');
    card.className = 'product-card';
    const discountBadge = p.discount_pct > 0
      ? `<span class="product-card__discount">-${p.discount_pct}%</span>`
      : '';
    card.innerHTML = `
      <img class="product-card__image" src="${esc(p.image_url || '')}" alt="${esc(p.name)}" loading="lazy" />
      <div class="product-card__name">${esc(p.name)}</div>
      <div class="product-card__meta">${esc(p.brand || '')}${p.package_size ? ' · ' + esc(p.package_size) : ''}</div>
      <div class="product-card__price">$${(p.price || 0).toFixed(2)}${discountBadge}</div>
    `;
    grid.insertBefore(card, emptyEl);
  });
}

// ─── Audio (STT via Whisper) ──────────────────────────────────────────────────

function bindAudio() {
  const micBtn = document.getElementById('btn-mic');
  if (!micBtn) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    micBtn.disabled = true;
    micBtn.title = 'Micrófono no disponible en este navegador';
  } else {
    micBtn.addEventListener('click', toggleRecording);
  }
}

async function toggleRecording() {
  if (audioState.recording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioState.chunks = [];
    audioState.mediaRecorder = new MediaRecorder(stream);
    audioState.mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0) audioState.chunks.push(e.data);
    };
    audioState.mediaRecorder.onstop = () => {
      const blob = new Blob(audioState.chunks, { type: 'audio/webm' });
      stream.getTracks().forEach(t => t.stop());
      transcribeAudio(blob);
    };
    audioState.mediaRecorder.start();
    audioState.recording = true;
    const btn = document.getElementById('btn-mic');
    btn.classList.add('btn--recording');
    btn.textContent = '⏹';
    btn.title = 'Detener grabación';
  } catch (err) {
    appendChatMsg('assistant', `No se pudo acceder al micrófono: ${err.message}`);
  }
}

function stopRecording() {
  if (audioState.mediaRecorder && audioState.recording) {
    audioState.mediaRecorder.stop();
    audioState.recording = false;
    const btn = document.getElementById('btn-mic');
    btn.classList.remove('btn--recording');
    btn.textContent = '🎤';
    btn.title = 'Grabar mensaje de voz';
  }
}

async function transcribeAudio(blob) {
  const micBtn = document.getElementById('btn-mic');
  micBtn.disabled = true;
  micBtn.textContent = '⏳';
  try {
    const res = await fetch(`${API}/transcribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/webm', 'X-Session-Token': state.sessionToken },
      body: blob,
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error);
    const text = (json.data.text || '').trim();
    if (text) {
      document.getElementById('chat-input').value = text;
      sendChat();
    }
  } catch (err) {
    appendChatMsg('assistant', `Error al transcribir audio: ${err.message}`);
  } finally {
    micBtn.disabled = false;
    micBtn.textContent = '🎤';
  }
}

// ─── Image Upload (Vision AI) ─────────────────────────────────────────────────

function bindImageUpload() {
  const imgBtn = document.getElementById('btn-image');
  const fileInput = document.getElementById('image-file-input');
  if (!imgBtn || !fileInput) return;

  imgBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    fileInput.value = '';
    if (file) describeImage(file);
  });
}

async function describeImage(file) {
  const imgBtn = document.getElementById('btn-image');
  imgBtn.disabled = true;
  imgBtn.textContent = '⏳';
  try {
    const arrayBuffer = await file.arrayBuffer();
    const res = await fetch(`${API}/describe-image`, {
      method: 'POST',
      headers: {
        'Content-Type': file.type || 'image/jpeg',
        'X-Session-Token': state.sessionToken,
      },
      body: arrayBuffer,
    });
    const json = await res.json();
    if (!json.ok) {
      appendChatMsg('assistant', `No pude leer la imagen: ${json.error}`);
      return;
    }
    const text = (json.data.text || '').trim();
    if (text) {
      document.getElementById('chat-input').value = text;
      sendChat();
    }
  } catch (err) {
    appendChatMsg('assistant', `Error al procesar la imagen: ${err.message}`);
  } finally {
    imgBtn.disabled = false;
    imgBtn.textContent = '📷';
  }
}
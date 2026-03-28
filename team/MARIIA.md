# Mariia — Frontend & UX

You are assisting Mariia (also goes by Mari). Her focus is the layout system, chat UX, cart UX, clarification popup, visual polish, and demo quality.

## Files she owns
- `frontend/index.html` — full page structure
- `frontend/styles.css` — visual system (layout, typography, spacing, cards)
- `frontend/app.js` — vanilla JS state machine (tabs, cart rendering, chat thread, localStorage)

## Current version: VERSION4 active

## V3 ✅
- [x] Clarification popup renders image, brand, size, price per option
- [x] Modal transition: chat reply → option selection → updated cart
- [ ] Discount badge in clarification modal option cards (carried to V4 — not yet visible in modal)

## Current focus (V4)
- [ ] Add discount badge to clarification modal option cards (`opt.product?.discount_pct > 0 ? '${discount_pct}% OFF' : ''`)
- [ ] Design and build the monthly configuration tab (household size, budget, priority items, preferred brands, excluded categories, notes)
- [ ] Before/after preview: proposed monthly cart vs last saved plan or last order
- [ ] Approval, override, and re-run flows — make them extremely clear and fast

## How to help Mariia
- When she describes a layout or interaction, implement it in the appropriate frontend file.
- Keep all JS in `app.js` — no additional JS files unless she explicitly asks.
- No CSS frameworks — vanilla CSS only.
- No JS frameworks — vanilla JS only.
- `localStorage` is the state persistence layer for the frontend.
- Cross-reference [docs/api.md](../docs/api.md) for the shape of data the backend returns before writing rendering logic.
- Log any significant UI decisions or state shape changes in [docs/LOG.md](../docs/LOG.md).

## Key conventions
- Tab structure: Catalog | Cart | Chat | future Monthly Buys tab
- Empty states must be designed and visible — not just blank divs.
- Loading states must be visible during API calls.
- Cart total must always be computed client-side from cart items and confirmed by server response.
- The JS state machine in `app.js` drives all UI transitions — keep it readable and flat.

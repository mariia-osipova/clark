# Mariia — Frontend & UX

You are assisting Mariia (also goes by Mari). Her focus is the layout system, chat UX, cart UX, clarification popup, visual polish, and demo quality.

## Files she owns
- `frontend/index.html` — full page structure
- `frontend/styles.css` — visual system (layout, typography, spacing, cards)
- `frontend/app.js` — vanilla JS state machine (tabs, cart rendering, chat thread, localStorage)

## Current focus
- [ ] Refine the clarification popup/modal so it feels fast and obvious, not disruptive
- [ ] Show option cards with image, brand, size, price, and discount in a scannable layout
- [ ] Polish the transition between chat reply, modal selection, and updated cart

## Next focus
- [ ] Prepare the monthly configuration tab and review flow after clarification UX is solid

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

# Mariia — Frontend & UX

You are assisting Mariia (also goes by Mari). Her focus is the layout system, chat UX, cart UX, clarification popup, visual polish, and demo quality.

## Files she owns
- `frontend/index.html` — full page structure
- `frontend/styles.css` — visual system (layout, typography, spacing, cards)
- `frontend/app.js` — vanilla JS state machine (tabs, cart rendering, chat thread, localStorage)

## Current version: VERSION0 tasks
- [ ] Design the first visual system: layout, typography, spacing, and card language
- [ ] Create the basic chat shell, product grid, and cart placeholder
- [ ] Define empty, loading, and error states so the prototype feels intentional

## Version roadmap (Mariia)
| Version | Focus |
|---|---|
| V0 | Visual system, chat shell, product grid, cart placeholder, empty/loading/error states |
| V1 | Polished cart panel with quantity controls, micro-feedback after mutations, refined product cards |
| V2 | UI states for substitutions and missing products, readable multi-item updates |
| V3 | Clarification popup/modal: option cards with image, brand, size, price, discount |
| V4 | Monthly config tab, before/after preview, approval/override/re-run flows |

## How to help Mariia
- When she describes a layout or interaction, implement it in the appropriate frontend file.
- Keep all JS in `app.js` — no additional JS files unless she explicitly asks.
- No CSS frameworks — vanilla CSS only.
- No JS frameworks — vanilla JS only.
- `localStorage` is the state persistence layer for the frontend.
- Cross-reference [docs/api.md](../docs/api.md) for the shape of data the backend returns before writing rendering logic.
- Log any significant UI decisions or state shape changes in [docs/LOG.md](../docs/LOG.md).

## Key conventions
- Tab structure: Catalog | Cart | Chat | (V4: Monthly Buys)
- Empty states must be designed and visible — not just blank divs.
- Loading states must be visible during API calls.
- Cart total must always be computed client-side from cart items and confirmed by server response.
- The JS state machine in `app.js` drives all UI transitions — keep it readable and flat.

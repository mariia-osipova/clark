# API Contracts

All endpoints under `/api/v1/`. All responses use the standard envelope.

**Standard envelope:**
```json
{ "ok": true, "data": {}, "error": null, "request_id": "uuid" }
```

---

## GET /api/v1/catalog

Returns the normalized product catalog.

**Response `data`:**
```json
{
  "products": [
    {
      "id": "string",
      "name": "string",
      "brand": "string",
      "package_size": "string",
      "price": 0.00,
      "image_url": "string",
      "available_quantity": 0,
      "category": "string",
      "discount_pct": 0.0
    }
  ],
  "total": 0
}
```

---

## POST /api/v1/chat

Send a chat message. Returns assistant reply, authoritative cart state, clarification metadata when needed, and missing ingredient tracking.

**Header:** `X-Session-Token: <anonymous-chat-session-token>`

**Request body:**
```json
{
  "message": "string",
  "history": [
    { "role": "user|assistant", "content": "string" }
  ],
  "cart": [],
  "action": "generate_monthly_basket",
  "clarification_response": {
    "pending_request_id": "string",
    "chosen_option_id": "string"
  }
}
```

`clarification_response` is optional and should only be sent when the user is answering a previously returned clarification prompt. When it is sent, `X-Session-Token` must match the session that received the original clarification.
`action` is optional. `generate_monthly_basket` bypasses the normal chat graph and returns a deterministic proposal built from the recurring plan plus order history.

**Response `data`:**
```json
{
  "reply": "string",
  "cart": [],
  "clarification": null,
  "missing_items": [],
  "dropped_items": []
}
```

`dropped_items` is always present (may be empty). It lists product names that were in the request cart but were dropped because they are out-of-stock or unknown. The frontend should remove these from localStorage when the field is non-empty.

**Cart item shape:**
```json
{
  "product_id": "string",
  "name": "string",
  "brand": "string",
  "package_size": "string",
  "price": 0.00,
  "quantity": 1,
  "image_url": "string"
}
```

**Clarification response:** when `ok` is true but `data.clarification` is present, the UI should show the clarification modal instead of updating the cart:
```json
{
  "reply": "string",
  "cart": null,
  "clarification": {
    "question": "string",
    "options": [
      { "id": "string", "label": "string", "product": { } }
    ],
    "pending_request_id": "string"
  },
  "missing_items": []
}
```

Notes:
- When `clarification` is present, `reply` mirrors the clarification question so the chat transcript stays readable.
- **Clarification lifecycle:** (1) Server issues clarification → persists `pending_request_id` in `pending_clarifications` table. (2) Client sends `clarification_response` with same `pending_request_id`. (3) Server validates: must exist and be unresolved for this session. Stale or unknown IDs return `400 "stale or unknown clarification request"`.
- `missing_items` contains normalized ingredient/product names the agent could not find while decomposing a recipe or broad shopping goal.
- Server-side history is trimmed by character budget before calling the model; clients can still send full local history.
- When `action` is `generate_monthly_basket`, the response may also include `proposed_cart` and `cart` stays `null`.

**Cart authority model:** `session_carts` in the DB is the authoritative source. `localStorage` is a display cache. On every server response that includes `cart`, the frontend must replace (not merge) localStorage with the returned value. On page load, `GET /api/v1/cart?session_id=...` must be called and the result must replace localStorage.

---

## POST /api/v1/auth/register — NOT IMPLEMENTED

Returns `501 Not Implemented`. Stubbed until after the hackathon.

## POST /api/v1/auth/login — NOT IMPLEMENTED

Returns `501 Not Implemented`. Stubbed until after the hackathon.

---

## GET /api/v1/orders

Requires header: `X-Session-Token: <token>`

**Response data:** `{ "orders": [ { "id": "string", "items": [], "total": 0.00, "created_at": "iso8601" } ] }`

## POST /api/v1/orders

Place a new order from the server-side session cart. The cart in the request body is ignored; the server reads from `session_carts` (authoritative source).

**Header:** `X-Session-Token: <session-token>` (or `session_id` in body as fallback)
**Request:** `{ "session_id": "string" }` (optional if header is present)
**Response data:** `{ "order_id": "string", "total": 0.00 }`
**Errors:** `400` if session not provided or server cart is empty.

---

## GET /api/v1/preferences

Returns saved user preferences.

**Response data:**
```json
{
  "preferences": {
    "preferred_brands": { "leche": "La Serenísima" },
    "excluded_categories": ["bebidas alcohólicas"],
    "notes": "string"
  },
  "updated_at": "iso8601 | null"
}
```

## PUT /api/v1/preferences

Save or replace user preferences.

**Request:** `{ "preferences": { ...prefs object... } }`
**Response data:** `{ "preferences": { ...saved prefs... } }`

---

---

## GET /api/v1/cart?session_id=\<id\>

Returns the server-side cart for a session.

**Response data:** `{ "session_id": "string", "items": [ { "product_id": "string", "quantity": 1 } ] }`

## POST /api/v1/cart

Add or update an item in the server-side cart. Upserts on (session_id, product_id).

**Request:** `{ "session_id": "string", "product_id": "string", "quantity": 1 }`
**Response data:** `{ "session_id": "string", "product_id": "string", "quantity": 1 }`

## POST /api/v1/cart/remove

Remove an item from the server-side cart.

**Request:** `{ "session_id": "string", "product_id": "string" }`
**Response data:** `{ "removed": true }`

---

## GET /api/v1/recurring-plan

Returns the saved recurring monthly shopping configuration (single default plan until auth lands).

**Response data:** `{ "plan": { "household_size": 1, "monthly_budget": null, "priority_items": [], "preferred_brands": {}, "strict_brand": false, "excluded_categories": [], "notes": "", "updated_at": "iso8601" } }`

Returns `{ "plan": {} }` if no plan has been saved yet.

## POST /api/v1/recurring-plan

Save or replace the recurring plan configuration.

**Request:** `{ "plan": { "household_size": 2, "monthly_budget": 50000, "priority_items": ["p_id_1"], "preferred_brands": { "leche": "La Serenísima" }, "strict_brand": false, "excluded_categories": ["alcohol"], "notes": "string" } }`
**Response data:** `{ "plan": { ...saved plan... } }`

## POST /api/v1/recurring-plan/generate

Generate a proposed monthly cart from saved config + order history + current catalog.
Calls `generate_monthly_basket_candidates()` from `product_semantic_index.py`.

**Response data:**
```json
{
  "proposed_cart": [
    {
      "product_id": "string",
      "name": "string",
      "brand": "string",
      "package_size": "string",
      "price": 0.00,
      "quantity": 1,
      "image_url": "string",
      "tag": "must_have | recurring | offer | suggested"
    }
  ],
  "total": 0.00,
  "budget_exceeded": false
}
```

`budget_exceeded` is `true` when the sum of proposed items exceeds the plan's `monthly_budget`. It is `false` when no budget is set.

## POST /api/v1/recurring-plan/accept

Save a proposed cart as a new order (same validation as `POST /api/v1/orders`).

**Request:** `{ "proposed_cart": [ <cart items> ] }`
**Response data:** `{ "order_id": "string", "total": 0.00, "items": 0 }`

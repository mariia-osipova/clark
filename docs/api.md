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

Send a chat message. Returns assistant reply and (from V1) authoritative cart state.

**Request body:**
```json
{
  "message": "string",
  "history": [
    { "role": "user|assistant", "content": "string" }
  ],
  "cart": []
}
```

**Response `data`:**
```json
{
  "reply": "string",
  "cart": []
}
```

**Cart item shape (V1+):**
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

**Clarification response (V3+):** when `ok` is true but `data.clarification` is present, the UI should show the clarification modal instead of updating the cart:
```json
{
  "reply": "string",
  "clarification": {
    "question": "string",
    "options": [
      { "id": "string", "label": "string", "product": { } }
    ],
    "pending_request_id": "string"
  }
}
```

---

## POST /api/v1/auth/register

**Request:** `{ "email": "string", "password": "string" }`
**Response data:** `{ "token": "string", "user_id": "string" }`

## POST /api/v1/auth/login

**Request:** `{ "email": "string", "password": "string" }`
**Response data:** `{ "token": "string", "user_id": "string" }`

---

## GET /api/v1/orders

Requires header: `X-Session-Token: <token>`

**Response data:** `{ "orders": [ { "id": "string", "items": [], "total": 0.00, "created_at": "iso8601" } ] }`

## POST /api/v1/orders

Place a new order from the current cart.

**Request:** `{ "cart": [ <cart items> ] }`
**Response data:** `{ "order_id": "string", "total": 0.00 }`

---

## POST /api/v1/monthly-plan (V4)

Save or update recurring monthly shopping configuration.

**Request:**
```json
{
  "household_size": 2,
  "monthly_budget": 50000,
  "priority_items": ["leche", "arroz"],
  "preferred_brands": { "leche": "La Serenísima" },
  "strict_brand": false,
  "excluded_categories": ["bebidas alcohólicas"],
  "notes": "string"
}
```

## POST /api/v1/monthly-plan/generate (V4)

Trigger monthly cart generation from saved config + order history + current catalog.

**Response data:** `{ "proposed_cart": [], "summary": { "repeated": [], "swapped": [], "skipped": [], "new": [] } }`

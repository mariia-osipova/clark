# V4 Agent Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the prompt-driven product decision loop with a deterministic tool layer (`resolve_product` + `add_to_cart`), simplify the system prompt to 4 routing rules, and add a monthly basket generation action.

**Architecture:** `resolve_product` (already in `backend/product_semantic_index.py`) replaces `search_products`, returning a verdict dict that the agent routes without reasoning. `add_to_cart` writes directly to the `session_carts` SQLite table (no HTTP round-trip). Monthly basket generation is a short-circuit path in `handle_chat()` that calls `generate_monthly_basket_candidates()` directly and returns a proposed cart without invoking the LangGraph graph.

**Tech Stack:** Python 3, LangGraph, LangChain OpenAI, SQLite via `backend/db.py`, pytest

---

## File Map

| File | Change |
|---|---|
| `backend/chat_agent_agentic.py` | Main changes: new tools, simplified prompt, session_id param, monthly basket action |
| `backend/app.py` | Forward `session_id` and `action` from request body to `handle_chat()` |
| `tests/test_chat_agent.py` | New tests for each behavior; existing tests updated where signatures change |

---

## Task 1: Thread `session_id` from `app.py` into `handle_chat()`

`handle_chat()` currently has no concept of session. `add_to_cart` (Task 2) needs it to write to the right DB row.

**Files:**
- Modify: `backend/chat_agent_agentic.py` — add `session_id: str = ""` param
- Modify: `backend/app.py` — extract and forward `session_id` from request body
- Test: `tests/test_chat_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `TestHandleChat` class in `tests/test_chat_agent.py`:

```python
@patch("backend.chat_agent_agentic._load_catalog")
@patch("backend.chat_agent_agentic._build_graph")
def test_handle_chat_accepts_session_id(self, mock_build_graph, mock_load_catalog, sample_catalog):
    from backend.chat_agent_agentic import handle_chat, _reset_app_cache
    _reset_app_cache()
    mock_load_catalog.return_value = sample_catalog
    mock_app = MagicMock()
    mock_app.invoke.return_value = {
        "messages": [LCAIMessage(content="Hola")],
        "result_cart": None,
        "clarification": None,
        "missing_items": [],
    }
    mock_build_graph.return_value = mock_app
    # Should not raise; session_id is accepted as a keyword arg
    result = handle_chat(message="hola", history=[], cart=[], session_id="sess-abc")
    assert result["reply"] == "Hola"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_chat_agent.py::TestHandleChat::test_handle_chat_accepts_session_id -v
```

Expected: `TypeError: handle_chat() got an unexpected keyword argument 'session_id'`

- [ ] **Step 3: Add `session_id` parameter to `handle_chat()`**

In `backend/chat_agent_agentic.py`, change the signature at line ~351:

```python
def handle_chat(
    message: str,
    history: list[dict],
    cart: list[dict],
    clarification_response: dict | None = None,
    context: str | None = None,
    session_id: str = "",
) -> dict[str, Any]:
```

- [ ] **Step 4: Run to verify it passes**

```bash
pytest tests/test_chat_agent.py::TestHandleChat::test_handle_chat_accepts_session_id -v
```

Expected: PASS

- [ ] **Step 5: Forward `session_id` from `app.py`**

In `backend/app.py`, update `_handle_chat()` to extract and pass `session_id`:

```python
def _handle_chat(self, body: dict):
    req_id = str(uuid.uuid4())
    message, history, cart, clarification_response, err = _validate_chat_body(body)
    if err:
        _log.warning("chat [%s] bad request: %s", req_id, err)
        self.send_json(envelope(error=err, request_id=req_id), 400)
        return
    session_id = str(body.get("session_id", "") or "")
    action = str(body.get("action", "") or "")
    try:
        from backend.chat_agent_agentic import handle_chat
        _log.info("chat [%s] message=%r clarification=%s", req_id, message[:60], clarification_response is not None)
        result = handle_chat(
            message=message,
            history=history,
            cart=cart,
            clarification_response=clarification_response,
            context=_assemble_chat_context(),
            session_id=session_id,
            action=action or None,
        )
        _log.info("chat [%s] ok cart_items=%d clarification=%s", req_id, len(result.get("cart") or []), result.get("clarification") is not None)
        self.send_json(envelope(data=result, request_id=req_id))
    except Exception as e:
        _log.error("chat [%s] error: %s", req_id, e, exc_info=True)
        self.send_json(envelope(error=str(e), request_id=req_id), 500)
```

(The `action` param is added here now; `handle_chat()` will accept it in Task 4.)

- [ ] **Step 6: Run the full test suite to confirm nothing broke**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/chat_agent_agentic.py backend/app.py tests/test_chat_agent.py
git commit -m "feat(v4/agent): thread session_id through handle_chat"
```

---

## Task 2: Add `add_to_cart` tool + read cart from DB after graph

Replace `set_cart` with a server-side `add_to_cart` tool that writes one item at a time to `session_carts`. After the graph completes, `handle_chat()` reads the full cart from DB and hydrates it with catalog data.

**Files:**
- Modify: `backend/chat_agent_agentic.py` — add `add_to_cart` tool, replace `set_cart`, add `_read_session_cart()` helper
- Test: `tests/test_chat_agent.py`

**Key context:**
- `session_carts` schema: `(session_id TEXT, product_id TEXT, quantity INTEGER, PRIMARY KEY (session_id, product_id))`
- `get_db()` is imported from `backend.db` and returns a `sqlite3.Connection` with `row_factory = sqlite3.Row`
- When `session_id` is empty, fall back to the old in-memory `result_cart` from graph state (backward compat for callers without session)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_chat_agent.py` (in `TestHandleChat` class):

```python
@patch("backend.chat_agent_agentic._load_catalog")
@patch("backend.chat_agent_agentic._build_graph")
def test_add_to_cart_tool_writes_to_db(self, mock_build_graph, mock_load_catalog, sample_catalog):
    """add_to_cart tool should upsert into session_carts and return confirmation."""
    from backend.chat_agent_agentic import _make_tools, _reset_app_cache
    _reset_app_cache()
    import tempfile, os
    from backend import db as _db
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        orig = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = tmp_path
        _db.init_db()
        tools = _make_tools(sample_catalog, session_id="sess-xyz")
        tool = next(t for t in tools if t.name == "add_to_cart")
        result = json.loads(tool.invoke({"product_id": "p1", "quantity": 2}))
        assert result["added"] is True
        assert result["product_id"] == "p1"
        conn = _db.get_db()
        row = conn.execute(
            "SELECT quantity FROM session_carts WHERE session_id=? AND product_id=?",
            ("sess-xyz", "p1"),
        ).fetchone()
        conn.close()
        assert row["quantity"] == 2
    finally:
        if orig is not None:
            os.environ["DATABASE_PATH"] = orig
        elif "DATABASE_PATH" in os.environ:
            del os.environ["DATABASE_PATH"]
        os.unlink(tmp_path)
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_chat_agent.py::TestHandleChat::test_add_to_cart_tool_writes_to_db -v
```

Expected: `TypeError: _make_tools() got an unexpected keyword argument 'session_id'` or `StopIteration` (no add_to_cart tool).

- [ ] **Step 3: Add `session_id` to `_make_tools()` and implement `add_to_cart`**

In `backend/chat_agent_agentic.py`, update `_make_tools` signature and add the tool. Also remove `set_cart` and add the `_read_session_cart` helper.

Replace the `_make_tools` function entirely:

```python
def _read_session_cart(session_id: str, catalog: list[dict]) -> list[dict]:
    """Read session cart from DB and hydrate with current catalog data."""
    if not session_id:
        return []
    from backend.db import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT product_id, quantity FROM session_carts WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    catalog_by_id = {p["id"]: p for p in catalog}
    result = []
    for row in rows:
        p = catalog_by_id.get(row["product_id"])
        if p and p.get("available_quantity", 1) > 0:
            result.append(_build_cart_item(p, row["quantity"]))
    return result


def _make_tools(catalog: list[dict], session_id: str = ""):
    """Return LangChain tool objects closed over the catalog and session."""

    @tool
    def resolve_product(query: str, quantity: int = 1) -> str:
        """Resolve a single product from a natural-language query.
        Returns a verdict dict: {status, product, quantity} or {status, options, quantity}.
        Route based on status: resolved → add_to_cart; needs_clarification → request_clarification; not_found → report_missing."""
        from backend.product_semantic_index import resolve_product as _resolve
        verdict = _resolve(query, quantity, catalog)
        return json.dumps(verdict)

    @tool
    def add_to_cart(product_id: str, quantity: int = 1) -> str:
        """Add or update one item in the session cart.
        product_id must come from a resolve_product verdict with status=resolved.
        quantity must be a positive integer."""
        catalog_by_id = {p["id"]: p for p in catalog}
        p = catalog_by_id.get(product_id)
        if not p:
            return json.dumps({"added": False, "error": "product not found"})
        if p.get("available_quantity", 1) <= 0:
            return json.dumps({"added": False, "error": "out of stock"})
        qty = max(1, int(quantity))
        if session_id:
            from backend.db import get_db
            conn = get_db()
            try:
                conn.execute(
                    """INSERT INTO session_carts (session_id, product_id, quantity)
                       VALUES (?, ?, ?)
                       ON CONFLICT(session_id, product_id) DO UPDATE SET quantity = excluded.quantity""",
                    (session_id, product_id, qty),
                )
                conn.commit()
            finally:
                conn.close()
        return json.dumps({
            "added": True,
            "product_id": product_id,
            "name": p.get("name", ""),
            "quantity": qty,
            "price": p["price"],
        })

    @tool
    def request_clarification(question: str, options: list[dict]) -> str:
        """Ask the user to choose between ambiguous product options before proceeding.
        Returns acknowledgement and the pending request id."""
        pending_id = str(uuid.uuid4())
        return json.dumps({
            "acknowledged": True,
            "pending_request_id": pending_id,
            "question": question,
            "options": options,
        })

    @tool
    def report_missing(ingredient: str) -> str:
        """Record an ingredient or product that could not be found in the catalog.
        ingredient: the name of the item that is unavailable."""
        return json.dumps({"recorded": True, "ingredient": ingredient})

    return [resolve_product, add_to_cart, request_clarification, report_missing]
```

- [ ] **Step 4: Update `_build_graph` to pass `session_id` and remove `set_cart` side-effect from `tools_node`**

Update `_build_graph` signature:

```python
def _build_graph(catalog: list[dict], api_key: str, session_id: str = ""):
    tools = _make_tools(catalog, session_id=session_id)
    ...
```

In `tools_node`, remove the `search_products` interception block and the `set_cart` capture block entirely. The new `tools_node` side-effect section should be:

```python
def tools_node(state: AgentState) -> dict:
    last_msg = state["messages"][-1]
    clarification = state.get("clarification")
    missing_items = list(state.get("missing_items") or [])
    new_messages = []

    for tc in last_msg.tool_calls:
        if clarification:
            break
        tool_fn = tools_by_name[tc["name"]]
        try:
            result = tool_fn.invoke(tc["args"])
        except Exception:
            result = json.dumps({"error": "tool execution failed"})
        new_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

        if tc["name"] == "request_clarification":
            parsed = json.loads(result)
            pending_message = ""
            for msg in reversed(state["messages"]):
                if isinstance(msg, HumanMessage):
                    pending_message = msg.content
                    break
            clarification = {
                "question": parsed.get("question", tc["args"].get("question", "¿Cuál preferís?")),
                "options": parsed.get("options", tc["args"].get("options", [])),
                "pending_request_id": parsed.get("pending_request_id"),
                "pending_message": pending_message,
            }
        elif tc["name"] == "report_missing":
            ingredient = tc["args"].get("ingredient", "").strip().lower()
            if ingredient and ingredient not in missing_items:
                missing_items.append(ingredient)

    return {
        "messages": new_messages,
        "result_cart": None,   # cart lives in DB now; read after graph
        "clarification": clarification,
        "missing_items": missing_items,
    }
```

- [ ] **Step 5: Update `_get_or_build_app` to thread `session_id`**

The cache currently keys on `api_key`. With `session_id`, we cannot cache the graph (each session would need a different closure). Change the cache to rebuild the graph per-call, caching only the catalog:

```python
_catalog_cache: tuple[list[dict], float] | None = None  # (catalog, mtime)

def _get_catalog() -> list[dict]:
    """Return catalog, reloading if the file changed."""
    global _catalog_cache
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except FileNotFoundError:
        return []
    if _catalog_cache is None or _catalog_cache[1] != mtime:
        _catalog_cache = (_load_catalog(), mtime)
    return _catalog_cache[0]


def _get_or_build_app(api_key: str, session_id: str = ""):
    """Build and return the compiled graph for this request."""
    catalog = _get_catalog()
    app = _build_graph(catalog, api_key, session_id=session_id)
    return app, catalog
```

Also update `_reset_app_cache`:

```python
def _reset_app_cache() -> None:
    global _catalog_cache
    _catalog_cache = None
```

> **Note:** Building the graph per-call adds a few ms of Python overhead but is negligible for a hackathon demo. The LLM latency dominates.

- [ ] **Step 6: Update `handle_chat()` to read cart from DB after graph**

Replace the final graph invocation and return block. After `app.invoke(...)`, read the cart:

```python
    final_state = app.invoke(
        {
            "messages": init_messages,
            "result_cart": None,
            "clarification": None,
            "missing_items": [],
        },
        config={"recursion_limit": 30},
    )

    last_msg = final_state["messages"][-1]
    clarification = final_state.get("clarification")
    if isinstance(last_msg, AIMessage) and last_msg.content:
        reply = last_msg.content
    elif clarification:
        reply = clarification.get("question", "Necesito una aclaración para continuar.")
    else:
        reply = "Lo siento, no pude completar la acción."

    cart_result = _read_session_cart(session_id, _catalog) if session_id else final_state.get("result_cart")

    return {
        "reply": reply,
        "cart": cart_result,
        "clarification": clarification,
        "missing_items": final_state.get("missing_items") or [],
    }
```

Also update the clarification short-circuit path to write to DB:

```python
    if clarification_response:
        chosen_id = clarification_response.get("chosen_option_id", "")
        product = next((p for p in _catalog if p.get("id") == chosen_id), None)
        if product:
            qty = 1
            if session_id:
                from backend.db import get_db
                conn = get_db()
                try:
                    conn.execute(
                        """INSERT INTO session_carts (session_id, product_id, quantity)
                           VALUES (?, ?, ?)
                           ON CONFLICT(session_id, product_id) DO UPDATE SET quantity = excluded.quantity""",
                        (session_id, chosen_id, qty),
                    )
                    conn.commit()
                finally:
                    conn.close()
                validated_cart = _read_session_cart(session_id, _catalog)
            else:
                new_items = list(cart) + [{"product_id": product["id"], "quantity": qty}]
                validated_cart, _ = _validate_cart_with_report(new_items, _catalog)

            pending_msg = clarification_response.get("pending_message", "").strip()

            if not pending_msg:
                product_label = " ".join(filter(None, [product.get("name", ""), product.get("package_size", "")]))
                return {
                    "reply": f"Listo, agregué {product_label} al carrito.",
                    "cart": validated_cart,
                    "clarification": None,
                    "missing_items": [],
                }

            # Multi-item: continue graph with resolved product in context
            product_label = " ".join(filter(None, [product.get("name", ""), product.get("package_size", "")]))
            cont_messages: list = [SystemMessage(content=_build_system_prompt())]
            if validated_cart:
                cart_lines = "\n".join(
                    f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}"
                    for i in validated_cart
                )
                cont_messages.append(SystemMessage(content=f"Carrito actual:\n{cart_lines}"))
            if context:
                cont_messages.append(SystemMessage(content=context))
            cont_messages.append(SystemMessage(content=(
                f"Ya resolviste la ambigüedad: el usuario eligió '{product_label}' "
                f"(product_id={product['id']}) y fue agregado al carrito. "
                "Continuá procesando el pedido original. "
                "No vuelvas a buscar ese producto."
            )))
            cont_messages.append(HumanMessage(content=pending_msg))

            cont_state = app.invoke(
                {"messages": cont_messages, "result_cart": None, "clarification": None, "missing_items": []},
                config={"recursion_limit": 30},
            )

            last_msg = cont_state["messages"][-1]
            cont_clar = cont_state.get("clarification")
            if isinstance(last_msg, AIMessage) and last_msg.content:
                reply = last_msg.content
            elif cont_clar:
                reply = cont_clar.get("question", "Necesito una aclaración para continuar.")
            else:
                reply = f"Listo, agregué {product_label} al carrito."

            final_cart = _read_session_cart(session_id, _catalog) if session_id else cont_state.get("result_cart") or validated_cart
            return {
                "reply": reply,
                "cart": final_cart,
                "clarification": cont_clar,
                "missing_items": cont_state.get("missing_items") or [],
            }
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/test_chat_agent.py -v --tb=short 2>&1 | tail -30
```

Fix any failures from the `set_cart` removal — existing tests that call `set_cart` need to be updated to use `add_to_cart`. See below.

**Update existing `test_set_cart_tool_updates_cart`** — rename and rewrite to test `add_to_cart`:

```python
@patch("backend.chat_agent_agentic._load_catalog")
@patch("backend.chat_agent_agentic._build_graph")
def test_add_to_cart_reflected_in_reply(self, mock_build_graph, mock_load_catalog, sample_catalog):
    from backend.chat_agent_agentic import handle_chat, _reset_app_cache
    _reset_app_cache()
    mock_load_catalog.return_value = sample_catalog
    mock_app = MagicMock()
    mock_app.invoke.return_value = {
        "messages": [LCAIMessage(content="Agregué la leche.")],
        "result_cart": None,
        "clarification": None,
        "missing_items": [],
    }
    mock_build_graph.return_value = mock_app
    # No session_id → cart comes from result_cart (None here, that's fine)
    result = handle_chat(message="quiero leche", history=[], cart=[])
    assert result["reply"] == "Agregué la leche."
```

- [ ] **Step 8: Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "feat(v4/agent): replace set_cart with add_to_cart, read cart from DB"
```

---

## Task 3: Replace `search_products` with `resolve_product` routing + simplify `tools_node`

The `tools_node` currently intercepts `search_products` results and runs `build_clarification_candidates` inline. With `resolve_product`, the verdict already contains `needs_clarification` or `resolved` — the agent routes based on the returned JSON, and `tools_node` only needs to capture side effects of `request_clarification` and `report_missing`.

Task 2 already removed `search_products` and its interception from `tools_node`. This task verifies that `resolve_product` verdicts drive the right agent behavior end-to-end.

**Files:**
- Modify: `tests/test_chat_agent.py` — add routing tests

- [ ] **Step 1: Write tests for resolve_product → add_to_cart routing**

```python
@patch("backend.chat_agent_agentic._load_catalog")
@patch("backend.chat_agent_agentic._build_graph")
def test_resolve_product_tool_returns_verdict(self, mock_build_graph, mock_load_catalog, sample_catalog):
    """resolve_product tool wraps product_semantic_index.resolve_product."""
    from backend.chat_agent_agentic import _make_tools, _reset_app_cache
    _reset_app_cache()
    mock_load_catalog.return_value = sample_catalog
    mock_build_graph.return_value = MagicMock()
    with patch("backend.product_semantic_index.resolve_product") as mock_resolve:
        mock_resolve.return_value = {
            "status": "resolved",
            "product": sample_catalog[0],
            "quantity": 1,
            "substituted": False,
        }
        tools = _make_tools(sample_catalog)
        tool = next(t for t in tools if t.name == "resolve_product")
        result = json.loads(tool.invoke({"query": "leche", "quantity": 1}))
        assert result["status"] == "resolved"
        assert result["product"]["id"] == "p1"

@patch("backend.chat_agent_agentic._load_catalog")
@patch("backend.chat_agent_agentic._build_graph")
def test_resolve_product_not_found_returns_not_found(self, mock_build_graph, mock_load_catalog, sample_catalog):
    from backend.chat_agent_agentic import _make_tools, _reset_app_cache
    _reset_app_cache()
    mock_load_catalog.return_value = sample_catalog
    mock_build_graph.return_value = MagicMock()
    with patch("backend.product_semantic_index.resolve_product") as mock_resolve:
        mock_resolve.return_value = {"status": "not_found", "quantity": 1}
        tools = _make_tools(sample_catalog)
        tool = next(t for t in tools if t.name == "resolve_product")
        result = json.loads(tool.invoke({"query": "caviar beluga", "quantity": 1}))
        assert result["status"] == "not_found"
```

- [ ] **Step 2: Run to verify they pass** (they should pass if Task 2 is done)

```bash
pytest tests/test_chat_agent.py -k "resolve_product" -v
```

Expected: PASS

- [ ] **Step 3: Verify `search_products` no longer exists in tools**

```python
def test_search_products_tool_no_longer_exists(self):
    from backend.chat_agent_agentic import _make_tools
    tools = _make_tools([])
    tool_names = [t.name for t in tools]
    assert "search_products" not in tool_names
    assert "resolve_product" in tool_names
    assert "add_to_cart" in tool_names
```

- [ ] **Step 4: Run**

```bash
pytest tests/test_chat_agent.py -k "no_longer_exists" -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_chat_agent.py
git commit -m "test(v4/agent): verify resolve_product routing replaces search_products"
```

---

## Task 4: Simplify system prompt to 4 routing rules

The current prompt (~40 lines) tells the LLM how to handle multi-product flows, when to substitute, format rules, etc. With `resolve_product` handling all product decisions, the prompt only needs to tell the agent how to route verdicts.

**Files:**
- Modify: `backend/chat_agent_agentic.py` — replace `_build_system_prompt()`
- Modify: `tests/test_chat_agent.py` — update/add prompt tests

- [ ] **Step 1: Update prompt tests to reflect new behavior**

Remove or update `test_prompt_instructs_search_before_add` and `test_prompt_mentions_cart_tool`. Add:

```python
class TestSystemPrompt:
    def test_prompt_is_in_spanish(self):
        prompt = _build_system_prompt()
        assert "español" in prompt.lower() or "asistente" in prompt.lower()

    def test_prompt_mentions_resolve_product(self):
        prompt = _build_system_prompt()
        assert "resolve_product" in prompt

    def test_prompt_mentions_add_to_cart(self):
        prompt = _build_system_prompt()
        assert "add_to_cart" in prompt

    def test_prompt_mentions_request_clarification(self):
        prompt = _build_system_prompt()
        assert "request_clarification" in prompt

    def test_prompt_mentions_report_missing(self):
        prompt = _build_system_prompt()
        assert "report_missing" in prompt

    def test_prompt_does_not_mention_search_products(self):
        prompt = _build_system_prompt()
        assert "search_products" not in prompt
```

- [ ] **Step 2: Run to verify new tests fail**

```bash
pytest tests/test_chat_agent.py::TestSystemPrompt -v
```

Expected: `test_prompt_mentions_resolve_product` FAIL, `test_prompt_does_not_mention_search_products` FAIL (old prompt still mentions search_products).

- [ ] **Step 3: Replace `_build_system_prompt()`**

```python
def _build_system_prompt() -> str:
    return """Eres un asistente de compras para supershop, un supermercado online.

## Reglas de routing (seguí estas 4 en orden)
1. Para cada producto que el usuario pida, llamá resolve_product(query, quantity).
2. Si el resultado tiene status "resolved" → llamá add_to_cart(product_id, quantity) con los valores del campo "product".
3. Si el resultado tiene status "needs_clarification" → llamá request_clarification con las options del resultado.
4. Si el resultado tiene status "not_found" → llamá report_missing con el nombre del producto.

## Carrito
- Nunca construyas ni infieras product_ids manualmente. Usá solo los que devuelva resolve_product.
- Para pedidos multi-producto, llamá resolve_product por separado para cada ítem.
- Al terminar, describí brevemente qué agregaste, qué faltó, y qué necesita aclaración.

## Reglas generales
- Respondé siempre en español, de forma amable y concisa.
- Sin markdown: sin **, sin *, sin #, sin guiones de lista.
- Si el usuario pregunta algo no relacionado con compras, redirigilo amablemente.
"""
```

- [ ] **Step 4: Run all prompt tests**

```bash
pytest tests/test_chat_agent.py::TestSystemPrompt -v
```

Expected: all PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all pass (or only pre-existing failures, none new).

- [ ] **Step 6: Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "feat(v4/agent): simplify system prompt to 4 routing rules"
```

---

## Task 5: Monthly basket action path

When the frontend sends `action: "generate_monthly_basket"` in the chat body, `handle_chat()` short-circuits the LangGraph graph entirely: it loads the user's recurring plan and order history from DB, calls `generate_monthly_basket_candidates()`, and returns a proposed cart with a formatted summary message. The user can then accept it via `POST /api/v1/recurring-plan/accept` (Nacho's endpoint, called from the frontend directly).

**Files:**
- Modify: `backend/chat_agent_agentic.py` — add `action` param + `_handle_generate_basket()` helper
- Modify: `backend/app.py` — `action` is already forwarded (Task 1, Step 5)
- Test: `tests/test_chat_agent.py`

- [ ] **Step 1: Write failing tests**

```python
@patch("backend.chat_agent_agentic._load_catalog")
@patch("backend.chat_agent_agentic._build_graph")
def test_generate_monthly_basket_action_returns_proposed_cart(self, mock_build_graph, mock_load_catalog, sample_catalog):
    """action='generate_monthly_basket' bypasses the graph and returns a proposed_cart."""
    import tempfile, os
    from backend import db as _db
    from backend.chat_agent_agentic import handle_chat, _reset_app_cache
    _reset_app_cache()
    mock_load_catalog.return_value = sample_catalog
    mock_build_graph.return_value = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        orig = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = tmp_path
        _db.init_db()
        # Insert a minimal recurring plan
        conn = _db.get_db()
        conn.execute(
            """INSERT INTO recurring_plans (id, household_size, monthly_budget, priority_items,
               preferred_brands, strict_brand, excluded_categories, notes)
               VALUES ('default', 2, 5000.0, '[]', '{}', 0, '[]', '')"""
        )
        conn.commit()
        conn.close()

        with patch("backend.product_semantic_index.generate_monthly_basket_candidates") as mock_gen:
            mock_gen.return_value = [
                {"query": "leche", "tag": "must_have", "status": "resolved",
                 "product": sample_catalog[0], "quantity": 1, "estimated_price": 350.0}
            ]
            result = handle_chat(
                message="",
                history=[],
                cart=[],
                action="generate_monthly_basket",
            )
        assert "proposed_cart" in result
        assert len(result["proposed_cart"]) == 1
        assert result["proposed_cart"][0]["product_id"] == "p1"
        assert "reply" in result
    finally:
        if orig is not None:
            os.environ["DATABASE_PATH"] = orig
        elif "DATABASE_PATH" in os.environ:
            del os.environ["DATABASE_PATH"]
        os.unlink(tmp_path)

@patch("backend.chat_agent_agentic._load_catalog")
@patch("backend.chat_agent_agentic._build_graph")
def test_generate_monthly_basket_no_plan_returns_empty(self, mock_build_graph, mock_load_catalog, sample_catalog):
    """With no recurring plan in DB, proposed_cart should be empty."""
    import tempfile, os
    from backend import db as _db
    from backend.chat_agent_agentic import handle_chat, _reset_app_cache
    _reset_app_cache()
    mock_load_catalog.return_value = sample_catalog
    mock_build_graph.return_value = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        orig = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = tmp_path
        _db.init_db()
        result = handle_chat(message="", history=[], cart=[], action="generate_monthly_basket")
        assert "proposed_cart" in result
        assert result["proposed_cart"] == []
    finally:
        if orig is not None:
            os.environ["DATABASE_PATH"] = orig
        elif "DATABASE_PATH" in os.environ:
            del os.environ["DATABASE_PATH"]
        os.unlink(tmp_path)
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_chat_agent.py -k "monthly_basket" -v
```

Expected: `TypeError: handle_chat() got an unexpected keyword argument 'action'`

- [ ] **Step 3: Add `action` parameter and `_handle_generate_basket()` to `handle_chat()`**

First add the helper function in `backend/chat_agent_agentic.py` (place before `handle_chat`):

```python
def _handle_generate_basket(catalog: list[dict]) -> dict:
    """
    Short-circuit handler for action='generate_monthly_basket'.
    Loads plan + order history from DB, calls generate_monthly_basket_candidates(),
    returns {reply, proposed_cart, cart, clarification, missing_items}.
    """
    from backend.db import get_db
    from backend.product_semantic_index import generate_monthly_basket_candidates

    conn = get_db()
    try:
        plan_row = conn.execute(
            "SELECT * FROM recurring_plans WHERE id='default'"
        ).fetchone()
        order_rows = conn.execute(
            "SELECT cart_json, total, created_at FROM orders ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()

    if not plan_row:
        return {
            "reply": "No tenés un plan de compras mensual guardado. Configuralo en la pestaña de compras recurrentes.",
            "proposed_cart": [],
            "cart": None,
            "clarification": None,
            "missing_items": [],
        }

    import json as _json
    plan = {
        "household_size": plan_row["household_size"],
        "monthly_budget": plan_row["monthly_budget"],
        "must_haves": _json.loads(plan_row["priority_items"]),
        "preferred_brands": _json.loads(plan_row["preferred_brands"]),
        "strict_brand": bool(plan_row["strict_brand"]),
        "excluded_categories": _json.loads(plan_row["excluded_categories"]),
        "notes": plan_row["notes"],
    }

    order_history = []
    for r in order_rows:
        try:
            items = _json.loads(r["cart_json"])
        except Exception:
            items = []
        order_history.append(items)

    budget = plan.get("monthly_budget") or float("inf")
    candidates = generate_monthly_basket_candidates(
        prefs=plan,
        order_history=order_history,
        catalog=catalog,
        budget=budget,
    )

    catalog_by_id = {p["id"]: p for p in catalog}
    proposed_cart = []
    for c in candidates:
        product = c.get("product")
        if not product:
            continue
        pid = product.get("id")
        if not pid or not catalog_by_id.get(pid):
            continue
        proposed_cart.append({
            "product_id": pid,
            "name": product.get("name", ""),
            "brand": product.get("brand", ""),
            "package_size": product.get("package_size", ""),
            "price": product.get("price", 0.0),
            "quantity": c.get("quantity", 1),
            "image_url": product.get("image_url", ""),
            "tag": c.get("tag", "suggested"),
        })

    total = round(sum(i["price"] * i["quantity"] for i in proposed_cart), 2)
    n = len(proposed_cart)
    must = sum(1 for i in proposed_cart if i.get("tag") == "must_have")
    recurring = sum(1 for i in proposed_cart if i.get("tag") == "recurring")
    offers = sum(1 for i in proposed_cart if i.get("tag") == "offer")

    reply = (
        f"Generé tu canasta mensual con {n} productos por ${total:.2f}. "
        f"{must} esenciales, {recurring} recurrentes, {offers} con descuento. "
        "Revisá el detalle y confirmá cuando quieras."
    )

    return {
        "reply": reply,
        "proposed_cart": proposed_cart,
        "cart": None,
        "clarification": None,
        "missing_items": [],
    }
```

Then update `handle_chat()` signature and add the early return:

```python
def handle_chat(
    message: str,
    history: list[dict],
    cart: list[dict],
    clarification_response: dict | None = None,
    context: str | None = None,
    session_id: str = "",
    action: str | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    app, _catalog = _get_or_build_app(api_key, session_id=session_id)

    if action == "generate_monthly_basket":
        return _handle_generate_basket(_catalog)

    # ... rest of existing handle_chat() body unchanged ...
```

- [ ] **Step 4: Run the monthly basket tests**

```bash
pytest tests/test_chat_agent.py -k "monthly_basket" -v
```

Expected: PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "feat(v4/agent): monthly basket generation action path"
```

---

## Self-Review

**Spec coverage:**
- [x] Swap `search_products` → `resolve_product` + `add_to_cart` — Tasks 2, 3
- [x] System prompt simplified to 4 routing rules — Task 4
- [x] `session_id` threaded through — Task 1
- [x] Monthly basket graph node (implemented as short-circuit action, simpler and equivalent) — Task 5
- [x] Recurring generation reproducible for demo — `generate_monthly_basket_candidates` is fully deterministic (rule-based, no LLM); same inputs → same output every time

**Placeholder scan:** None found.

**Type consistency:**
- `_make_tools(catalog, session_id="")` — used consistently in Tasks 2, 3
- `_get_or_build_app(api_key, session_id="")` — used in Task 2 (Step 5) and Task 5 (Step 3)
- `_read_session_cart(session_id, catalog)` — defined Task 2 (Step 3), used Task 2 (Step 6)
- `_handle_generate_basket(catalog)` — defined and used in Task 5
- `proposed_cart` key in return dict — only present for `generate_monthly_basket` action; normal flow does not include it (frontend should handle its absence gracefully)

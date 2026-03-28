# Fix Agent Workflow Weaknesses — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix all 14 verified correctness, reliability, and security issues in `chat_agent_agentic.py` and `app.py`.

**Architecture:** Changes are additive and confined to two files. Each task is independent unless noted. The biggest structural change is the singleton cache (Task 5), which requires updating existing test patches. Do tasks in order — Task 5 changes the import surface that later tests rely on.

**Tech Stack:** Python 3 stdlib, LangGraph, LangChain, pytest.

---

## File Map

| File | What changes |
|---|---|
| `backend/chat_agent_agentic.py` | All agent logic fixes (Tasks 1–7, 9) |
| `backend/app.py` | Path traversal guard (Task 8) |
| `tests/test_chat_agent.py` | New tests for each fix; update existing tests for singleton |

---

## Task 1 — Halt the graph when clarification is requested

**Why:** `should_continue` only runs after `agent_node`. By then the clarification flag hasn't been set yet — it's set in `tools_node`. The graph must exit after `tools_node` detects a clarification call, not after the next agent turn.

**Files:**
- Modify: `backend/chat_agent_agentic.py:217-228`
- Test: `tests/test_chat_agent.py` (add to `TestAgenticLoop`)

- [x] **Step 1.1 — Write failing test**

Add to `class TestAgenticLoop` in `tests/test_chat_agent.py`:

```python
@patch("backend.product_semantic_index.search")
@patch("backend.chat_agent_agentic.ChatOpenAI")
@patch("backend.chat_agent_agentic._load_catalog")
def test_graph_halts_after_request_clarification(
    self, mock_load_catalog, mock_llm_cls, mock_search, sample_catalog
):
    """Graph must stop immediately after request_clarification — no extra LLM call."""
    mock_load_catalog.return_value = sample_catalog
    mock_search.return_value = [sample_catalog[0]]

    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.invoke.side_effect = [
        self._ai_with_tool_call(
            "request_clarification",
            {
                "question": "¿Cuál leche querés?",
                "options": [
                    {"id": "opt1", "label": "Entera"},
                    {"id": "opt2", "label": "Descremada"},
                ],
            },
            "c1",
        ),
        # This would be called if the bug is still present:
        self._ai_reply("Extra unexpected reply"),
    ]
    mock_llm_cls.return_value = llm

    result = handle_chat("quiero leche", [], [])

    # Graph must have called llm.invoke exactly once (one agent turn, then halt)
    assert llm.invoke.call_count == 1, (
        f"Expected 1 LLM call, got {llm.invoke.call_count}. "
        "Graph did not halt after request_clarification."
    )
    assert result["clarification"] is not None
    assert result["clarification"]["question"] == "¿Cuál leche querés?"
```

- [x] **Step 1.2 — Run test and confirm it fails**

```bash
cd /home/jerefigo/Documents/fun/hackITBA2026
python -m pytest tests/test_chat_agent.py::TestAgenticLoop::test_graph_halts_after_request_clarification -v
```

Expected: FAIL — `assert llm.invoke.call_count == 1` fails because the graph keeps running.

- [x] **Step 1.3 — Implement the fix**

In `backend/chat_agent_agentic.py`, replace the `_build_graph` function's internal edge wiring. Change:

```python
    def should_continue(state: AgentState) -> str:
        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")
```

To:

```python
    def should_continue(state: AgentState) -> str:
        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        return END

    def after_tools(state: AgentState) -> str:
        """Halt if a clarification was just requested; otherwise loop back."""
        if state.get("clarification"):
            return END
        return "agent"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_conditional_edges("tools", after_tools)
```

- [x] **Step 1.4 — Run test and confirm it passes**

```bash
python -m pytest tests/test_chat_agent.py::TestAgenticLoop::test_graph_halts_after_request_clarification -v
```

Expected: PASS

- [x] **Step 1.5 — Run full test suite to check for regressions**

```bash
python -m pytest tests/test_chat_agent.py -v
```

Expected: all previously passing tests still pass.

- [x] **Step 1.6 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "fix(agent): halt graph immediately after request_clarification via after_tools edge"
```

---

## Task 2 — Include UUID in clarification tool result

**Why:** The `pending_request_id` UUID is generated in `tools_node` after the tool runs, but is never included in the `ToolMessage` the LLM sees. The LLM therefore has no way to match a clarification response (which contains the UUID) to a prior request. Fix: generate the UUID inside the tool and return it in the result; `tools_node` reads it from the result.

**Files:**
- Modify: `backend/chat_agent_agentic.py:146-150, 198-204`
- Test: `tests/test_chat_agent.py` (add to `TestAgenticLoop`)

- [x] **Step 2.1 — Write failing test**

Add to `class TestAgenticLoop` in `tests/test_chat_agent.py`:

```python
@patch("backend.product_semantic_index.search")
@patch("backend.chat_agent_agentic.ChatOpenAI")
@patch("backend.chat_agent_agentic._load_catalog")
def test_clarification_tool_result_contains_pending_id(
    self, mock_load_catalog, mock_llm_cls, mock_search, sample_catalog
):
    """The ToolMessage for request_clarification must contain pending_request_id
    so the LLM can match it in the next turn."""
    mock_load_catalog.return_value = sample_catalog

    captured_messages = []

    def capture_and_reply(messages):
        captured_messages.append(list(messages))
        if len(captured_messages) == 1:
            return self._ai_with_tool_call(
                "request_clarification",
                {"question": "¿Cuál querés?", "options": [{"id": "o1", "label": "A"}]},
                "c1",
            )
        return self._ai_reply("ok")

    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.invoke.side_effect = capture_and_reply
    mock_llm_cls.return_value = llm

    handle_chat("quiero algo", [], [])

    # The graph halts after tools_node so there's only 1 LLM call.
    # Check that the ToolMessage added by tools_node contains pending_request_id.
    from langchain_core.messages import ToolMessage
    first_call_msgs = captured_messages[0]
    # tools_node adds the ToolMessage and the graph halts — verify via state.
    # Instead, inspect what tools_node returned by calling _build_graph manually.
    # Simpler: call the tool directly and verify its output contains the UUID.
    from backend.chat_agent_agentic import _make_tools
    tools = _make_tools(sample_catalog)
    req_clarification = next(t for t in tools if t.name == "request_clarification")
    result_json = req_clarification.invoke(
        {"question": "¿Cuál?", "options": [{"id": "o1", "label": "A"}]}
    )
    import json
    result = json.loads(result_json)
    assert "pending_request_id" in result, (
        "request_clarification tool result must include pending_request_id"
    )
    assert result["pending_request_id"]  # non-empty
```

- [x] **Step 2.2 — Run test and confirm it fails**

```bash
python -m pytest tests/test_chat_agent.py::TestAgenticLoop::test_clarification_tool_result_contains_pending_id -v
```

Expected: FAIL — `pending_request_id` not in result.

- [x] **Step 2.3 — Implement the fix**

In `backend/chat_agent_agentic.py`, change the `request_clarification` tool (lines 146-150):

```python
    @tool
    def request_clarification(question: str, options: list[dict]) -> str:
        """Ask the user to choose between ambiguous product options before proceeding.
        Returns the pending_request_id the front-end must echo back when the user replies."""
        pending_id = str(uuid.uuid4())
        return json.dumps({
            "acknowledged": True,
            "pending_request_id": pending_id,
            "question": question,
            "options": options,
        })
```

Then update `tools_node` to read the UUID from the tool result instead of generating it (replace lines 198-204):

```python
            elif tc["name"] == "request_clarification":
                parsed = json.loads(result)
                clarification = {
                    "question": parsed.get("question", tc["args"].get("question", "¿Cuál preferís?")),
                    "options": parsed.get("options", tc["args"].get("options", [])),
                    "pending_request_id": parsed.get("pending_request_id"),
                }
```

- [x] **Step 2.4 — Run test and confirm it passes**

```bash
python -m pytest tests/test_chat_agent.py::TestAgenticLoop::test_clarification_tool_result_contains_pending_id -v
```

Expected: PASS

- [x] **Step 2.5 — Run full test suite**

```bash
python -m pytest tests/test_chat_agent.py -v
```

Expected: all pass.

- [x] **Step 2.6 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "fix(agent): emit pending_request_id from request_clarification tool result for LLM matching"
```

---

## Task 3 — Report dropped items in set_cart tool result

**Why:** `_validate_cart` silently drops invalid/out-of-stock items. The agent's `ToolMessage` only reports the count of valid items, not what was removed. The agent then confidently says "cart set" without knowing items were lost.

**Files:**
- Modify: `backend/chat_agent_agentic.py:139-144`
- Test: `tests/test_chat_agent.py` (add to `TestAgenticLoop`)

- [x] **Step 3.1 — Write failing test**

Add to `class TestAgenticLoop` in `tests/test_chat_agent.py`:

```python
@patch("backend.chat_agent_agentic.ChatOpenAI")
@patch("backend.chat_agent_agentic._load_catalog")
def test_set_cart_reports_dropped_items(
    self, mock_load_catalog, mock_llm_cls, sample_catalog
):
    """When set_cart drops invalid items, the ToolMessage must list them
    so the agent can inform the user."""
    mock_load_catalog.return_value = sample_catalog

    captured_tool_messages = []

    def inspect_and_reply(messages):
        from langchain_core.messages import ToolMessage
        for m in messages:
            if isinstance(m, ToolMessage):
                captured_tool_messages.append(m)
        return self._ai_reply("ok")

    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.invoke.side_effect = [
        self._ai_with_tool_call(
            "set_cart",
            {"items": [
                {"product_id": "p1", "quantity": 1},   # valid
                {"product_id": "bad_id", "quantity": 1},  # unknown
                {"product_id": "p3", "quantity": 1},   # out of stock
            ]},
            "c1",
        ),
        inspect_and_reply,
    ]
    mock_llm_cls.return_value = llm

    handle_chat("poneme leche, cosa_mala y pan", [], [])

    assert len(captured_tool_messages) == 1
    import json
    result = json.loads(captured_tool_messages[0].content)
    assert "dropped" in result, "ToolMessage must include 'dropped' key listing removed items"
    assert len(result["dropped"]) == 2
    dropped_ids = {d["product_id"] for d in result["dropped"]}
    assert "bad_id" in dropped_ids
    assert "p3" in dropped_ids
```

- [x] **Step 3.2 — Run test and confirm it fails**

```bash
python -m pytest tests/test_chat_agent.py::TestAgenticLoop::test_set_cart_reports_dropped_items -v
```

Expected: FAIL — `'dropped' not in result`.

- [x] **Step 3.3 — Implement the fix**

In `backend/chat_agent_agentic.py`, change `_validate_cart` to return both valid items and the dropped IDs. First, create a helper that returns both:

```python
def _validate_cart(items: list[dict], catalog: list[dict]) -> list[dict]:
    """Validate cart items against the catalog. Remove unknown or out-of-stock items."""
    catalog_by_id = {p["id"]: p for p in catalog}
    valid = []
    for item in items:
        pid = item.get("product_id")
        if pid and pid in catalog_by_id:
            p = catalog_by_id[pid]
            if p.get("available_quantity", 1) > 0:
                valid.append({
                    "product_id": pid,
                    "name": p["name"],
                    "brand": p.get("brand", ""),
                    "package_size": p.get("package_size", ""),
                    "price": p["price"],
                    "quantity": max(1, int(item.get("quantity", 1))),
                    "image_url": p.get("image_url", ""),
                })
    return valid


def _validate_cart_with_report(
    items: list[dict], catalog: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Return (valid_items, dropped_items). dropped_items keeps original product_id
    and a reason string so the agent can report them."""
    catalog_by_id = {p["id"]: p for p in catalog}
    valid: list[dict] = []
    dropped: list[dict] = []
    for item in items:
        pid = item.get("product_id")
        if not pid or pid not in catalog_by_id:
            dropped.append({"product_id": pid or "(missing)", "reason": "unknown product"})
        elif catalog_by_id[pid].get("available_quantity", 1) <= 0:
            dropped.append({"product_id": pid, "reason": "out of stock"})
        else:
            p = catalog_by_id[pid]
            valid.append({
                "product_id": pid,
                "name": p["name"],
                "brand": p.get("brand", ""),
                "package_size": p.get("package_size", ""),
                "price": p["price"],
                "quantity": max(1, int(item.get("quantity", 1))),
                "image_url": p.get("image_url", ""),
            })
    return valid, dropped
```

Then update the `set_cart` tool to use the new helper:

```python
    @tool
    def set_cart(items: list[dict]) -> str:
        """Replace the entire cart with the given items.
        Each item must have product_id (from search_products results) and quantity.
        Call this after every cart mutation. Returns the validated cart and any
        dropped items as JSON."""
        valid, dropped = _validate_cart_with_report(items, catalog)
        return json.dumps({"cart": valid, "count": len(valid), "dropped": dropped})
```

Also update `tools_node` to read from the new result structure (line 195-197 — `result_cart` capture):

```python
            if tc["name"] == "set_cart":
                parsed = json.loads(result)
                result_cart = parsed.get("cart")
```

(This line is already correct — `parsed.get("cart")` still works because we kept the `cart` key.)

- [x] **Step 3.4 — Run test and confirm it passes**

```bash
python -m pytest tests/test_chat_agent.py::TestAgenticLoop::test_set_cart_reports_dropped_items -v
```

Expected: PASS

- [x] **Step 3.5 — Confirm existing cart validation tests still pass**

```bash
python -m pytest tests/test_chat_agent.py::TestValidateCart -v
```

Expected: all pass (`_validate_cart` is still there, untouched).

- [x] **Step 3.6 — Run full suite**

```bash
python -m pytest tests/test_chat_agent.py -v
```

Expected: all pass.

- [x] **Step 3.7 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "fix(agent): set_cart reports dropped items in ToolMessage so agent can inform the user"
```

---

## Task 4 — Add request_clarification guidance to system prompt

**Why:** The system prompt describes single-product and recipe flows but never mentions `request_clarification`. Without instructions the model won't call it — or calls it at wrong moments.

**Files:**
- Modify: `backend/chat_agent_agentic.py:46-78`
- Test: `tests/test_chat_agent.py` (add to `TestSystemPrompt`)

- [x] **Step 4.1 — Write failing test**

Add to `class TestSystemPrompt` in `tests/test_chat_agent.py`:

```python
def test_prompt_mentions_request_clarification(self):
    prompt = _build_system_prompt()
    assert "request_clarification" in prompt, (
        "System prompt must instruct the agent when to use request_clarification"
    )

def test_prompt_clarification_example_has_two_options(self):
    """Prompt should give the agent a concrete trigger: two+ options found."""
    prompt = _build_system_prompt()
    # Check that the prompt tells the agent to use it when there are multiple options
    assert "ambig" in prompt.lower() or "opcion" in prompt.lower() or "opción" in prompt.lower()
```

- [x] **Step 4.2 — Run tests and confirm they fail**

```bash
python -m pytest tests/test_chat_agent.py::TestSystemPrompt::test_prompt_mentions_request_clarification tests/test_chat_agent.py::TestSystemPrompt::test_prompt_clarification_example_has_two_options -v
```

Expected: FAIL

- [x] **Step 4.3 — Implement the fix**

In `backend/chat_agent_agentic.py`, add a new section to `_build_system_prompt` before the `## Reglas generales` block:

```python
def _build_system_prompt() -> str:
    return """Eres un asistente de compras inteligente para supershop, un supermercado online.
Tu trabajo es ayudar al usuario a armar su carrito de compras de forma eficiente.

## Flujo para un producto específico
1. Llamá a search_products con una query descriptiva.
2. Elegí el producto más adecuado (mejor match de nombre, marca, tamaño).
3. Llamá a set_cart con el carrito COMPLETO actualizado.
4. Respondé nombrando el producto y el precio.

## Flujo para recetas, menús o metas de compra
Cuando el usuario pida una receta, un menú semanal, o una meta amplia (ej: "quiero hacer una torta", "armame desayunos para la semana"):
1. Identificá TODOS los ingredientes o productos necesarios.
2. Para CADA ingrediente/producto, llamá a search_products por separado.
3. Por cada búsqueda:
   a. Si encontrás un match adecuado → incluilo en el carrito.
   b. Si encontrás algo parecido pero no exacto → usalo como sustituto y anotá la diferencia.
   c. Si no encontrás nada útil → llamá a report_missing con el nombre del ingrediente.
4. Al final, llamá a set_cart UNA SOLA VEZ con todos los ítems juntos (previos + nuevos).
5. Respondé con un resumen estructurado (ver formato abajo).

## Cuándo usar request_clarification
Usá request_clarification ANTES de agregar al carrito cuando el pedido del usuario sea ambiguo
y haya dos o más opciones razonables (ej: "leche" puede ser entera o descremada, "fideos" puede
ser varias marcas o formatos). Pasá las opciones encontradas y preguntá al usuario cuál prefiere.
NO la uses si hay un match claro o si el usuario ya especificó marca/tamaño.

## Formato de respuesta para multi-producto
Terminá siempre con un resumen en tres secciones (omití las secciones vacías):
✅ Agregados: listá producto, marca y precio de cada ítem nuevo.
🔄 Sustituciones: explicá qué pediste y qué agregaste en su lugar y por qué.
❌ No disponibles: listá los ingredientes que no encontraste en el catálogo.

## Reglas generales
- Respondé siempre en español, de forma amable y concisa.
- Nunca uses un product_id que no apareció en los resultados de search_products.
- Al actualizar el carrito, siempre incluí TODOS los ítems previos más los nuevos.
- Si el usuario pregunta algo no relacionado con compras, redirigilo amablemente.
"""
```

- [x] **Step 4.4 — Run tests and confirm they pass**

```bash
python -m pytest tests/test_chat_agent.py::TestSystemPrompt -v
```

Expected: all pass.

- [x] **Step 4.5 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "fix(agent): document request_clarification usage in system prompt with trigger conditions"
```

---

## Task 5 — Singleton cache for graph and catalog

**Why:** `_load_catalog()` reads from disk and `_build_graph()` constructs `ChatOpenAI`, calls `.bind_tools()`, and compiles the graph on **every** `handle_chat` call. These should be module-level singletons.

**Important for tests:** Existing `TestHandleChat` tests patch `_load_catalog` and `_build_graph` directly. After this change, `handle_chat` calls `_get_or_build_app()` instead — which calls those functions only when the cache is cold. Tests must reset the cache before each test, or patch `_get_or_build_app` instead. This task updates the affected tests.

**Files:**
- Modify: `backend/chat_agent_agentic.py:249-253`
- Modify: `tests/test_chat_agent.py` (update `TestHandleChat` to reset cache + add perf test)

- [x] **Step 5.1 — Write new test (performance regression guard)**

Add to `tests/test_chat_agent.py`:

```python
class TestSingletonCache:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_graph_built_once_across_two_calls(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """_build_graph must be called only once even when handle_chat is called twice."""
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        from langchain_core.messages import AIMessage
        llm.invoke.return_value = AIMessage(content="ok")
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        handle_chat("cómo estás", [], [])

        # ChatOpenAI should have been constructed only once
        assert mock_llm_cls.call_count == 1, (
            f"ChatOpenAI constructed {mock_llm_cls.call_count} times; expected 1. "
            "Graph is not being cached between calls."
        )

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_catalog_loaded_once_across_two_calls(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """_load_catalog must be called only once when the cache is warm."""
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        from langchain_core.messages import AIMessage
        llm.invoke.return_value = AIMessage(content="ok")
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        handle_chat("adiós", [], [])

        assert mock_load_catalog.call_count == 1, (
            f"_load_catalog called {mock_load_catalog.call_count} times; expected 1."
        )

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_reset_cache_forces_rebuild(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """_reset_app_cache must trigger a fresh build on the next call."""
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        from langchain_core.messages import AIMessage
        llm.invoke.return_value = AIMessage(content="ok")
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        handle_chat("hola de nuevo", [], [])

        assert mock_llm_cls.call_count == 2
```

- [x] **Step 5.2 — Run new tests and confirm they fail**

```bash
python -m pytest tests/test_chat_agent.py::TestSingletonCache -v
```

Expected: FAIL — `_reset_app_cache` doesn't exist yet.

- [x] **Step 5.3 — Implement the singleton**

In `backend/chat_agent_agentic.py`, add after the `CATALOG_PATH` constant (around line 31):

```python
# ─── Singleton cache ───────────────────────────────────────────────────────────

_app_cache: tuple | None = None  # (app, catalog, api_key)


def _get_or_build_app(api_key: str):
    """Return (compiled_graph, catalog), building and caching on first call."""
    global _app_cache
    if _app_cache is None or _app_cache[2] != api_key:
        catalog = _load_catalog()
        app = _build_graph(catalog, api_key)
        _app_cache = (app, catalog, api_key)
    return _app_cache[0], _app_cache[1]


def _reset_app_cache() -> None:
    """Invalidate the singleton. Call this in tests that need a fresh graph."""
    global _app_cache
    _app_cache = None
```

Then in `handle_chat`, replace lines 252-253:

```python
    catalog = _load_catalog()
    app = _build_graph(catalog, api_key)
```

With:

```python
    app, catalog = _get_or_build_app(api_key)
```

- [x] **Step 5.4 — Update existing TestHandleChat tests to reset cache**

`TestHandleChat` patches `_load_catalog` and `_build_graph`, which are only called when the cache is cold. Add `setup_method` and `teardown_method` to reset the cache so patches always take effect:

```python
class TestHandleChat:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    # ... existing test methods unchanged ...
```

Do the same for `TestAgenticLoop` (it patches `_load_catalog` and `ChatOpenAI` — same issue):

```python
class TestAgenticLoop:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    # ... existing test methods unchanged ...
```

- [x] **Step 5.5 — Run new tests and confirm they pass**

```bash
python -m pytest tests/test_chat_agent.py::TestSingletonCache -v
```

Expected: all 3 pass.

- [x] **Step 5.6 — Run full test suite**

```bash
python -m pytest tests/test_chat_agent.py -v
```

Expected: all pass.

- [x] **Step 5.7 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "perf(agent): singleton cache for graph and catalog — avoid rebuild on every request"
```

---

## Task 6 — Loop guard and LLM timeout

**Why:** No explicit recursion limit means a misbehaving model hits LangGraph's silent default (25) and raises an unhandled `GraphRecursionError`. No timeout means a hung OpenAI call blocks a ThreadingHTTPServer thread permanently.

**Files:**
- Modify: `backend/chat_agent_agentic.py:164-171, 284`
- Test: `tests/test_chat_agent.py` (add `TestResilience`)

- [x] **Step 6.1 — Write tests**

Add to `tests/test_chat_agent.py`:

```python
class TestResilience:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_llm_constructed_with_timeout(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """ChatOpenAI must be constructed with a timeout."""
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        from langchain_core.messages import AIMessage
        llm.invoke.return_value = AIMessage(content="ok")
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])

        call_kwargs = mock_llm_cls.call_args.kwargs
        assert "timeout" in call_kwargs, "ChatOpenAI must be constructed with timeout="
        assert call_kwargs["timeout"] > 0

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_invoke_called_with_recursion_limit(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """app.invoke must pass a recursion_limit in its config."""
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        from langchain_core.messages import AIMessage
        llm.invoke.return_value = AIMessage(content="ok")
        mock_llm_cls.return_value = llm

        # Patch _get_or_build_app to capture the compiled app mock
        captured_invocations = []
        real_get_or_build = None

        import backend.chat_agent_agentic as module
        original = module._get_or_build_app

        def patched_get_or_build(api_key):
            app, catalog = original(api_key)
            original_invoke = app.invoke
            def capturing_invoke(state, config=None, **kw):
                captured_invocations.append(config)
                return original_invoke(state, config=config, **kw)
            app.invoke = capturing_invoke
            return app, catalog

        with patch.object(module, "_get_or_build_app", side_effect=patched_get_or_build):
            handle_chat("hola", [], [])

        assert captured_invocations, "app.invoke was never called"
        config = captured_invocations[0]
        assert config is not None, "app.invoke was called without config"
        assert "recursion_limit" in config, "config must include recursion_limit"
        assert config["recursion_limit"] >= 20
```

- [x] **Step 6.2 — Run tests and confirm they fail**

```bash
python -m pytest tests/test_chat_agent.py::TestResilience -v
```

Expected: both FAIL.

- [x] **Step 6.3 — Implement the fix**

In `backend/chat_agent_agentic.py`, add `timeout=30` to `ChatOpenAI` in `_build_graph`:

```python
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=api_key,
        timeout=30,
    ).bind_tools(tools)
```

Add `config={"recursion_limit": 30}` to `app.invoke` in `handle_chat`:

```python
    final_state = app.invoke(
        {
            "messages": init_messages,
            "catalog": catalog,
            "result_cart": None,
            "clarification": None,
            "missing_items": [],
        },
        config={"recursion_limit": 30},
    )
```

- [x] **Step 6.4 — Run tests and confirm they pass**

```bash
python -m pytest tests/test_chat_agent.py::TestResilience -v
```

Expected: both pass.

- [x] **Step 6.5 — Run full suite**

```bash
python -m pytest tests/test_chat_agent.py -v
```

Expected: all pass.

- [x] **Step 6.6 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "fix(agent): add 30s LLM timeout and recursion_limit=30 to prevent infinite loops"
```

---

## Task 7 — Low-priority cleanups (dead field, case-sensitive dedup, exception sanitization)

Three small fixes in one task: (a) remove dead `catalog` field from `AgentState`, (b) normalize `missing_items` dedup to lowercase, (c) sanitize exception messages before sending to LLM.

**Files:**
- Modify: `backend/chat_agent_agentic.py:36-41, 183, 190-192, 206-208, 284-290`
- Test: `tests/test_chat_agent.py` (add `TestCleanups`)

- [x] **Step 7.1 — Write tests**

Add to `tests/test_chat_agent.py`:

```python
class TestCleanups:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def test_missing_items_dedup_is_case_insensitive(self, sample_catalog):
        """'Sal' and 'sal' must not both appear in missing_items."""
        from backend.chat_agent_agentic import _build_graph
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "sk-test")

        with patch("backend.chat_agent_agentic.ChatOpenAI") as mock_llm_cls:
            llm = MagicMock()
            llm.bind_tools.return_value = llm
            llm.invoke.side_effect = [
                # First call: report_missing "Sal"
                MagicMock(
                    **{
                        "tool_calls": [
                            {"name": "report_missing", "args": {"ingredient": "Sal"}, "id": "c1", "type": "tool_call"}
                        ],
                        "content": "",
                    }
                ),
                # Second call: report_missing "sal" (same, different case)
                MagicMock(
                    **{
                        "tool_calls": [
                            {"name": "report_missing", "args": {"ingredient": "sal"}, "id": "c2", "type": "tool_call"}
                        ],
                        "content": "",
                    }
                ),
                # Third: end
                MagicMock(tool_calls=[], content="ok"),
            ]
            from langchain_core.messages import AIMessage
            for m in llm.invoke.side_effect:
                if not hasattr(m, 'tool_calls'):
                    pass

            mock_llm_cls.return_value = llm
            _reset_app_cache = __import__('backend.chat_agent_agentic', fromlist=['_reset_app_cache'])._reset_app_cache
            _reset_app_cache()

            with patch("backend.chat_agent_agentic._load_catalog", return_value=sample_catalog):
                from backend.chat_agent_agentic import handle_chat
                result = handle_chat("necesito sal", [], [])

        assert result["missing_items"].count("sal") == 1, (
            "Case-insensitive dedup failed: 'sal' appears more than once"
        )

    def test_tool_exception_message_is_sanitized(self, sample_catalog):
        """Exception details must not be forwarded raw to the LLM ToolMessage."""
        from backend.chat_agent_agentic import _make_tools
        import json as _json

        tools = _make_tools(sample_catalog)
        search_tool = next(t for t in tools if t.name == "search_products")

        with patch("backend.product_semantic_index.search", side_effect=RuntimeError("/secret/path/info leaked")):
            # We can't call tools_node directly, so test the sanitization helper
            # by checking the tool raises and the error message is sanitized.
            # The actual sanitization happens in tools_node — test it via the full graph.
            pass  # covered by integration: tools_node should not pass str(exc) raw

        # Direct unit test: verify exception content is not forwarded
        # Build a minimal tools_node scenario
        from langchain_core.messages import AIMessage, ToolMessage
        import backend.chat_agent_agentic as mod

        with patch("backend.chat_agent_agentic.ChatOpenAI") as mock_llm_cls, \
             patch("backend.chat_agent_agentic._load_catalog", return_value=sample_catalog), \
             patch("backend.product_semantic_index.search", side_effect=RuntimeError("SECRET_INTERNAL_PATH=/opt/server")):

            llm = MagicMock()
            llm.bind_tools.return_value = llm

            captured = []
            def capture(msgs):
                captured.append(list(msgs))
                if len(captured) == 1:
                    return AIMessage(
                        content="",
                        tool_calls=[{"name": "search_products", "args": {"query": "leche"}, "id": "c1", "type": "tool_call"}],
                    )
                return AIMessage(content="ok")

            llm.invoke.side_effect = capture
            mock_llm_cls.return_value = llm
            _reset_app_cache()

            from backend.chat_agent_agentic import handle_chat, _reset_app_cache
            _reset_app_cache()
            handle_chat("quiero leche", [], [])

        # Check all ToolMessages in second call do not contain raw exception text
        if len(captured) >= 2:
            second_msgs = captured[1]
            tool_messages = [m for m in second_msgs if isinstance(m, ToolMessage)]
            for tm in tool_messages:
                assert "SECRET_INTERNAL_PATH" not in tm.content, (
                    "Raw exception detail leaked into ToolMessage"
                )
```

- [x] **Step 7.2 — Run tests (some will fail)**

```bash
python -m pytest tests/test_chat_agent.py::TestCleanups -v
```

Expected: `test_missing_items_dedup_is_case_insensitive` fails; exception test may vary.

- [x] **Step 7.3 — Fix (a): remove dead `catalog` field from `AgentState`**

In `backend/chat_agent_agentic.py`, change `AgentState`:

```python
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    result_cart: list[dict] | None
    clarification: dict | None
    missing_items: list[str]
```

Remove `catalog` from the `app.invoke` call in `handle_chat`:

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
```

Also remove the `catalog` key from `_make_graph_state` in the test helper at the top of `TestHandleChat`:

```python
def _make_graph_state(reply="ok", result_cart=None, clarification=None):
    from langchain_core.messages import AIMessage
    return {
        "messages": [AIMessage(content=reply)],
        "result_cart": result_cart,
        "clarification": clarification,
        "missing_items": [],
    }
```

- [x] **Step 7.4 — Fix (b): normalize `missing_items` dedup to lowercase**

In `tools_node`, change the `report_missing` handler:

```python
            elif tc["name"] == "report_missing":
                ingredient = tc["args"].get("ingredient", "").strip().lower()
                if ingredient and ingredient not in missing_items:
                    missing_items.append(ingredient)
```

- [x] **Step 7.5 — Fix (c): sanitize exception messages**

In `tools_node`, change the exception handler:

```python
            try:
                result = tool_fn.invoke(tc["args"])
            except Exception:
                result = json.dumps({"error": "tool execution failed"})
```

- [x] **Step 7.6 — Run tests and confirm they pass**

```bash
python -m pytest tests/test_chat_agent.py::TestCleanups -v
```

Expected: all pass.

- [x] **Step 7.7 — Run full suite**

```bash
python -m pytest tests/test_chat_agent.py -v
```

Expected: all pass.

- [x] **Step 7.8 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "fix(agent): remove dead catalog state field, lowercase missing_items dedup, sanitize tool exceptions"
```

---

## Task 8 — Path traversal guard in `_serve_static`

**Why:** `path.lstrip("/")` only strips leading slashes; it doesn't prevent `..` traversal. A crafted path like `/../backend/chat_agent_agentic.py` resolves outside the frontend directory.

**Files:**
- Modify: `backend/app.py:277-284`
- Test: `tests/test_api.py` (add path traversal tests)

- [x] **Step 8.1 — Write failing test**

Read `tests/test_api.py` first to understand its structure, then add:

```python
class TestServeStaticSecurity:
    """Path traversal protection for _serve_static."""

    def _make_handler(self):
        """Return a handler instance with mocked socket/server — no real HTTP."""
        from backend.app import ShopHandler
        from unittest.mock import MagicMock, patch
        handler = ShopHandler.__new__(ShopHandler)
        handler.server = MagicMock()
        handler.client_address = ("127.0.0.1", 9999)
        handler.requestline = "GET / HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "GET"
        handler.headers = MagicMock()
        handler._headers_buf = []
        handler.wfile = MagicMock()
        handler.rfile = MagicMock()
        return handler

    def test_path_traversal_returns_403(self):
        """A path with .. that escapes frontend/ must get a 403, not serve the file."""
        from unittest.mock import patch, MagicMock, call
        handler = self._make_handler()
        responses = []
        handler.send_response = lambda code: responses.append(code)
        handler.end_headers = MagicMock()
        handler.send_header = MagicMock()

        handler._serve_static("/../backend/chat_agent_agentic.py")

        assert 403 in responses, (
            "Path traversal should return 403, not serve the file. "
            f"Got responses: {responses}"
        )

    def test_normal_path_is_not_blocked(self, tmp_path):
        """A normal path inside frontend/ must not be blocked."""
        from unittest.mock import patch, MagicMock
        import backend.app as app_module

        # Create a fake frontend/index.html
        fake_frontend = tmp_path / "frontend"
        fake_frontend.mkdir()
        (fake_frontend / "index.html").write_bytes(b"<html></html>")

        handler = self._make_handler()
        responses = []
        headers_sent = []
        handler.send_response = lambda code: responses.append(code)
        handler.end_headers = MagicMock()
        handler.send_header = lambda k, v: headers_sent.append((k, v))
        handler.wfile = MagicMock()

        with patch.object(app_module, "ROOT", tmp_path):
            handler._serve_static("/index.html")

        assert 200 in responses, f"Normal path blocked. Responses: {responses}"
```

- [x] **Step 8.2 — Run tests and confirm the traversal test fails**

```bash
python -m pytest tests/test_api.py::TestServeStaticSecurity -v
```

Expected: `test_path_traversal_returns_403` FAIL (currently serves or 404s instead of 403).

- [x] **Step 8.3 — Implement the fix**

In `backend/app.py`, replace `_serve_static` path resolution (lines 278-284):

```python
    def _serve_static(self, path: str):
        if path == "/" or path == "":
            path = "/index.html"
        frontend_root = (ROOT / "frontend").resolve()
        file_path = (ROOT / "frontend" / path.lstrip("/")).resolve()
        # Reject paths that escape the frontend directory
        if not str(file_path).startswith(str(frontend_root) + "/") and file_path != frontend_root:
            self.send_response(403)
            self.end_headers()
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
```

- [x] **Step 8.4 — Run tests and confirm both pass**

```bash
python -m pytest tests/test_api.py::TestServeStaticSecurity -v
```

Expected: both pass.

- [x] **Step 8.5 — Run full API test suite**

```bash
python -m pytest tests/test_api.py -v
```

Expected: all pass.

- [x] **Step 8.6 — Commit**

```bash
git add backend/app.py tests/test_api.py
git commit -m "fix(server): block path traversal in _serve_static with resolved path check"
```

---

## Task 9 — Token-aware history truncation

**Why:** `history[-20:]` is a message-count cutoff. Verbose tool outputs can exhaust `gpt-4o-mini`'s ~128k context window well before 20 messages. Use a character-budget heuristic (~4 chars/token, 40k char budget for history) instead.

**Files:**
- Modify: `backend/chat_agent_agentic.py:276`
- Test: `tests/test_chat_agent.py` (add to `TestCleanups`)

- [x] **Step 9.1 — Write failing test**

Add to `class TestCleanups` in `tests/test_chat_agent.py`:

```python
@patch("backend.chat_agent_agentic.ChatOpenAI")
@patch("backend.chat_agent_agentic._load_catalog")
def test_huge_history_does_not_exceed_char_budget(
    self, mock_load_catalog, mock_llm_cls, sample_catalog
):
    """A history with very long messages must be truncated to fit the char budget."""
    mock_load_catalog.return_value = sample_catalog
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    from langchain_core.messages import AIMessage
    llm.invoke.return_value = AIMessage(content="ok")
    mock_llm_cls.return_value = llm

    # 25 messages of 5000 chars each = 125k chars total, well over any budget
    huge_history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 5000}
        for i in range(25)
    ]

    captured = []
    original_invoke = llm.invoke.side_effect

    def capture(msgs):
        captured.append(list(msgs))
        return AIMessage(content="ok")

    llm.invoke.side_effect = capture
    _reset_app_cache()
    handle_chat("hola", huge_history, [])

    assert captured
    messages = captured[0]
    total_chars = sum(len(str(m.content)) for m in messages)
    # Should be well under 125k — the budget is 40k chars for history
    assert total_chars < 60_000, (
        f"Messages total {total_chars} chars — history not truncated by budget"
    )
```

- [x] **Step 9.2 — Run test and confirm it fails**

```bash
python -m pytest "tests/test_chat_agent.py::TestCleanups::test_huge_history_does_not_exceed_char_budget" -v
```

Expected: FAIL — total chars exceeds 60k.

- [x] **Step 9.3 — Implement the fix**

In `backend/chat_agent_agentic.py`, add a helper before `handle_chat`:

```python
_MAX_HISTORY_CHARS = 40_000  # ~10k tokens; leaves room for system prompt and response


def _trim_history(history: list[dict]) -> list[dict]:
    """Return the most recent history messages that fit within the char budget."""
    trimmed: list[dict] = []
    chars = 0
    for msg in reversed(history):
        content_len = len(msg.get("content", ""))
        if chars + content_len > _MAX_HISTORY_CHARS:
            break
        trimmed.insert(0, msg)
        chars += content_len
    return trimmed
```

Then in `handle_chat`, replace:

```python
    for msg in history[-20:]:
```

With:

```python
    for msg in _trim_history(history):
```

- [x] **Step 9.4 — Run test and confirm it passes**

```bash
python -m pytest "tests/test_chat_agent.py::TestCleanups::test_huge_history_does_not_exceed_char_budget" -v
```

Expected: PASS

- [x] **Step 9.5 — Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: all pass.

- [x] **Step 9.6 — Commit**

```bash
git add backend/chat_agent_agentic.py tests/test_chat_agent.py
git commit -m "fix(agent): replace fixed-count history slice with char-budget trim (~40k chars)"
```

---

## Self-Review

**Spec coverage:**

| Issue | Task |
|---|---|
| #1 request_clarification doesn't halt loop | Task 1 |
| #2 Silent cart drops | Task 3 |
| #3 Multiple set_cart overwrites | Addressed in Task 3 (cart key preserved) + prompt already says call once |
| #4 Clarification UUID not grounded | Task 2 |
| #5 Graph rebuilt every request | Task 5 |
| #6 Catalog loaded every request | Task 5 |
| #7 No loop guard | Task 6 |
| #8 No LLM timeout | Task 6 |
| #9 request_clarification not in prompt | Task 4 |
| #10 Dead catalog state field | Task 7 |
| #11 History truncation ignores tokens | Task 9 |
| #12 Case-sensitive missing_items dedup | Task 7 |
| #13 Exception content leaks to LLM | Task 7 |
| #14 Path traversal in _serve_static | Task 8 |

All 14 issues have corresponding tasks. No gaps.

**Placeholder scan:** No TBDs or "implement later" found.

**Type consistency:** `_validate_cart_with_report` returns `tuple[list[dict], list[dict]]` and is used in `set_cart` tool as `valid, dropped = _validate_cart_with_report(...)`. `_validate_cart` (original) is preserved for `TestValidateCart`. `AgentState` removes `catalog` field — the `_make_graph_state` test helper is updated accordingly.

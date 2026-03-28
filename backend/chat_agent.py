"""
Thin compatibility shim — delegates to chat_agent_agentic.
Owner: Jeremias

Do not add logic here. If the server or UI still imports this module,
it should transparently forward to chat_agent_agentic.handle_chat.
"""

from backend.chat_agent_agentic import handle_chat  # noqa: F401

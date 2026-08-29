"""
chat_backend.py — the front-desk agent's brain, shared by chat.py (terminal
REPL) and server.py (web console). Keeps ONE conversation and ONE tool
implementation so the two front-ends can't drift apart.
"""
import json
import threading

from anthropic import Anthropic

import chat as chat_defs
import events

_lock = threading.Lock()
_messages = []
_client = None

MODEL = "claude-sonnet-5"


def _client_once():
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def reset():
    with _lock:
        _messages.clear()


class Busy(Exception):
    """Raised when a turn is already in flight. A diagnosis takes minutes, and
    the lock used to be held for all of it: a second tab (the projector, a
    judge opening the URL) would block silently behind it, then have the FIRST
    tab's answer broadcast into its own window as if it were the reply. Better
    to say plainly that something is already running."""


def ask(user_text):
    """Runs one full turn (including any tool calls) and returns the agent's
    final text. Publishes tool activity to the 'swarm' channel so the sidebar
    shows front-desk actions alongside the real specialist events.

    Raises Busy if another turn is already running."""
    # If anything below fails, an assistant turn carrying a tool_use can be
    # left with no matching tool_result. _messages is module-global and
    # survives the exception, so EVERY later turn 400s on "tool_use ids were
    # found without tool_result blocks" - the console stays broken for the
    # rest of the process, even after a page reload.
    if not _lock.acquire(blocking=False):
        raise Busy("a diagnosis is already running in this session")
    # Read the marker only once the lock is HELD. Taken before the acquire it
    # could be a stale, smaller index than the list this thread ends up owning:
    # tab B snapshots the length mid-turn, tab A finishes and releases, B
    # acquires and then raises - and the rollback deletes A's COMPLETED turn,
    # stripping a tool_result off an assistant tool_use and creating the
    # permanent breakage this marker exists to prevent.
    _rollback_to = len(_messages)
    try:
        _messages.append({"role": "user", "content": user_text})
        client = _client_once()
        final_text = []

        while True:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                system=chat_defs.SYSTEM_PROMPT,
                tools=chat_defs.TOOLS,
                messages=_messages,
            )
            _messages.append({"role": "assistant", "content": resp.content})
            final_text = [b.text for b in resp.content if b.type == "text"]

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                break

            tool_results = []
            for tu in tool_uses:
                events.publish("swarm", {"label": "tool", "actor": f"front-desk: {tu.name}"})
                fn = chat_defs.DISPATCH.get(tu.name)
                try:
                    result = fn(tu.input) if fn else {"error": f"unknown tool {tu.name}"}
                except (Exception, SystemExit) as e:
                    # SystemExit inherits from BaseException, not Exception, and
                    # the tools raise it constantly - every sandbox guard, the
                    # run lock, the harness runner. Escaping here killed the
                    # worker thread silently: no traceback, no chat event, so
                    # the browser sat on typing dots with a dead send button
                    # forever. The likeliest trigger was also the most public
                    # one: clicking Diagnose while a terminal run holds the lock.
                    result = {"error": f"{type(e).__name__}: {e}"}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
            _messages.append({"role": "user", "content": tool_results})

        return " ".join(final_text) if final_text else "(no text returned)"
    except BaseException:
        del _messages[_rollback_to:]
        raise
    finally:
        _lock.release()

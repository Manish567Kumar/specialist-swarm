"""
events.py — a tiny in-process pub/sub bus, so the swarm's real event stream
(thread spawns, delegations, replies) and the chat front desk's replies can
both drive a live UI without any new dependency. Channels: "chat", "swarm".

Why there is a ring buffer as well as live subscribers: publish() used to
snapshot the subscriber list and drop the item if nobody was listening. An
EventSource takes ~3s to reconnect after a tab refresh or a laptop sleep, so
an item published in that gap was lost FOREVER — and if the lost item was the
chat reply, the UI sat on typing dots with the send button disabled and no way
to recover. Now every item gets a monotonic id and is retained, so a
reconnecting client replays what it missed via Last-Event-ID.
"""
import collections
import itertools
import queue
import threading
import time

_lock = threading.Lock()
_subscribers = []

# Enough to cover a whole diagnosis's event stream, bounded so a long session
# can't grow without limit.
_BUFFER_SIZE = 500
_buffer = collections.deque(maxlen=_BUFFER_SIZE)
_ids = itertools.count(1)


def publish(channel, data):
    with _lock:
        item = {"id": next(_ids), "channel": channel, "data": data, "ts": time.time()}
        _buffer.append(item)
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(item)
        except queue.Full:
            pass
    return item["id"]


def recent(after_id=0):
    """Items published after `after_id`, oldest first. A reconnecting client
    passes its Last-Event-ID here to catch up on what it missed."""
    with _lock:
        return [it for it in _buffer if it["id"] > after_id]


def last_id():
    """Id of the most recent item, or 0 if nothing has been published.

    A RECONNECTING client sends Last-Event-ID and wants everything after it. A
    FRESH page load sends no header at all, and treating that as id 0 replayed
    the entire ring buffer as brand-new UI - which is not a catch-up, it is a
    duplicate. Such a client starts here instead."""
    with _lock:
        return _buffer[-1]["id"] if _buffer else 0


def subscribe():
    """Returns a Queue that receives every future publish() call. Caller
    should call unsubscribe(q) when the client disconnects."""
    q = queue.Queue(maxsize=1000)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def reset():
    """Test/demo helper — clears the buffer without disturbing subscribers."""
    with _lock:
        _buffer.clear()

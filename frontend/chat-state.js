(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TrioChatState = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const VERSION = 2;
  const STORAGE_KEY = 'trio.chat-state.v2';
  const ROOM_IDS = ['group', 'user-agent_a', 'user-agent_b', 'agent_a-agent_b'];

  function defaultRoom() {
    return { draft: '', scrollTop: 0, readCount: 0, pendingSend: null };
  }

  function createDefaultState() {
    const rooms = {};
    for (const id of ROOM_IDS) rooms[id] = defaultRoom();
    return { version: VERSION, activeRoom: 'group', rooms };
  }

  function finiteNonNegative(value) {
    return Number.isFinite(value) && value >= 0 ? value : 0;
  }

  function normalizePending(value, room) {
    if (!value || value.room !== room || typeof value.body !== 'string' ||
        typeof value.key !== 'string' || !value.key) return null;
    return { room, body: value.body, key: value.key };
  }

  function normalize(value) {
    const result = createDefaultState();
    if (!value || value.version !== VERSION || !value.rooms) return result;
    if (ROOM_IDS.includes(value.activeRoom)) result.activeRoom = value.activeRoom;
    for (const id of ROOM_IDS) {
      const source = value.rooms[id];
      if (!source || typeof source !== 'object') continue;
      result.rooms[id] = {
        draft: typeof source.draft === 'string' ? source.draft : '',
        scrollTop: finiteNonNegative(source.scrollTop),
        readCount: finiteNonNegative(source.readCount),
        pendingSend: normalizePending(source.pendingSend, id),
      };
    }
    return result;
  }

  function loadState(storage) {
    try {
      return normalize(JSON.parse(storage.getItem(STORAGE_KEY)));
    } catch (_) {
      return createDefaultState();
    }
  }

  function saveState(storage, state) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(normalize(state)));
    } catch (_) {
      // Storage may be disabled, full, or unavailable in private browsing.
    }
  }

  function roomState(state, room) {
    if (!ROOM_IDS.includes(room)) throw new Error('Unknown room');
    return state.rooms[room];
  }

  function setDraft(state, room, draft) {
    roomState(state, room).draft = String(draft);
  }

  function setActiveRoom(state, room) {
    state.activeRoom = room;
    return roomState(state, room);
  }

  function prepareSend(state, room, makeKey) {
    const current = roomState(state, room);
    const body = current.draft;
    if (current.pendingSend && current.pendingSend.body === body) {
      return { ...current.pendingSend };
    }
    current.pendingSend = { room, body, key: makeKey() };
    return { ...current.pendingSend };
  }

  function completeSend(state, operation) {
    const current = roomState(state, operation.room);
    if (!current.pendingSend || current.pendingSend.key !== operation.key) return;
    current.pendingSend = null;
    if (current.draft === operation.body) current.draft = '';
  }

  function unreadCount(state, room, total) {
    return Math.max(0, finiteNonNegative(total) - roomState(state, room).readCount);
  }

  function markRead(state, room, loadedCount) {
    roomState(state, room).readCount = finiteNonNegative(loadedCount);
  }

  function isFollowing(metrics, threshold = 80) {
    return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold;
  }

  const beijingFormatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });

  function formatBeijing(seconds) {
    const parts = {};
    for (const part of beijingFormatter.formatToParts(new Date(seconds * 1000))) {
      if (part.type !== 'literal') parts[part.type] = part.value;
    }
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  }

  return {
    VERSION, STORAGE_KEY, ROOM_IDS, createDefaultState, loadState, saveState,
    roomState, setDraft, setActiveRoom, prepareSend, completeSend,
    unreadCount, markRead, isFollowing, formatBeijing,
  };
});

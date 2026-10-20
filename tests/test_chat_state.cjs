const assert = require('node:assert/strict');
const test = require('node:test');

const {
  STORAGE_KEY,
  createDefaultState,
  loadState,
  saveState,
  setDraft,
  setActiveRoom,
  prepareSend,
  completeSend,
  unreadCount,
  markRead,
  isFollowing,
  formatBeijing,
} = require('../frontend/chat-state.js');

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
  };
}

test('switching rooms restores each room draft independently', () => {
  const state = createDefaultState();
  setDraft(state, 'group', 'group draft');
  setActiveRoom(state, 'user-agent_a');
  setDraft(state, 'user-agent_a', 'Agent A draft');

  assert.equal(setActiveRoom(state, 'group').draft, 'group draft');
  assert.equal(setActiveRoom(state, 'user-agent_a').draft, 'Agent A draft');
});

test('a late send completion cannot clear another room or a newer draft', () => {
  const state = createDefaultState();
  setDraft(state, 'group', 'old A');
  const send = prepareSend(state, 'group', () => 'key-a');
  setActiveRoom(state, 'user-agent_b');
  setDraft(state, 'user-agent_b', 'draft B');
  setDraft(state, 'group', 'new A');

  completeSend(state, send);

  assert.equal(state.rooms.group.draft, 'new A');
  assert.equal(state.rooms['user-agent_b'].draft, 'draft B');
});

test('a failed send retains its idempotency key for retry after room switches', () => {
  const state = createDefaultState();
  setDraft(state, 'group', 'retry me');
  const first = prepareSend(state, 'group', () => 'stable-key');
  setActiveRoom(state, 'user-agent_a');
  setActiveRoom(state, 'group');
  const retry = prepareSend(state, 'group', () => 'wrong-new-key');

  assert.deepEqual(retry, first);
});

test('saved active room and per-room state survive reload', () => {
  const storage = memoryStorage();
  const state = createDefaultState();
  setActiveRoom(state, 'user-agent_a');
  Object.assign(state.rooms['user-agent_a'], {
    draft: 'saved draft', scrollTop: 321, readCount: 7,
    pendingSend: { room: 'user-agent_a', body: 'saved draft', key: 'saved-key' },
  });
  saveState(storage, state);

  const restored = loadState(storage);
  assert.equal(restored.activeRoom, 'user-agent_a');
  assert.deepEqual(restored.rooms['user-agent_a'], state.rooms['user-agent_a']);
});

test('unavailable, corrupt, and wrong-version storage safely use defaults', () => {
  const unavailable = { getItem() { throw new Error('disabled'); }, setItem() { throw new Error('disabled'); } };
  const corrupt = memoryStorage({ [STORAGE_KEY]: '{not json' });
  const old = memoryStorage({ [STORAGE_KEY]: JSON.stringify({ version: 1, activeRoom: 'user-agent_a' }) });

  assert.equal(loadState(unavailable).activeRoom, 'group');
  assert.equal(loadState(corrupt).activeRoom, 'group');
  assert.equal(loadState(old).activeRoom, 'group');
  assert.doesNotThrow(() => saveState(unavailable, createDefaultState()));
});

test('following latest messages uses the inclusive 80px boundary', () => {
  assert.equal(isFollowing({ scrollHeight: 1000, scrollTop: 520, clientHeight: 400 }), true);
  assert.equal(isFollowing({ scrollHeight: 1000, scrollTop: 519, clientHeight: 400 }), false);
});

test('unread is count minus read count and bottom marks only loaded messages read', () => {
  const state = createDefaultState();
  state.rooms.group.readCount = 3;
  assert.equal(unreadCount(state, 'group', 8), 5);
  markRead(state, 'group', 6);
  assert.equal(unreadCount(state, 'group', 8), 2);
});

test('timestamps are fixed Beijing YYYY-MM-DD HH:mm across host timezones', () => {
  assert.equal(formatBeijing(0), '1970-01-01 08:00');
  assert.equal(formatBeijing(1767225599), '2026-01-01 07:59');
});

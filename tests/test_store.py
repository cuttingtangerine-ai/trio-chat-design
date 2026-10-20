import tempfile
import unittest
from pathlib import Path
from trio_core.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.db')

    def tearDown(self):
        self.tmp.cleanup()

    def test_private_buffer_redacts_content_and_reply(self):
        self.store.post('agent_a-agent_b', 'agent_a', 'private subject', 'a', private=True)
        with self.store.db() as db:
            job = db.execute('SELECT * FROM jobs').fetchone()
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        self.store.post('agent_a-agent_b', 'agent_b', 'private response', 'b', job_id=job['id'])
        public = self.store.read('agent_a-agent_b', 'user')['messages']
        self.assertEqual([m['body'] for m in public], [None, None])
        with self.assertRaises(PermissionError):
            self.store.action('agent_b', 'release', {'buffer': public[0]['buffer']})
        self.store.action('agent_a', 'release', {'buffer': public[0]['buffer']})
        self.assertEqual(self.store.read('agent_a-agent_b', 'user')['messages'][1]['body'], 'private response')

    def test_idempotency_and_room_authorization(self):
        first = self.store.post('group', 'user', 'hello', 'same')
        self.assertEqual(first, self.store.post('group', 'user', 'hello', 'same'))
        self.assertEqual(len(self.store.read('group', 'user')['messages']), 1)
        with self.assertRaises(PermissionError):
            self.store.read('user-agent_b', 'agent_a')
        with self.assertRaises(PermissionError):
            self.store.post('agent_a-agent_b', 'user', 'hello', 'a')

    def test_six_reply_limit_cannot_be_reset_by_ai(self):
        for i in range(6):
            self.store.post('agent_a-agent_b', 'agent_a' if i % 2 == 0 else 'agent_b', str(i), str(i))
        self.assertTrue(self.store.read('agent_a-agent_b', 'user')['paused'])
        with self.assertRaises(ValueError):
            self.store.post('agent_a-agent_b', 'agent_a', 'continue', '7')
        with self.assertRaises(PermissionError):
            self.store.action('agent_a', 'continue', {'room': 'agent_a-agent_b'})
        self.store.action('user', 'continue', {'room': 'agent_a-agent_b'})
        self.assertFalse(self.store.read('agent_a-agent_b', 'user')['paused'])

    def test_human_message_invalidates_stale_reply(self):
        self.store.post('group', 'user', 'first', 'first')
        with self.store.db() as db:
            job = db.execute('SELECT * FROM jobs LIMIT 1').fetchone()
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        self.store.post('group', 'user', 'new direction', 'new')
        with self.assertRaises(ValueError):
            self.store.post('group', job['actor'], 'old reply', 'reply', job_id=job['id'])

    def test_restart_marks_uncertain_jobs_for_manual_retry(self):
        self.store.post('group', 'user', 'test', 'first')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running'")
        restarted = Store(self.store.path)
        restarted.recover()
        self.assertTrue(all(j['status'] == 'failed' for j in restarted.read('group', 'user')['jobs']))


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from trio_core.store import Store


class MessagePagingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / 'messages.db')
        for number in range(7):
            self.store.post('user-agent_a', 'user', f'message {number}', f'key-{number}')

    def tearDown(self):
        self.dir.cleanup()

    def test_initial_page_is_a_bounded_tail_and_can_load_earlier(self):
        tail = self.store.read_page('user-agent_a', 'user', limit=3)

        self.assertEqual([item['body'] for item in tail['messages']], ['message 4', 'message 5', 'message 6'])
        self.assertTrue(tail['has_more_before'])
        self.assertFalse(tail['has_more_after'])
        self.assertEqual(tail['total'], 7)

        earlier = self.store.read_page(
            'user-agent_a', 'user', before=tail['before_cursor'], limit=3
        )
        self.assertEqual([item['body'] for item in earlier['messages']], ['message 1', 'message 2', 'message 3'])
        self.assertTrue(earlier['has_more_before'])

    def test_after_cursor_returns_only_new_messages(self):
        first = self.store.read_page('user-agent_a', 'user', limit=3)
        self.store.post('user-agent_a', 'user', 'new message', 'new-key')

        newer = self.store.read_page(
            'user-agent_a', 'user', after=first['after_cursor'], limit=3
        )
        self.assertEqual([item['body'] for item in newer['messages']], ['new message'])
        self.assertFalse(newer['has_more_after'])

    def test_private_messages_are_still_redacted_for_user_in_pages(self):
        private_id = self.store.post(
            'agent_a-agent_b', 'agent_a', 'not public yet', 'private-key', private=True
        )
        page = self.store.read_page('agent_a-agent_b', 'user', limit=10)

        item = next(item for item in page['messages'] if item['id'] == private_id)
        self.assertIsNone(item['body'])
        self.assertTrue(item['private'])


if __name__ == '__main__':
    unittest.main()

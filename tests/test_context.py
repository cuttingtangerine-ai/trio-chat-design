import json
import tempfile
import unittest
from pathlib import Path
from trio_core.store import Store
from trio_core.context import context, build_prompt

class ContextTests(unittest.TestCase):
    def test_original_agent_a_can_initiate_after_limit_but_callback_cannot_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db')
            for i in range(6):
                store.post('agent_a-agent_b','agent_a' if i%2==0 else 'agent_b','hello',str(i))
            with self.assertRaises(ValueError):
                store.post('agent_a-agent_b','agent_a','automatic reply','blocked')
            store.post('agent_a-agent_b','agent_a','new subject','new-root',initiate=True)
            with store.db() as db:
                state=db.execute("SELECT * FROM rooms WHERE id='agent_a-agent_b'").fetchone()
                self.assertEqual(state['used'],1)
                self.assertEqual(state['epoch'],1)
            with self.assertRaises(ValueError):
                store.post('agent_a-agent_b','agent_a','another','another',initiate=True)

    def test_original_agent_a_callback_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db')
            store.post('group','user','hello','trigger')
            with store.db() as db:
                job=dict(db.execute("SELECT * FROM jobs WHERE actor='agent_a'").fetchone())
                db.execute("UPDATE jobs SET status='waiting_agent_a' WHERE id=?",(job['id'],))
            first=store.post('group','agent_a','response',job['id'],job_id=job['id'])
            self.assertEqual(first,store.post('group','agent_a','response',job['id'],job_id=job['id']))
    def test_audience_is_room_specific_even_with_old_misdirected_history(self):
        history=[{'from':'agent_a','body':'用户，你想聊什么？'}]
        for actor,peer in [('agent_a','Agent B'),('agent_b','Agent A')]:
            prompt=build_prompt({'room':'agent_a-agent_b','actor':actor},history)
            self.assertIn('交谈对象是 '+peer,prompt)
            self.assertIn('不要向她提问',prompt)
            self.assertIn('先前的参与者识别错误',prompt)
        self.assertIn('交谈对象是用户',build_prompt({'room':'user-agent_b','actor':'agent_b'},[]))
        self.assertNotIn('只是旁观者',build_prompt({'room':'group','actor':'agent_a'},[]))
    def test_queued_exchange_stops_at_six_and_continues_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            store.action('user','start',{'room':'agent_a-agent_b'})
            actors=[]
            for _ in range(6):
                with store.db() as db:
                    job=dict(db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created LIMIT 1").fetchone())
                    db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
                actors.append(job['actor'])
                store.post('agent_a-agent_b',job['actor'],'test reply',job['id'],job_id=job['id'])
            self.assertEqual(actors,['agent_a','agent_b']*3)
            with store.db() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0],0)
            store.action('user','continue',{'room':'agent_a-agent_b'})
            with store.db() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0],1)

    def test_private_never_enters_public_context(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            store.post('agent_a-agent_b','agent_a','SECRET','one',private=True)
            job = {'room':'agent_a-agent_b','actor':'agent_b','buffer':None,'trigger_id':'unknown'}
            self.assertNotIn('SECRET', json.dumps(context(store,job)))
            secret = store.read('agent_a-agent_b','agent_b')['messages'][0]
            job['buffer'] = secret['buffer']
            self.assertIn('SECRET',json.dumps(context(store,job)))
            job['room'] = 'group'
            self.assertNotIn('SECRET',json.dumps(context(store,job)))

    def test_initial_group_replies_have_same_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            trigger = store.post('group','user','hello','one')
            job = {'room':'group','actor':'agent_b','buffer':None,'trigger_id':trigger}
            before = context(store,job)
            store.post('group','agent_a','first reply','two')
            self.assertEqual(context(store,job),before)

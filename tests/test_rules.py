import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from core import *
from arcade import Arcade

class Rules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'test.db')
        self.db = Store(self.path)
    def tearDown(self):
        self.db.db.close()
        self.tmp.cleanup()
    def test_math(self):
        for value, expected in [('2..', 2), ('3...', 3), ('5-3', 2), ('5-2', 3), ('(8+1)/3', 3), ('9×2÷3', 6)]: self.assertEqual(calculate(value), expected)
        for value in ['2**999', '__import__("os")', '1/0', '1.5', '9'*101, '1/2', '2//1']:
            with self.assertRaises(ValueError): calculate(value)
    def test_count_reset_and_record(self):
        with self.db.edit(1) as s:
            self.assertTrue(counting(s, 1, '1')[0])
            self.assertTrue(counting(s, 2, '5-3')[0])
            self.assertFalse(counting(s, 2, '3')[0])
            self.assertEqual(s['count'], 0)
            self.assertEqual(s['record'], 2)
            self.assertTrue(counting(s, 1, '1')[0])
            self.assertFalse(counting(s, 2, 'hello')[0])
    def test_target(self):
        with self.db.edit(1) as s:
            s['count'] = TARGET-1
            self.assertTrue(counting(s, 1, str(TARGET))[0])
            self.assertFalse(counting(s, 2, str(TARGET+1))[0])
            self.assertEqual(s['count'], TARGET)
    def test_wait_days(self):
        with self.db.edit(1) as s:
            self.assertTrue(waiting(s, 1, 'Жду сервер день 1', date(2026, 11, 1))[0])
            self.assertFalse(waiting(s, 1, 'Жду сервер день 2', date(2026, 11, 1))[0])
            self.assertTrue(waiting(s, 1, 'Жду сервер день 2', date(2026, 11, 2))[0])
            self.assertFalse(waiting(s, 1, 'Жду сервер день 3', date(2026, 11, 4))[0])
            self.assertTrue(waiting(s, 1, 'Жду сервер день 1', date(2026, 11, 4))[0])
            self.assertEqual(user(s, 1)['best_streak'], 2)
    def test_words(self):
        with self.db.edit(1) as s:
            valid = lambda word: word in ['конь', 'нос', 'слон']
            self.assertTrue(words(s, 1, 'конь', valid)[0])
            self.assertFalse(words(s, 1, 'нос', valid)[0])
            self.assertTrue(words(s, 2, 'нос', valid)[0])
            self.assertTrue(words(s, 1, 'слон', valid)[0])
            self.assertFalse(words(s, 2, 'нос', valid)[0])
    def test_rep(self):
        with self.db.edit(1) as s:
            self.assertFalse(reputation(s, 1, 1, 1, 0)[0])
            self.assertTrue(reputation(s, 1, 2, 1, 0)[0])
            self.assertFalse(reputation(s, 1, 3, -1, 10)[0])
            self.assertTrue(reputation(s, 1, 2, -1, 86400)[0])
            self.assertEqual(user(s, 2)['rep'], 0)
    def test_persistence_and_isolation(self):
        with self.db.edit(1) as s: counting(s, 1, '1')
        other = Store(self.path)
        with other.edit(1) as s: self.assertEqual(s['count'], 1)
        with other.edit(2) as s: self.assertEqual(s['count'], 0)
        other.db.close()
    def test_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.db.edit(1) as s:
                s['count'] = 400
                raise RuntimeError()
        with self.db.edit(1) as s: self.assertEqual(s['count'], 0)
    def test_invite_attribution(self):
        old = {'a': (0, 123), 'b': (1, 456)}
        self.assertEqual(attribute_invite(old, {'a': (1, 123), 'b': (1, 456)}), 123)
        for current in [None, {}, old, {'a': (2, 123), 'b': (1, 456)}, {'a': (1, 123), 'b': (2, 456)}]: self.assertIsNone(attribute_invite(old, current))
        self.assertIsNone(attribute_invite(None, old))
    def test_invite_rewards(self):
        with self.db.edit(1) as s:
            self.assertTrue(invite_reward(s, 2, 1)[0])
            self.assertFalse(invite_reward(s, 2, 1)[0])
            self.assertFalse(invite_reward(s, 3, 1, True)[0])
            self.assertFalse(invite_reward(s, 4, None)[0])
            self.assertFalse(invite_reward(s, 4, 1)[0])
            self.assertEqual(user(s, 1)['points'], 10)

class Games(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.db = Store(':memory:')
    async def asyncTearDown(self): self.db.db.close()
    async def test_snake(self):
        game = Arcade(1, 'snake', self.db, 1, lambda: None)
        game.food = (5, 4)
        game.step('right')
        self.assertEqual(game.score, 1)
        for _ in range(4): game.step('right')
        self.assertTrue(game.done)
        with self.db.edit(1) as s: self.assertEqual(user(s, 1)['snake'], 1)
    async def test_pong(self):
        game = Arcade(1, 'pong', self.db, 1, lambda: None)
        game.ball = (3, 7)
        game.step('stay')
        self.assertEqual(game.score, 1)
        game.ball, game.velocity = (0, 7), (1, 1)
        game.step('stay')
        self.assertTrue(game.done)
        with self.db.edit(1) as s: self.assertEqual(user(s, 1)['pong'], 1)
    async def test_timeout(self):
        released = []
        game = Arcade(1, 'snake', self.db, 1, lambda: released.append(True))
        await game.on_timeout()
        game.finish()
        self.assertEqual(len(released), 1)
        self.assertTrue(all(b.disabled for b in game.children))

if __name__ == '__main__': unittest.main()

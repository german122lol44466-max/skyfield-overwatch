"""Domain rules. No Discord or network dependencies."""
import ast
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from fractions import Fraction

TARGET = 1_000_000_000_000


def calculate(text):
    text = text.strip().rstrip('.…').strip().replace('×', '*').replace('÷', '/')
    if not text or len(text) > 100 or not re.fullmatch(r'[\d\s+*/()%\-.]+', text):
        raise ValueError('Нужно число или выражение: 5-3, (8+1)/3.')
    try:
        tree = ast.parse(text, mode='eval')
        if len(list(ast.walk(tree))) > 50:
            raise ValueError()
        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) is int:
                result = Fraction(node.value)
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                result = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp):
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Add): result = a+b
                elif isinstance(node.op, ast.Sub): result = a-b
                elif isinstance(node.op, ast.Mult): result = a*b
                elif isinstance(node.op, ast.Div): result = a/b
                elif isinstance(node.op, ast.Mod): result = a%b
                else: raise ValueError()
            else: raise ValueError()
            if abs(result.numerator) > 10**18 or result.denominator > 10**18:
                raise ValueError()
            return result
        result = visit(tree.body)
        if result.denominator != 1: raise ValueError()
        return int(result)
    except (SyntaxError, ZeroDivisionError, ValueError, OverflowError, RecursionError):
        raise ValueError('Выражение должно давать целое число; степени и код запрещены.') from None


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS guilds (id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def edit(self, guild):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT data FROM guilds WHERE id=?', (str(guild),)).fetchone()
            state = json.loads(row[0]) if row else {'channels': {}, 'users': {}, 'count': 0, 'record': 0, 'last': None, 'words': [], 'word_last': None, 'rep_events': {}}
            yield state
            self.db.execute('INSERT OR REPLACE INTO guilds VALUES (?,?)', (str(guild), json.dumps(state, ensure_ascii=False)))


def user(state, uid):
    return state['users'].setdefault(str(uid), {'count_ok': 0, 'count_fail': 0, 'words': 0, 'rep': 0, 'streak': 0, 'best_streak': 0, 'days': 0, 'date': None, 'snake': 0, 'pong': 0, 'bio': ''})


def counting(s, uid, text):
    if s['count'] >= TARGET: return False, '🏆 Цель достигнута! Администратор может начать новый раунд.'
    p = user(s, uid)
    try: value = calculate(text)
    except ValueError: value = None
    expected = s['count']+1
    if str(uid) == s['last'] or value != expected:
        s['count'], s['last'] = 0, None
        p['count_fail'] += 1
        return False, f'💥 Счёт сброшен. Ожидалось **{expected}**, один участник не может ходить дважды подряд. Начинаем с **1**.'
    s['count'], s['last'] = value, str(uid)
    s['record'] = max(s['record'], value)
    p['count_ok'] += 1
    return True, '🏆 Достигнуто 1 000 000 000 000!' if value == TARGET else ''


def waiting(s, uid, text, today):
    match = re.fullmatch(r'жду\s+сервер\s+день\s+(\d{1,9})[.!…]*', text.strip(), re.I)
    if not match: return False, 'Формат: **Жду сервер день 1**. Далее — номер своей серии.'
    p = user(s, uid)
    previous = date.fromisoformat(p['date']) if p['date'] else None
    if previous and today <= previous: return False, 'Сегодня уже отметился. Приходи завтра по Москве!'
    number = p['streak']+1 if previous and (today-previous).days == 1 else 1
    if int(match[1]) != number: return False, f'Твоя следующая отметка: **Жду сервер день {number}**.'
    p.update(date=today.isoformat(), streak=number, best_streak=max(p['best_streak'], number), days=p['days']+1)
    return True, f'🔥 Серия: **{number}** дн. • Всего отметок: **{p["days"]}**'


def next_letter(word):
    return next((c for c in reversed(word) if c not in 'ьъы'), '')


def words(s, uid, text, valid):
    word = text.strip().lower().replace('ё', 'е')
    if not re.fullmatch('[а-я]{2,40}', word) or not valid(word):
        return False, 'Нужно русское нарицательное существительное в начальной форме из словаря.'
    if s['word_last'] == str(uid): return False, 'Подожди ход другого участника.'
    if word in s['words']: return False, 'Это слово уже было в текущем раунде.'
    if s['words'] and word[0] != next_letter(s['words'][-1]):
        return False, f'Нужно слово на **{next_letter(s["words"][-1]).upper()}**.'
    s['words'].append(word)
    s['word_last'] = str(uid)
    user(s, uid)['words'] += 1
    return True, f'Следующая буква: **{next_letter(word).upper()}** • Слов в раунде: **{len(s["words"])}**'


def reputation(s, sender, target, value, now):
    if sender == target: return False, 'Себе репутацию менять нельзя.'
    key = str(sender)
    if key in s['rep_events'] and now-s['rep_events'][key] < 86400:
        return False, 'Можно оценить одного участника раз в 24 часа.'
    user(s, target)['rep'] += value
    s['rep_events'][key] = now
    return True, f'Репутация изменена на {value:+d}.'


def attribute_invite(previous, current):
    """Only one known invite with exactly one additional use is attributable."""
    if previous is None or current is None: return None
    changes = [(code, uses-previous[code][0], inviter) for code, (uses, inviter) in current.items() if code in previous and uses != previous[code][0]]
    # Unknown, disappeared or concurrently-used links are intentionally unrewarded.
    if set(previous) != set(current): return None
    if len(changes) == 1 and changes[0][1] == 1: return changes[0][2]
    return None


def invite_reward(s, joined, inviter, is_bot=False):
    seen = s.setdefault('seen_members', [])
    if str(joined) in seen: return False, 'Повторный вход: очки не начисляются.'
    seen.append(str(joined))
    if is_bot: return False, 'За приглашение ботов очки не начисляются.'
    if inviter is None or inviter == joined:
        return False, 'Пригласивший не определён достоверно: без начисления очков.'
    p = user(s, inviter)
    amount = s.get('invite_points', 10)
    p['points'] = p.get('points', 0)+amount
    p['invites'] = p.get('invites', 0)+1
    return True, f'Пригласил <@{inviter}> • +{amount} очков • Всего: {p["points"]}.'

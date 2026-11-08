import asyncio
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import pymorphy3

from core import Store, user, counting, waiting, words, reputation, TARGET, attribute_invite, invite_reward
from arcade import Arcade

load_dotenv()
ROOT = Path(__file__).parent
DB = Path(os.getenv('DATABASE_PATH', str(ROOT / 'data/skyfield.sqlite3')))
DB.parent.mkdir(parents=True, exist_ok=True)
store = Store(str(DB))
morph = pymorphy3.MorphAnalyzer()
active = {}
locks = {}
invite_snapshots = {}


def card(title, description=''):
    e = discord.Embed(title=title, description=description, color=0xff9418)
    e.set_author(name='SKYFIELD / OVERWATCH UNIT')
    e.set_footer(text='λ SKYFIELD • Город живёт, пока мы вместе')
    return e


def profile(guild, member):
    with store.edit(guild) as s: p = dict(user(s, member.id))
    e = card(f'ДОСЬЕ / {member.display_name}', p['bio'] or 'Досье пока не заполнено. Используй /bio.')
    e.set_thumbnail(url=member.display_avatar.url)
    for name, value in [('Приглашено', p.get('invites', 0)), ('Очки', p.get('points', 0)), ('Репутация', f'{p["rep"]:+d}'), ('Верных чисел', p['count_ok']), ('Сбросов счёта', p['count_fail']), ('Принято слов', p['words']), ('Серия ожидания', p['streak'] if p['date'] and (datetime.now(ZoneInfo('Europe/Moscow')).date()-datetime.fromisoformat(p['date']).date()).days <= 1 else 0), ('Лучшая серия', p['best_streak']), ('Дней ожидания', p['days']), ('Snake • рекорд', p['snake']), ('Pong • рекорд', p['pong'])]:
        e.add_field(name=name, value=str(value))
    e.add_field(name='В Discord с', value=discord.utils.format_dt(member.created_at, 'D'))
    if member.joined_at: e.add_field(name='На сервере с', value=discord.utils.format_dt(member.joined_at, 'D'))
    return e


def stats(guild):
    with store.edit(guild) as s:
        e = card('ГОРОДСКАЯ СВОДКА', 'Совместный прогресс сообщества Skyfield')
        e.add_field(name='Счёт', value=f'{s["count"]:,} / {TARGET:,}')
        e.add_field(name='Рекорд счёта', value=f'{s["record"]:,}')
        e.add_field(name='Слов в раунде', value=str(len(s['words'])))
        e.add_field(name='Участников в базе', value=str(len(s['users'])))
        e.add_field(name='Всего дней ожидания', value=str(sum(p['days'] for p in s['users'].values())))
        e.add_field(name='Каналы', value='\n'.join(f'{k}: <#{v}>' for k, v in s['channels'].items()) or 'Администратор: /setup', inline=False)
        return e


def leaderboard(guild, metric):
    with store.edit(guild) as s:
        top = sorted(s['users'].items(), key=lambda p: p[1].get(metric, 0), reverse=True)[:10]
    return card('ЛИДЕРЫ / '+{'points': 'ОЧКИ', 'invites': 'ПРИГЛАШЕНИЯ', 'rep': 'РЕПУТАЦИЯ', 'count_ok': 'СЧЁТ', 'words': 'СЛОВА', 'best_streak': 'ОЖИДАНИЕ', 'snake': 'SNAKE', 'pong': 'PONG'}[metric], '\n'.join(f'**{n}.** <@{uid}> — **{p.get(metric, 0)}**' for n, (uid, p) in enumerate(top, 1)) or 'Здесь появятся первые участники.')


async def start_game(i, kind):
    key = (i.guild_id, i.user.id)
    if key in active:
        await i.response.send_message('Сначала заверши текущую игру (или дождись тайм-аута).', ephemeral=True)
        return
    view = Arcade(i.user.id, kind, store, i.guild_id, lambda: active.pop(key, None))
    active[key] = view
    try:
        await i.response.send_message(embed=view.embed(), view=view, ephemeral=True)
        view.message = await i.original_response()
    except Exception:
        view.finish()
        raise


class Panel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    async def interaction_check(self, i):
        with store.edit(i.guild_id) as s: channel = s['channels'].get('commands')
        if channel and i.channel_id != channel:
            await i.response.send_message(f'Панель перенесена в <#{channel}>. Администратор может создать её через /panel.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Моё досье', style=discord.ButtonStyle.primary, custom_id='sf:profile')
    async def dossier(self, i, button): await i.response.send_message(embed=profile(i.guild_id, i.user), ephemeral=True)

    @discord.ui.button(label='Статистика', custom_id='sf:stats')
    async def statistics(self, i, button): await i.response.send_message(embed=stats(i.guild_id), ephemeral=True)

    @discord.ui.button(label='Лидеры', custom_id='sf:top')
    async def top(self, i, button): await i.response.send_message(embed=leaderboard(i.guild_id, 'rep'), ephemeral=True)

    @discord.ui.button(label='Snake', style=discord.ButtonStyle.success, custom_id='sf:snake', row=1)
    async def snake(self, i, button): await start_game(i, 'snake')

    @discord.ui.button(label='Pong', style=discord.ButtonStyle.success, custom_id='sf:pong', row=1)
    async def pong(self, i, button): await start_game(i, 'pong')


async def snapshot_invites(guild):
    try:
        invites = await guild.invites()
        current = {v.code: (v.uses or 0, v.inviter.id if v.inviter else None) for v in invites}
    except discord.HTTPException:
        current = None
        logging.warning('Invite tracking unavailable for guild %s: Manage Server permission required', guild.id)
    invite_snapshots[guild.id] = current
    return current


class CommandTree(app_commands.CommandTree):
    async def interaction_check(self, i):
        if not i.guild_id: return False
        if i.user.guild_permissions.manage_guild: return True
        with store.edit(i.guild_id) as s: channel = s['channels'].get('commands')
        if channel and i.channel_id != channel:
            await i.response.send_message(f'Команды бота доступны в <#{channel}>.', ephemeral=True)
            return False
        return True


class Unit(commands.Bot):
    async def setup_hook(self):
        self.add_view(Panel())
        gid = os.getenv('GUILD_ID')
        if gid:
            guild = discord.Object(id=int(gid))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else: await self.tree.sync()

    async def on_ready(self):
        logging.info('Skyfield online: %s', self.user)
        for guild in self.guilds:
            async with locks.setdefault(guild.id, asyncio.Lock()):
                await snapshot_invites(guild)
                with store.edit(guild.id) as state:
                    seen = set(state.setdefault('seen_members', []))
                    seen.update(str(m.id) for m in guild.members)
                    state['seen_members'] = list(seen)

    async def on_disconnect(self):
        invite_snapshots.clear()

    async def on_invite_create(self, invite):
        if invite.guild:
            async with locks.setdefault(invite.guild.id, asyncio.Lock()):
                snapshot = invite_snapshots.get(invite.guild.id)
                if snapshot is not None:
                    snapshot[invite.code] = (invite.uses or 0, invite.inviter.id if invite.inviter else None)

    async def on_member_join(self, member):
        guild = member.guild
        async with locks.setdefault(guild.id, asyncio.Lock()):
            previous = invite_snapshots.get(guild.id)
            current = await snapshot_invites(guild)
            inviter = attribute_invite(previous, current)
            with store.edit(guild.id) as s:
                ok, reason = invite_reward(s, member.id, inviter, member.bot)
                channel_id = s['channels'].get('invites')
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel:
                try:
                    await channel.send(embed=card('НОВЫЙ ЖИТЕЛЬ', f'{member.mention} присоединился.\n{reason}'))
                except discord.HTTPException:
                    logging.warning('Could not send invite log to %s', channel_id)


    async def on_message(self, message):
        if not message.guild or message.author.bot or message.webhook_id: return
        async with locks.setdefault(message.guild.id, asyncio.Lock()):
            with store.edit(message.guild.id) as s:
                mode = next((k for k, cid in s['channels'].items() if cid == message.channel.id), None)
                if mode not in ('count', 'words', 'wait'): return
                if mode == 'count': ok, reply = counting(s, message.author.id, message.content)
                elif mode == 'wait': ok, reply = waiting(s, message.author.id, message.content, datetime.now(ZoneInfo('Europe/Moscow')).date())
                else: ok, reply = words(s, message.author.id, message.content, valid_word)
            try:
                await message.add_reaction('✅' if ok else '❌')
                if reply: await message.reply(reply, mention_author=False)
            except discord.HTTPException:
                logging.warning('Could not acknowledge a game message in channel %s', message.channel.id)


def valid_word(word):
    if not morph.word_is_known(word): return False
    return any(p.tag.POS == 'NOUN' and p.normal_form.replace('ё', 'е') == word and not ({'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Abbr'} & set(p.tag.grammemes)) for p in morph.parse(word))


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = Unit(tree_cls=CommandTree, command_prefix=commands.when_mentioned, intents=intents, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name='setup', description='Настроить каналы игр, команд, приглашений и награду')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(i: discord.Interaction, count: discord.TextChannel, words: discord.TextChannel, wait: discord.TextChannel, commands_channel: discord.TextChannel, invites: discord.TextChannel, invite_points: app_commands.Range[int, 1, 10000] = 10):
    if not i.guild.me.guild_permissions.manage_guild:
        await i.response.send_message('Для учёта приглашений выдай боту право «Управлять сервером».', ephemeral=True); return
    channels = [count, words, wait, commands_channel, invites]
    if len({c.id for c in channels}) != 5:
        await i.response.send_message('Нужны пять разных каналов.', ephemeral=True); return
    for c in channels:
        p = c.permissions_for(i.guild.me)
        if not all((p.view_channel, p.send_messages, p.embed_links, p.attach_files, p.read_message_history, p.add_reactions)):
            await i.response.send_message(f'Не хватает прав в {c.mention}: просмотр, сообщения, embeds, история, реакции, файлы.', ephemeral=True); return
    with store.edit(i.guild_id) as s:
        s['channels'] = {'count': count.id, 'words': words.id, 'wait': wait.id, 'commands': commands_channel.id, 'invites': invites.id}
        s['invite_points'] = invite_points
    await i.response.defer(ephemeral=True)
    async with locks.setdefault(i.guild_id, asyncio.Lock()):
        await snapshot_invites(i.guild)
    await i.followup.send(embed=stats(i.guild_id), ephemeral=True)


@bot.tree.command(name='invites_sync', description='Обновить снимок ссылок после создания приглашений')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def invites_sync(i: discord.Interaction):
    await i.response.defer(ephemeral=True)
    async with locks.setdefault(i.guild_id, asyncio.Lock()):
        result = await snapshot_invites(i.guild)
    await i.followup.send('Приглашения синхронизированы. Теперь можно приглашать новых участников.' if result is not None else 'Нет доступа к приглашениям. Проверь право «Управлять сервером».', ephemeral=True)


@bot.tree.command(name='panel', description='Разместить постоянную панель Skyfield в этом канале')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def panel(i: discord.Interaction):
    with store.edit(i.guild_id) as s: channel = s['channels'].get('commands')
    if channel and i.channel_id != channel:
        await i.response.send_message(f'Размести панель в <#{channel}>.', ephemeral=True); return
    e = card('OVERWATCH UNIT', '**СЕРВИСЫ СООБЩЕСТВА / ONLINE**\n\nТвоё досье, общие достижения и аркадные игры — в одном месте.\n\n**01 / СЧЁТ**\nОт 1 до триллиона. Ошибка обнуляет общий прогресс.\n**02 / СЛОВА**\nПродолжай цепочку и не повторяйся.\n**03 / ЖДУ СЕРВЕР**\nОтмечайся каждый день и сохраняй серию.\n\nПравила: /help • Чужой профиль: /profile • Репутация: /rep')
    e.set_thumbnail(url='attachment://logo.png')
    await i.response.send_message(embed=e, file=discord.File(ROOT/'assets/logo.png', filename='logo.png'), view=Panel())


@bot.tree.command(name='profile', description='Посмотреть своё или чужое досье')
@app_commands.guild_only()
async def profile_cmd(i: discord.Interaction, member: discord.Member | None = None):
    await i.response.send_message(embed=profile(i.guild_id, member or i.user))


@bot.tree.command(name='bio', description='Установить текст о себе (до 250 символов)')
@app_commands.guild_only()
async def bio(i: discord.Interaction, text: app_commands.Range[str, 1, 250]):
    with store.edit(i.guild_id) as s: user(s, i.user.id)['bio'] = discord.utils.escape_markdown(text)
    await i.response.send_message('Досье обновлено.', ephemeral=True)


@bot.tree.command(name='stats', description='Статистика сервера')
@app_commands.guild_only()
async def stats_cmd(i: discord.Interaction): await i.response.send_message(embed=stats(i.guild_id))


@bot.tree.command(name='top', description='Таблица лидеров')
@app_commands.guild_only()
@app_commands.choices(category=[app_commands.Choice(name=n, value=v) for n, v in [('Очки', 'points'), ('Приглашения', 'invites'), ('Репутация', 'rep'), ('Счёт', 'count_ok'), ('Слова', 'words'), ('Серия ожидания', 'best_streak'), ('Snake', 'snake'), ('Pong', 'pong')]])
async def top(i: discord.Interaction, category: app_commands.Choice[str]):
    await i.response.send_message(embed=leaderboard(i.guild_id, category.value))


@bot.tree.command(name='rep', description='Поставить +rep или -rep участнику (раз в 24 часа)')
@app_commands.guild_only()
@app_commands.choices(value=[app_commands.Choice(name='+rep', value=1), app_commands.Choice(name='-rep', value=-1)])
async def rep(i: discord.Interaction, member: discord.Member, value: app_commands.Choice[int]):
    if member.bot:
        await i.response.send_message('Ботов оценивать нельзя.', ephemeral=True); return
    with store.edit(i.guild_id) as s: ok, reply = reputation(s, i.user.id, member.id, value.value, time.time())
    await i.response.send_message(f'{member.mention}: {reply}', ephemeral=not ok)


@bot.tree.command(name='snake', description='Пошаговая змейка на кнопках')
@app_commands.guild_only()
async def snake(i: discord.Interaction): await start_game(i, 'snake')


@bot.tree.command(name='pong', description='Пошаговый одиночный Pong: отбивай мяч')
@app_commands.guild_only()
async def pong(i: discord.Interaction): await start_game(i, 'pong')


@bot.tree.command(name='reset', description='Начать новый раунд счёта или слов; рекорды сохранятся')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(game=[app_commands.Choice(name='Счёт', value='count'), app_commands.Choice(name='Слова', value='words')])
async def reset(i: discord.Interaction, game: app_commands.Choice[str], confirm: bool):
    if not confirm:
        await i.response.send_message('Для сброса укажи confirm: True.', ephemeral=True); return
    with store.edit(i.guild_id) as s:
        if game.value == 'count': s.update(count=0, last=None)
        else: s.update(words=[], word_last=None)
    await i.response.send_message(f'Новый раунд: {game.name}. Рекорды сохранены.')


@bot.tree.command(name='help', description='Команды и правила игр Skyfield')
@app_commands.guild_only()
async def help_cmd(i: discord.Interaction):
    await i.response.send_message(embed=card('СПРАВОЧНИК UNIT', '**Счёт:** каждое новое сообщение в канале — ход. 1, 2.., 3... или 5-2; разрешены + − * / % и скобки. Ошибка, текст или два хода подряд сбрасывают счёт. Удаление и редактирование не отменяют ход.\n\n**Слова:** русские нарицательные существительные в начальной форме. Без повторов; ходят по очереди. Ё=Е, конечные Ь/Ъ/Ы пропускаются. Ошибка не сбрасывает раунд. Словарь может не знать редкие слова.\n\n**Ожидание:** «Жду сервер день 1», завтра — день 2. Один раз в календарный день по Москве; пропуск дня начинает серию с 1.\n\n**Репутация:** /rep, один голос в 24 часа; себе и ботам нельзя.\n\n**Приглашения:** приглашай по своей обычной постоянной ссылке. Очки и число приглашённых — в /profile и /top. Повторные входы и неопределённые приглашения не оплачиваются.\n\n**Досье:** /profile, /bio, /stats, /top.\n**Аркада:** /snake, /pong — один клик = один шаг, Pong одиночный.\n**Администратор:** /setup, /panel, /reset.'), ephemeral=True)


@bot.tree.error
async def command_error(i, error):
    if isinstance(error, app_commands.CheckFailure): message = 'Недостаточно прав для этой команды.'
    else:
        logging.error('Command failed', exc_info=(type(error), error, error.__traceback__))
        message = 'Не удалось выполнить команду. Проверь права бота и журнал сервера.'
    if i.response.is_done(): await i.followup.send(message, ephemeral=True)
    else: await i.response.send_message(message, ephemeral=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    token = os.getenv('DISCORD_TOKEN', '')
    if not token or token == 'paste_token_here': raise SystemExit('Укажи DISCORD_TOKEN в .env')
    bot.run(token, log_handler=None)

"""Development subscriptions and announcement embeds, registered by install()."""
import asyncio
from pathlib import Path

import discord
from discord import app_commands

ORANGE = 0xff9418


def role_problem(guild, role):
    if role is None:
        return 'Роль подписчиков удалена. Администратор должен повторить /dev_setup.'
    if role.is_default() or role.managed or role.permissions.value:
        return 'Нужна отдельная обычная роль для пингов, без разрешений и административных прав.'
    if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
        return 'Выдай боту «Управлять ролями» и подними его роль выше роли подписчиков.'
    return None


def channel_problem(guild, channel, files=False):
    if channel is None:
        return 'Канал удалён или недоступен. Повтори /dev_setup.'
    p = channel.permissions_for(guild.me)
    if not (p.view_channel and p.send_messages and p.embed_links and (not files or p.attach_files)):
        return 'Боту нужны права просмотра канала, отправки сообщений, встраивания ссылок и прикрепления файлов.'
    return None


def update_embed(title, changes, author, filename=None):
    e = discord.Embed(title=title, description=changes, color=ORANGE, timestamp=discord.utils.utcnow())
    e.set_author(name='SKYFIELD / ДНЕВНИК РАЗРАБОТКИ')
    e.set_thumbnail(url='attachment://logo.png')
    if filename: e.set_image(url='attachment://'+filename)
    e.set_footer(text=f'OVERWATCH UNIT • Опубликовал {author}')
    return e


def install(bot, store, root):
    configuration_locks = {}
    def lock(gid):
        return configuration_locks.setdefault(gid, asyncio.Lock())

    class SubscriptionPanel(discord.ui.View):
        def __init__(self): super().__init__(timeout=None)

        async def change(self, i, subscribe):
            if not i.guild:
                await i.response.send_message('Эта панель работает на сервере.', ephemeral=True)
                return
            await i.response.defer(ephemeral=True)
            async with lock(i.guild_id):
                with store.edit(i.guild_id) as state: cfg = dict(state.get('development', {}))
                if not i.message or cfg.get('message') != i.message.id or cfg.get('panel_channel') != i.channel_id:
                    await i.followup.send('Панель устарела. Используй новую панель подписки, размещённую администрацией.', ephemeral=True)
                    return
                role = i.guild.get_role(cfg.get('role', 0))
                error = role_problem(i.guild, role)
                if error:
                    await i.followup.send(error, ephemeral=True); return
                try:
                    # Fetch fresh role state; do not depend on a stale interaction member cache.
                    member = await i.guild.fetch_member(i.user.id)
                    has_role = role in member.roles
                    if subscribe and not has_role:
                        await member.add_roles(role, reason='Подписка на новости разработки Skyfield')
                    elif not subscribe and has_role:
                        await member.remove_roles(role, reason='Отписка от новостей разработки Skyfield')
                    text = ('Подписка включена! Теперь ты получаешь пинги о разработке.' if not has_role else 'Ты уже подписан на новости.') if subscribe else ('Подписка отключена.' if has_role else 'Ты уже отписан от новостей.')
                    await i.followup.send(text, ephemeral=True)
                except discord.HTTPException:
                    await i.followup.send('Не удалось изменить роль. Проверь её положение и права бота; затем попробуй ещё раз.', ephemeral=True)

        @discord.ui.button(label='Получать пинги', emoji='🔔', style=discord.ButtonStyle.success, custom_id='sf:dev:subscribe:v1')
        async def subscribe(self, i, button): await self.change(i, True)

        @discord.ui.button(label='Не получать пинги', emoji='🔕', style=discord.ButtonStyle.secondary, custom_id='sf:dev:unsubscribe:v1')
        async def unsubscribe(self, i, button): await self.change(i, False)

    @bot.tree.command(name='dev_setup', description='Выбрать каналы разработки, роль пингов и разместить панель подписки')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True, manage_roles=True)
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    @app_commands.describe(panel_channel='Куда отправить панель подписки', role='Отдельная роль без прав: например Новости разработки', updates_channel='Куда публиковать новости через /dev_update')
    async def dev_setup(i: discord.Interaction, panel_channel: discord.TextChannel, role: discord.Role, updates_channel: discord.TextChannel):
        error = role_problem(i.guild, role)
        if not error and i.user.id != i.guild.owner_id and role >= i.user.top_role:
            error = 'Роль подписки должна быть ниже твоей самой высокой роли.'
        error = error or channel_problem(i.guild, panel_channel, True) or channel_problem(i.guild, updates_channel, True)
        if error:
            await i.response.send_message(error, ephemeral=True); return
        await i.response.defer(ephemeral=True)
        async with lock(i.guild_id):
            with store.edit(i.guild_id) as state:
                old = dict(state.get('development', {}))
                game_channels = [state['channels'].get(k) for k in ('count', 'words', 'wait')]
            if panel_channel.id in game_channels or updates_channel.id in game_channels:
                await i.followup.send('Выбери каналы вне игр «счёт», «слова» и «жду сервер».', ephemeral=True); return
            if old and old.get('role') != role.id and i.guild.get_role(old.get('role', 0)) is not None:
                await i.followup.send('У подписки уже назначена другая роль. Используй прежнюю роль: так существующие подписчики не потеряют подписку. Её можно переименовать в настройках Discord.', ephemeral=True); return
            embed = discord.Embed(title='Хотите ли вы получать пинги о разработке?', description='**Будь в курсе развития Skyfield.**\n\nНовые механики, обновления и закулисье разработки — подписывайся, чтобы не пропустить важное.\n\n🔔 **Получать пинги** — получить роль подписчика.\n🔕 **Не получать пинги** — снять роль в любой момент.', color=ORANGE)
            embed.set_author(name='SKYFIELD / НА СВЯЗИ С РАЗРАБОТКОЙ')
            embed.set_thumbnail(url='attachment://logo.png')
            embed.set_footer(text='OVERWATCH UNIT • Подписка добровольная')
            message = await panel_channel.send(embed=embed, file=discord.File(Path(root)/'assets/logo.png', filename='logo.png'), view=SubscriptionPanel(), allowed_mentions=discord.AllowedMentions.none())
            with store.edit(i.guild_id) as state:
                state['development'] = {'panel_channel': panel_channel.id, 'updates_channel': updates_channel.id, 'role': role.id, 'message': message.id}
            if old.get('message'):
                previous_channel = i.guild.get_channel(old.get('panel_channel'))
                if previous_channel:
                    try:
                        await previous_channel.get_partial_message(old['message']).edit(view=None)
                    except discord.HTTPException: pass
            await i.followup.send(f'Панель: {message.jump_url}\nОбновления: {updates_channel.mention}\nРоль: {role.mention}', ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @bot.tree.command(name='dev_update', description='Опубликовать красивую карточку обновления разработки')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(title='Название обновления', changes='Что изменилось: описание или список, поддерживается Markdown', image='Необязательное изображение PNG, JPG, GIF или WebP до 8 МБ', ping='Упомянуть роль подписчиков (по умолчанию нет)')
    async def dev_update(i: discord.Interaction, title: app_commands.Range[str, 1, 200], changes: app_commands.Range[str, 1, 3500], image: discord.Attachment | None = None, ping: bool = False):
        if not title.strip() or not changes.strip():
            await i.response.send_message('Заголовок и описание не должны быть пустыми.', ephemeral=True); return
        with store.edit(i.guild_id) as state: cfg = dict(state.get('development', {}))
        if not cfg:
            await i.response.send_message('Сначала настрой разработку через /dev_setup.', ephemeral=True); return
        channel = i.guild.get_channel(cfg.get('updates_channel'))
        error = channel_problem(i.guild, channel, True)
        role = i.guild.get_role(cfg.get('role', 0))
        if ping:
            error = error or role_problem(i.guild, role)
            if not error and not (role.mentionable or channel.permissions_for(i.guild.me).mention_everyone):
                error = 'Для пинга разреши упоминать роль подписчиков в настройках роли либо дай боту право упоминать все роли в канале обновлений.'
        suffix = Path(image.filename).suffix.lower() if image else ''
        if image and (suffix not in {'.png', '.jpg', '.jpeg', '.gif', '.webp'} or not (image.content_type or '').startswith('image/') or image.size > min(8*1024*1024, i.guild.filesize_limit)):
            error = 'Прикрепи PNG, JPG, GIF или WebP не больше 8 МБ и лимита сервера.'
        if error:
            await i.response.send_message(error, ephemeral=True); return
        await i.response.defer(ephemeral=True)
        files = [discord.File(Path(root)/'assets/logo.png', filename='logo.png')]
        try:
            filename = 'update'+suffix if image else None
            if image: files.append(await image.to_file(filename=filename))
            message = await channel.send(content=role.mention if ping else None, embed=update_embed(title, changes, i.user.display_name, filename), files=files, allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role] if ping else [], replied_user=False))
            await i.followup.send(f'Обновление опубликовано: {message.jump_url}', ephemeral=True)
        finally:
            for file in files: file.close()

    return SubscriptionPanel

import random
import asyncio
import discord


class Arcade(discord.ui.View):
    """One button press is one simulation step, never a fake real-time loop."""
    def __init__(self, owner, kind, store, guild, release):
        super().__init__(timeout=180)
        self.owner, self.kind, self.store, self.guild = owner, kind, store, guild
        self.release = release
        self.done, self.score, self.message = False, 0, None
        self.lock = asyncio.Lock()
        self.body, self.direction = [(4, 4), (3, 4), (2, 4)], (1, 0)
        self.food = self.new_food()
        self.ball, self.velocity, self.paddle = (4, 2), (1, 1), 4
        for label, value in ([('⬆️', 'up'), ('⬅️', 'left'), ('⬇️', 'down'), ('➡️', 'right')] if kind == 'snake' else [('⬅️', 'left'), ('⏺️ Ждать', 'stay'), ('➡️', 'right')]):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            async def callback(i, action=value): await self.move(i, action)
            button.callback = callback
            self.add_item(button)
        stop = discord.ui.Button(label='Завершить', style=discord.ButtonStyle.danger)
        async def quit_game(i):
            async with self.lock:
                self.finish()
                await i.response.edit_message(embed=self.embed(), view=self)
        stop.callback = quit_game
        self.add_item(stop)

    def new_food(self):
        free = [(x, y) for y in range(9) for x in range(9) if (x, y) not in self.body]
        return random.choice(free) if free else None

    async def interaction_check(self, i):
        if i.user.id != self.owner:
            await i.response.send_message('Это чужая игра. Запусти свою через /snake или /pong.', ephemeral=True)
            return False
        return True

    def finish(self):
        if self.done: return
        self.done = True
        from core import user
        with self.store.edit(self.guild) as s:
            p = user(s, self.owner)
            p[self.kind] = max(p[self.kind], self.score)
        for item in self.children: item.disabled = True
        self.release()
        self.stop()

    async def on_timeout(self):
        async with self.lock:
            self.finish()
            if self.message:
                try: await self.message.edit(embed=self.embed(), view=self)
                except discord.HTTPException: pass

    def step(self, action):
        if self.kind == 'snake':
            direction = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}[action]
            if direction == (-self.direction[0], -self.direction[1]):
                direction = self.direction
            self.direction = direction
            head = (self.body[0][0]+direction[0], self.body[0][1]+direction[1])
            eating = head == self.food
            if not all(0 <= v < 9 for v in head) or head in (self.body if eating else self.body[:-1]):
                self.finish(); return
            self.body.insert(0, head)
            if eating:
                self.score += 1
                self.food = self.new_food()
                if self.food is None: self.finish()
            else: self.body.pop()
        else:
            self.paddle = max(1, min(7, self.paddle+{'left': -1, 'stay': 0, 'right': 1}[action]))
            x, y = self.ball
            dx, dy = self.velocity
            if not 0 <= x+dx < 9: dx = -dx
            if y+dy < 0: dy = 1
            nx, ny = x+dx, y+dy
            if ny == 8:
                if abs(nx-self.paddle) > 1:
                    self.ball = (nx, ny)
                    self.finish(); return
                self.score += 1
                dy, ny = -1, 7
            self.ball, self.velocity = (nx, ny), (dx, dy)

    async def move(self, i, action):
        async with self.lock:
            if not self.done: self.step(action)
            await i.response.edit_message(embed=self.embed(), view=self)

    def embed(self):
        grid = [['·' for _ in range(9)] for _ in range(9)]
        if self.kind == 'snake':
            if self.food: grid[self.food[1]][self.food[0]] = '◆'
            for x, y in self.body: grid[y][x] = '●'
            x, y = self.body[0]; grid[y][x] = '◉'
        else:
            for x in range(self.paddle-1, self.paddle+2): grid[8][x] = '━'
            x, y = self.ball; grid[y][x] = '●'
        title = 'SNAKE' if self.kind == 'snake' else 'PONG • SOLO'
        e = discord.Embed(title=f'{title} / {"ЗАВЕРШЕНО" if self.done else "В ИГРЕ"}', description='```\n'+'\n'.join(' '.join(row) for row in grid)+'\n```', color=0xff9418)
        e.add_field(name='Счёт', value=str(self.score))
        e.set_footer(text='Один клик — один ход • 3 минуты бездействия — завершение')
        return e

import logging
from typing import Optional, List, Set, Dict

from discord import Embed, Color, Message, Member, TextChannel, Forbidden, HTTPException
from discord.ext.commands import Cog, Bot
from discord_slash import SlashContext
from discord_slash.cog_ext import cog_slash, cog_subcommand
from discord_slash.model import SlashCommandPermissionType
from discord_slash.utils.manage_commands import create_option, create_permission

from dingomata.cogs.gamecode.pool import MemberPool, MemberRoleError
from dingomata.config import get_guild_config, get_guilds, get_mod_permissions

log = logging.getLogger(__name__)


class GameCodeSenderCommands(Cog, name='Game Code Sender'):
    """RNG-based Game Code distributor."""
    _GROUP_NAME = 'game'
    _PLAYER_PERMS = {
        guild: [create_permission(role, SlashCommandPermissionType.ROLE, True)
                for role in get_guild_config(guild).game_code.player_roles]
        for guild in get_guilds() if None not in get_guild_config(guild).game_code.player_roles
    }  # Precompute player permissions on load

    def __init__(self, bot: Bot):
        """Initialize application state."""
        self._bot = bot
        self._pools: Dict[int, MemberPool] = {}
        self._title = ''
        self._current_message: Dict[int, Message] = {}
        self._picked_users: Dict[int, List[Member]] = {}
        self._previously_selected_users: Dict[int, Set[Member]] = {}

    @cog_slash(
        name='join',
        description='Join the open game pool.',
        guild_ids=get_guilds(),
        permissions=_PLAYER_PERMS,
    )
    async def join(self, ctx: SlashContext) -> None:
        guild_id = ctx.guild.id
        pool = self._pool_for_guild(guild_id)
        if pool.is_open:
            if ctx.author in self._previously_selected_users:
                log.info(f'Rejected join request from {ctx.author}: recently selected')
                await ctx.reply('You cannot join this pool because you were recently selected.', hidden=True)
                return
            try:
                pool.add_member(ctx.author)
                log.info(f"Joined successfully: {ctx.author}")
                await ctx.reply(get_guild_config(guild_id).game_code.message_joined.format(title=self._title),
                                hidden=True)
            except MemberRoleError as e:
                await ctx.reply(str(e), hidden=True)
                log.warning(f"Rejected join request from {ctx.author}: missing roles.")
        else:
            await ctx.reply(f"You can't join the pool, it's not open right now.", hidden=True)
            log.info(f"Rejected join request from {ctx.author}: pool closed")

    @cog_slash(
        name='leave',
        description="Leave the open game pool that you've already joined.",
        guild_ids=get_guilds(),
        permissions=_PLAYER_PERMS,
    )
    async def leave(self, ctx: SlashContext) -> None:
        guild_id = ctx.guild.id
        pool = self._pool_for_guild(guild_id)
        if pool.is_open:
            pool.remove_member(ctx.author)
            await ctx.reply(get_guild_config(ctx.guild.id).game_code.message_left.format(title=self._title),
                            hidden=True)
            log.info(f"Member removed from pool: {ctx.author}")
        else:
            await ctx.reply(f'The pool is currently closed. You have not been added.', hidden=True)
            log.info(f"Rejected unjoin request from {ctx.author}: pool closed")

    @cog_subcommand(
        base=_GROUP_NAME,
        name='open',
        description='Open a new game pool for people to join.',
        guild_ids=get_guilds(),
        options=[
            create_option(name='title', description='Name of the game to start', option_type=str, required=True),
        ],
        base_permissions=get_mod_permissions(),
        base_default_permission=False,
    )
    async def open(self, ctx: SlashContext, *, title: str = '') -> None:
        self._pool_for_guild(ctx.guild.id).open()
        if title:
            self._title = title
        embed = Embed(title=get_guild_config(ctx.guild.id).game_code.message_opened.format(title=title),
                      description=get_guild_config(ctx.guild.id).game_code.message_opened_subtitle.format(title=title),
                      color=Color.gold(),
                      )
        await self._channel_for_guild(ctx.guild.id).send(embed=embed)
        log.info(f'Pool opened with title: {self._title}')
        await ctx.reply(f'Done, opened a pool with title {self._title}')

    @cog_subcommand(
        base=_GROUP_NAME,
        name='close',
        description='Close the open pool.',
        guild_ids=get_guilds(),
        base_permissions=get_mod_permissions(),
        base_default_permission=False,
    )
    async def close(self, ctx: SlashContext) -> None:
        pool = self._pool_for_guild(ctx.guild.id)
        pool.close()
        embed = Embed(
            title=get_guild_config(ctx.guild.id).game_code.message_closed.format(title=self._title),
            description=f'Total Entries: {pool.size}',
            color=Color.dark_red())
        await self._channel_for_guild(ctx.guild.id).send(embed=embed)
        log.info(f'Pool closed')
        await ctx.reply(f'Done, pool is closed.')

    @cog_subcommand(
        base=_GROUP_NAME,
        name='pick',
        description='Pick users randomly from the pool and send them a DM.',
        guild_ids=get_guilds(),
        options=[
            create_option(name='count', description='Number of users to pick', option_type=int, required=True),
            create_option(name='message', description='Message to DM to selected users', option_type=str, required=True)
        ],
        base_permissions=get_mod_permissions(),
        base_default_permission=False,
    )
    async def pick(self, ctx: SlashContext, count: int, *, message: str) -> None:
        """Randomly pick users from the pool and send them a DM.

        If the pool is open, it will be closed automatically.

        If the bot is configured to exclude selected users from future pools, they will be added to the exclusion
        list after they're picked.
        """
        pool = self._pool_for_guild(ctx.guild.id)
        self._picked_users = pool.pick(count)
        log.info(f'Picked users: {self._picked_users}')
        if get_guild_config(ctx.guild.id).game_code.exclude_selected:
            self._previously_selected_users[ctx.guild.id].update(self._picked_users)
        embed = Embed(title=get_guild_config(ctx.guild.id).game_code.message_picked_announce.format(title=self._title),
                      description=f'Total entries: {pool.size}\n'
                                  + '\n'.join(user.display_name for user in self._picked_users),
                      color=Color.blue())
        await self._channel_for_guild(ctx.guild.id).send(embed=embed)
        await self.resend(ctx, message=message)

    @cog_subcommand(
        base=_GROUP_NAME,
        name='resend',
        description='Send a DM to all users picked in the previous pool.',
        guild_ids=get_guilds(),
        options=[
            create_option(name='message', description='Message to DM to selected users', option_type=str, required=True)
        ],
        base_permissions=get_mod_permissions(),
        base_default_permission=False,
    )
    async def resend(self, ctx: SlashContext, *, message: str) -> None:
        for user in self._picked_users[ctx.guild.id]:
            try:
                await user.send(message)
                log.info(f'Sent a DM to {user}.')
            except Forbidden:
                await ctx.reply(f'Failed to DM {user}. Their DM is probably not open. Use the resend command to try '
                                'sending again, or issue another pick command to pick more members.', hidden=True)
                log.warning(f'Failed to DM {user}. DM not open?')
            except HTTPException as e:
                await ctx.reply(f'Failed to DM {user}. You may want to resend the message. {e}', hidden=True)
                log.exception(e)
        await ctx.reply('All done', hidden=True)

    @cog_subcommand(
        base=_GROUP_NAME,
        name='list',
        description='Show a list of all users currently in the pool.',
        guild_ids=get_guilds(),
        base_permissions=get_mod_permissions(),
        base_default_permission=False,
    )
    async def list(self, ctx: SlashContext) -> None:
        await ctx.reply('\n'.join(member.display_name for member in self._pool_for_guild(ctx.guild.id).members),
                        hidden=True)

    @cog_subcommand(
        base=_GROUP_NAME,
        name='clear_pool',
        description='Clear the current pool.',
        guild_ids=get_guilds(),
        base_permissions=get_mod_permissions(),
        base_default_permission=False,
    )
    async def clear_pool(self, ctx: SlashContext) -> None:
        self._pool_for_guild(ctx.guild.id).clear()
        await ctx.reply('All done!', hidden=True)

    @cog_subcommand(
        base=_GROUP_NAME,
        name='clear_selected',
        description='Clear the list of people who were selected before so they become eligible again.',
        guild_ids=get_guilds(),
        base_permissions=get_mod_permissions(),
        base_default_permission=False,
    )
    async def clear_selected(self, ctx: SlashContext) -> None:
        self._previously_selected_users = set()
        await ctx.reply('All done!', hidden=True)

    def _channel_for_guild(self, guild_id: int) -> Optional[TextChannel]:
        channel_id = get_guild_config(guild_id).game_code.player_channel
        return self._bot.get_channel(channel_id)

    def _pool_for_guild(self, guild_id: int) -> MemberPool:
        if guild_id not in self._pools:
            self._pools[guild_id] = MemberPool(guild_id)
        return self._pools[guild_id]
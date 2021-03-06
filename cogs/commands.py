import asyncio
import re
import typing
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from ext.command import command, group
from ext.time import UserFriendlyTime, UserFriendlyTimeOrChannel
from ext.utils import get_perm_level, format_timedelta, in_bot_channel


class MemberOrID(commands.MemberConverter):
    async def convert(self, ctx, argument):
        try:
            result = await super().convert(ctx, argument)
        except commands.BadArgument as e:
            match = self._get_id_match(argument) or re.match(r'<@!?([0-9]+)>$', argument)
            if match:
                result = discord.Object(int(match.group(1)))
            else:
                raise commands.BadArgument(f'Member {argument} not found') from e

        return result


class Commands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_error(self, ctx, error):
        """Handles discord.Forbidden"""
        if isinstance(error, discord.Forbidden):
            await ctx.send(f'I do not have the required permissions needed to run `{ctx.command.name}`.')

    async def send_log(self, ctx, *args):
        guild_config = await ctx.guild_config()
        offset = guild_config.get('time_offset', 0)
        current_time = (ctx.message.created_at + timedelta(hours=offset)).strftime('%H:%M:%S')
        guild_config = {i: int(guild_config.get('modlog', {})[i]) for i in guild_config.get('modlog', {})}

        try:
            if ctx.command.name == 'purge':
                fmt = f'`{current_time}` {ctx.author} purged {args[0]} messages in **#{ctx.channel.name}**'
                if args[1]:
                    fmt += f', from {args[1]}'
                await ctx.bot.get_channel(guild_config.get('message_purge')).send(fmt)
            elif ctx.command.name == 'kick':
                fmt = f'`{current_time}` {ctx.author} kicked {args[0]} ({args[0].id}), reason: {args[1]}'
                await ctx.bot.get_channel(guild_config.get('member_kick')).send(fmt)
            elif ctx.command.name == 'softban':
                fmt = f'`{current_time}` {ctx.author} softbanned {args[0]} ({args[0].id}), reason: {args[1]}'
                await ctx.bot.get_channel(guild_config.get('member_softban')).send(fmt)
            elif ctx.command.name == 'ban':
                name = getattr(args[0], 'name', '(no name)')
                fmt = f'`{current_time}` {ctx.author} banned {name} ({args[0].id}), reason: {args[1]}'
                await ctx.bot.get_channel(guild_config.get('member_ban')).send(fmt)
            elif ctx.command.name == 'unban':
                name = getattr(args[0], 'name', '(no name)')
                fmt = f'`{current_time}` {ctx.author} unbanned {name} ({args[0].id}), reason: {args[1]}'
                await ctx.bot.get_channel(guild_config.get('member_unban')).send(fmt)
            elif ctx.command.qualified_name == 'warn add':
                fmt = f'`{current_time}` {ctx.author} warned {args[0]} ({args[0].id}), reason: {args[1]}'
                await ctx.bot.get_channel(guild_config.get('member_warn')).send(fmt)
            elif ctx.command.qualified_name == 'warn remove':
                fmt = f'`{current_time}` {ctx.author} has deleted warn #{args[0]}'
                await ctx.bot.get_channel(guild_config.get('member_warn')).send(fmt)
            elif ctx.command.name == 'lockdown':
                fmt = f'`{current_time}` {ctx.author} has {"enabled" if args[0] else "disabled"} lockdown for {args[1].mention}'
                await ctx.bot.get_channel(guild_config.get('channel_lockdown')).send(fmt)
            elif ctx.command.name == 'slowmode':
                fmt = f'`{current_time}` {ctx.author} has enabled slowmode for {args[0].mention} for {args[1]}'
                await ctx.bot.get_channel(guild_config.get('channel_slowmode')).send(fmt)

            else:
                raise NotImplementedError(f'{ctx.command.name} not implemented for commands/send_log')
        except AttributeError:
            # channel not found [None.send()]
            pass

    @command(5)
    async def user(self, ctx, member: discord.Member):
        """Get a user's info"""
        async def timestamp(created):
            delta = format_timedelta(ctx.message.created_at - created)
            offset = (await ctx.guild_config()).get('time_offset', 0)
            created += timedelta(hours=offset)

            return f"{delta} ago ({created.strftime('%H:%M:%S')})"

        created = await timestamp(member.created_at)
        joined = await timestamp(member.joined_at)
        member_info = f'**Joined** {joined}\n'

        for n, i in enumerate(reversed(member.roles)):
            if i != ctx.guild.default_role:
                if n == 0:
                    member_info += '**Roles**: '
                member_info += i.name
                if n != len(member.roles) - 2:
                    member_info += ', '
                else:
                    member_info += '\n'

        em = discord.Embed(color=member.color)
        em.set_author(name=member, icon_url=member.avatar_url)
        em.add_field(name='Basic Information', value=f'**ID**: {member.id}\n**Nickname**: {member.nick}\n**Mention**: {member.mention}\n**Created** {created}', inline=False)
        em.add_field(name='Member Information', value=member_info, inline=False)
        await ctx.send(embed=em)

    @group(6, invoke_without_command=True)
    async def note(self, ctx):
        """Manage notes"""
        await ctx.invoke(self.bot.get_command('help'), command_or_cog='note')

    @note.command(6)
    async def add(self, ctx, member: MemberOrID, *, note):
        """Add a note"""
        if get_perm_level(member, await ctx.guild_config())[0] >= get_perm_level(ctx.author, await ctx.guild_config())[0]:
            await ctx.send('User has insufficient permissions')
        else:
            notes = await self.bot.mongo.rainbot.guilds.find_one({'guild_id': str(ctx.guild.id)}) or {}
            guild_notes = notes.get('notes', [])
            notes = list(filter(lambda w: w['member_id'] == str(member.id), guild_notes))

            offset = (await ctx.guild_config()).get('time_offset', 0)
            current_date = (ctx.message.created_at + timedelta(hours=offset)).strftime('%Y-%m-%d')
            if len(guild_notes) == 0:
                case_number = 1
            else:
                case_number = guild_notes[-1]['case_number'] + 1

            push = {
                'case_number': case_number,
                'date': current_date,
                'member_id': str(member.id),
                'moderator_id': str(ctx.author.id),
                'note': note
            }
            await self.bot.mongo.rainbot.guilds.find_one_and_update({'guild_id': str(ctx.guild.id)}, {'$push': {'notes': push}}, upsert=True)
            await ctx.send(self.bot.accept)

    @note.command(6, aliases=['delete', 'del'])
    async def remove(self, ctx, case_number: int):
        """Remove a note"""
        notes = await self.bot.mongo.rainbot.guilds.find_one({'guild_id': str(ctx.guild.id)}) or {}
        notes = notes.get('notes', [])
        note = list(filter(lambda w: w['case_number'] == case_number, notes))
        if len(note) == 0:
            await ctx.send(f'Note #{case_number} does not exist.')
        else:
            await self.bot.mongo.rainbot.guilds.find_one_and_update({'guild_id': str(ctx.guild.id)}, {'$pull': {'notes': note[0]}})
            await ctx.send(self.bot.accept)

    @note.command(6, name='list', aliases=['view'])
    async def _list(self, ctx, member: MemberOrID):
        """View the notes of a user"""
        notes = await self.bot.mongo.rainbot.guilds.find_one({'guild_id': str(ctx.guild.id)}) or {}
        notes = notes.get('notes', [])
        notes = list(filter(lambda w: w['member_id'] == str(member.id), notes))
        name = getattr(member, 'name', str(member.id))
        if name != str(member.id):
            name += f'#{member.discriminator}'

        if len(notes) == 0:
            await ctx.send(f'{name} has no notes.')
        else:
            fmt = f'**{name} has {len(notes)} notes.**'
            for note in notes:
                moderator = ctx.guild.get_member(int(note['moderator_id']))
                fmt += f"\n`{note['date']}` Note #{note['case_number']}: {moderator} noted {note['note']}"

            await ctx.send(fmt)

    @group(6, invoke_without_command=True)
    async def warn(self, ctx):
        """Manage warns"""
        await ctx.invoke(self.bot.get_command('help'), command_or_cog='warn')

    @warn.command(6, name='add')
    async def add_(self, ctx, member: MemberOrID, *, reason):
        """Warn a user"""
        if get_perm_level(member, await ctx.guild_config())[0] >= get_perm_level(ctx.author, await ctx.guild_config())[0]:
            await ctx.send('User has insufficient permissions')
        else:
            warns = await self.bot.mongo.rainbot.guilds.find_one({'guild_id': str(ctx.guild.id)}) or {}
            guild_warns = warns.get('warns', [])
            warns = list(filter(lambda w: w['member_id'] == str(member.id), guild_warns))
            try:
                num_warns = len(warns) + 1
                fmt = f'You have been warned in **{ctx.guild.name}**, reason: {reason}. This is warning #{num_warns}.'
                if num_warns < 5:
                    fmt += ' On your 3rd warning, you will be kicked.'
                else:
                    fmt += ' You have been kicked from the server.'
                await member.send(fmt)
            except discord.Forbidden:
                if ctx.author != ctx.guild.me:
                    await ctx.send('The user has PMs disabled or blocked the bot.')
            finally:
                offset = (await ctx.guild_config()).get('time_offset', 0)
                current_date = (ctx.message.created_at + timedelta(hours=offset)).strftime('%Y-%m-%d')
                if len(guild_warns) == 0:
                    case_number = 1
                else:
                    case_number = guild_warns[-1]['case_number'] + 1
                push = {
                    'case_number': case_number,
                    'date': current_date,
                    'member_id': str(member.id),
                    'moderator_id': str(ctx.author.id),
                    'reason': reason
                }
                await self.bot.mongo.rainbot.guilds.find_one_and_update({'guild_id': str(ctx.guild.id)}, {'$push': {'warns': push}}, upsert=True)
                if ctx.author != ctx.guild.me:
                    await ctx.send(self.bot.accept)
                await self.send_log(ctx, member, reason)

                if num_warns >= 3:
                    ctx.command = self.kick
                    await ctx.invoke(self.kick, member, reason=reason)

    @warn.command(6, name='remove', aliases=['delete', 'del'])
    async def remove_(self, ctx, case_number: int):
        """Remove a warn"""
        warns = await self.bot.mongo.rainbot.guilds.find_one({'guild_id': str(ctx.guild.id)}) or {}
        warns = warns.get('warns', [])
        warn = list(filter(lambda w: w['case_number'] == case_number, warns))
        if len(warn) == 0:
            await ctx.send(f'Warn #{case_number} does not exist.')
        else:
            await self.bot.mongo.rainbot.guilds.find_one_and_update({'guild_id': str(ctx.guild.id)}, {'$pull': {'warns': warn[0]}})
            await ctx.send(self.bot.accept)
            await self.send_log(ctx, case_number)

    @warn.command(6, name='list', aliases=['view'])
    async def list_(self, ctx, member: MemberOrID):
        """View the warns of a user"""
        warns = await self.bot.mongo.rainbot.guilds.find_one({'guild_id': str(ctx.guild.id)}) or {}
        warns = warns.get('warns', [])
        warns = list(filter(lambda w: w['member_id'] == str(member.id), warns))
        name = getattr(member, 'name', str(member.id))
        if name != str(member.id):
            name += f'#{member.discriminator}'

        if len(warns) == 0:
            await ctx.send(f'{name} has no warns.')
        else:
            fmt = f'**{name} has {len(warns)} warns.**'
            for warn in warns:
                moderator = ctx.guild.get_member(int(warn['moderator_id']))
                fmt += f"\n`{warn['date']}` Warn #{warn['case_number']}: {moderator} warned {name} for {warn['reason']}"

            await ctx.send(fmt)

    @command(6, usage='<member> <duration> <reason>')
    async def mute(self, ctx, member: discord.Member, *, time: UserFriendlyTime(default='No reason', assume_reason=True)=None):
        """Mutes a user"""
        if get_perm_level(member, await ctx.guild_config())[0] >= get_perm_level(ctx.author, await ctx.guild_config())[0]:
            await ctx.send('User has insufficient permissions')
        else:
            duration = None
            reason = None
            if time.dt:
                duration = time.dt - ctx.message.created_at
            if time.arg:
                reason = time.arg
            await self.bot.mute(member, duration, reason=reason)
            await ctx.send(self.bot.accept)

    @command(6)
    async def unmute(self, ctx, member: discord.Member, *, reason='No reason'):
        """Unmutes a user"""
        if get_perm_level(member, await ctx.guild_config())[0] >= get_perm_level(ctx.author, await ctx.guild_config())[0]:
            await ctx.send('User has insufficient permissions')
        else:
            await self.bot.unmute(ctx.guild.id, member.id, None, reason=reason)
            await ctx.send(self.bot.accept)

    @command(6, aliases=['clean', 'prune'])
    async def purge(self, ctx, limit: int, *, member: MemberOrID=None):
        """Deletes messages in bulk"""
        count = min(2000, limit)
        await ctx.message.delete()

        retries = 0
        if member:
            while count > 0:
                retries += 1
                last_message = -1
                previous = None
                async for m in ctx.channel.history(limit=50):
                    if m.author.id == member.id:
                        last_message = previous
                        break
                    previous = m.id

                if last_message != -1:
                    try:
                        deleted = await ctx.channel.purge(limit=count, check=lambda m: m.author.id == member.id, before=discord.Object(last_message))
                    except discord.NotFound:
                        pass
                    else:
                        count -= len(deleted)
                else:
                    break

                if retries > 20:
                    break
        else:
            deleted = await ctx.channel.purge(limit=count)
            count -= len(deleted)

        await ctx.send(f'Deleted {limit - count} messages', delete_after=3)
        await self.send_log(ctx, limit - count, member)

    @command(6)
    async def lockdown(self, ctx, channel: discord.TextChannel=None):
        channel = channel or ctx.channel
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)

        if overwrite.send_messages is None or overwrite.send_messages:
            overwrite.send_messages = False
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            await ctx.send(f'Lockdown {self.bot.accept}')
            enable = True
        else:
            # dont change to "not overwrite.send_messages"
            overwrite.send_messages = None
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            await ctx.send(f'Un-lockdown {self.bot.accept}')
            enable = False

        await self.send_log(ctx, enable, channel)

    @command(6, usage='[duration] [channel]')
    async def slowmode(self, ctx, *, time: UserFriendlyTime(converter=commands.TextChannelConverter, default=False, assume_reason=True)):
        """Enables slowmode, max 6h

        Examples:
        !!slowmode 2h
        !!slowmode 2h #general
        !!slowmode off
        !!slowmode 0s #general
        """
        duration = timedelta()
        channel = ctx.channel
        if time.dt:
            duration = time.dt - ctx.message.created_at
        if time.arg:
            if isinstance(time.arg, str):
                try:
                    channel = await commands.TextChannelConverter().convert(ctx, time.arg)
                except commands.BadArgument:
                    if time.arg != 'off':
                        raise
            else:
                channel = time.arg

        seconds = int(duration.total_seconds())

        if seconds > 21600:
            await ctx.send('Slowmode only supports up to 6h max at the moment')
        else:
            fmt = format_timedelta(duration, assume_forever=False)
            await channel.edit(slowmode_delay=int(duration.total_seconds()))
            await self.send_log(ctx, channel, fmt)
            if duration.total_seconds():
                await ctx.send(f'Enabled `{fmt}` slowmode on {channel.mention}')
            else:
                await ctx.send(f'Disabled slowmode on {channel.mention}')

    @command(7)
    async def kick(self, ctx, member: discord.Member, *, reason=None):
        """Kicks a user"""
        if get_perm_level(member, await ctx.guild_config())[0] >= get_perm_level(ctx.author, await ctx.guild_config())[0]:
            await ctx.send('User has insufficient permissions')
        else:
            await member.kick(reason=reason)
            if ctx.author != ctx.guild.me:
                await ctx.send(self.bot.accept)
            await self.send_log(ctx, member, reason)

    @command(7)
    async def softban(self, ctx, member: discord.Member, *, reason=None):
        """Swings the banhammer"""
        if get_perm_level(member, await ctx.guild_config())[0] >= get_perm_level(ctx.author, await ctx.guild_config())[0]:
            await ctx.send('User has insufficient permissions')
        else:
            await member.ban(reason=reason)
            await asyncio.sleep(2)
            await member.unban(reason=reason)
            await ctx.send(self.bot.accept)
            await self.send_log(ctx, member, reason)

    @command(7)
    async def ban(self, ctx, member: MemberOrID, *, reason=None):
        """Swings the banhammer"""
        if get_perm_level(member, await ctx.guild_config())[0] >= get_perm_level(ctx.author, await ctx.guild_config())[0]:
            await ctx.send('User has insufficient permissions')
        else:
            await ctx.guild.ban(member, reason=reason)
            await ctx.send(self.bot.accept)
            await self.send_log(ctx, member, reason)

    @command(7)
    async def unban(self, ctx, member: MemberOrID, *, reason=None):
        """Unswing the banhammer"""
        await ctx.guild.unban(member, reason=reason)
        await ctx.send(self.bot.accept)
        await self.send_log(ctx, member, reason)


def setup(bot):
    bot.add_cog(Commands(bot))

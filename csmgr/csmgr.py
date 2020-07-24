from redbot.core import commands, checks, Config
from redbot.core.bot import Red
import aiohttp
import asyncio
from urllib.parse import urlparse
import discord
from .gql import *

from .checks import is_cog_support_server, is_core_dev_or_qa, is_senior_cog_creator
from .discord_ids import (
    COG_CREATOR_ROLE_ID,
    COG_SUPPORT_SERVER_ID,
    OTHERCOGS_ID,
    SENIOR_COG_CREATOR_ROLE_ID,
    V3_COG_SUPPORT_CATEGORY_ID,
)

GH_API = "https://api.github.com/graphql"


class CSMgr(commands.Cog):
    """
    Cog support server manager
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.db = Config.get_conf(self, identifier=59595922, force_registration=True)
        default_member = {"repos": {}}
        default_global = {"repos": {}, "token": ""}
        self.db.register_member(**default_member)
        self.db.register_global(**default_global)
        self.session = aiohttp.ClientSession()

    def __unload(self):
        if not self.session.closed:
            fut = asyncio.ensure_future(self.session.close())
            yield from fut.__await__()

    async def do_request(self, data: dict) -> dict:
        token = await self.db.token()
        async with self.session.post(
            GH_API, json=data, headers={"Authorization": "Bearer {}".format(token)}
        ) as r:
            resp = await r.json()
            return resp

    @commands.command()
    @is_cog_support_server()
    @is_core_dev_or_qa()
    async def addcreator(self, ctx: commands.Context, member: discord.Member, url: str):
        """
        Register a new cog creator

        `url` should be a link to the repository
        """
        if await self.db.member(member).repos():
            await ctx.send("That user has already been marked as a cog creator")
            return
        service, username, repository = self.parse_url(url)

        repo_data = {
            "service": service,
            "username": username,
            "repository": repository,
            "creator_level": "cog creator",
            "channel": 0,
            "news_channel": 0,
            "news_role": 0,
        }

        for c in ctx.guild.text_channels:
            if repository in c.name:
                repo_data["channel"] = c.id
        if service.lower() == "github":
            data = await self.do_request(
                {
                    "query": USER_REPO_EXIST_QUERY,
                    "variables": {"owner": username, "repository": repository},
                }
            )
            if data["data"]["repository"] is None and "errors" in data:
                for error in data["errors"]:
                    if "type" in error and error["type"] == "NOT_FOUND":
                        await ctx.send(error["message"])
                        return

        await self.db.member(member).repos.set_raw(repository, value=repo_data)
        await member.add_roles(ctx.guild.get_role(COG_CREATOR_ROLE_ID))
        await ctx.send(f"Done. {member.mention} is now a cog creator!")

    @commands.command()
    @is_cog_support_server()
    @is_core_dev_or_qa()
    async def grantsupport(self, ctx: commands.Context, member: discord.Member, repo: str):
        """
        Grants this user a support channel. Must already be a cog creator
        """
        try:
            cid = await self.db.member(member).repos.get_raw(repo, "channel")
        except KeyError:
            await ctx.send("That repo has not been registered yet!")
            return
        if cid != 0 and ctx.guild.get_channel(cid):
            await ctx.send("It appears a channel already exists for that repo!")
            return
        chan_name = "support_" + repo.lower()
        chan = None
        for channel in ctx.guild.text_channels:
            if channel.name == chan_name:
                chan = channel
                break
        cat = ctx.guild.get_channel(V3_COG_SUPPORT_CATEGORY_ID)
        if chan:
            await chan.edit(category=cat, reason="Moving channel to V3 support category")
            await ctx.send("Existing channel moved to the V3 support category.")
        else:
            chan = await self.add_textchannel(chan_name, ctx.guild, member, cat)
            await ctx.send(chan.mention + " has been created!")
        await self.db.member(member).repos.set_raw(repo, "channel", value=chan.id)

    @commands.command()
    @is_cog_support_server()
    @is_core_dev_or_qa()
    async def makesenior(self, ctx: commands.Context, member: discord.Member, repo: str):
        """
        Makes this user a senior cog creator
        """
        try:
            cid = await self.db.member(member).repos.get_raw(repo, "channel")
        except KeyError:
            await ctx.send("That repo isn't registered yet!")
        if cid == 0:
            await self.add_textchannel(
                "support_" + repo,
                ctx.guild,
                member,
                ctx.guild.get_channel(V3_COG_SUPPORT_CATEGORY_ID),
            )
        await self.db.member(member).repos.set_raw(
            repo, "creator_level", value="senior cog creator"
        )
        await member.add_roles(ctx.guild.get_role(SENIOR_COG_CREATOR_ROLE_ID))

    @commands.command()
    @is_cog_support_server()
    @is_senior_cog_creator()
    async def makeannouncement(
        self, ctx: commands.Context, repo: str, mention_users: bool = False, *, message: str
    ):
        """
        Make an announcement in your repo's news channel.

        repo needs to be the name of your repo

        mention_users, if set to True, will mention everyone when making the announcement
        """
        pass

    @commands.command()
    @is_cog_support_server()
    @is_core_dev_or_qa()
    async def makechannellist(self, ctx: commands.Context):
        """
        Make a list of all support channels
        """
        members = await self.db.all_members(ctx.guild)
        for m in members:
            repos = members[m]["repos"]
            owner = ctx.guild.get_member(m)
            if not owner:
                continue  # member not in server anymore
            for r in repos:
                repo = repos[r]

                support_channel = ctx.guild.get_channel(repo["channel"])
                repo_name = repo["repository"]
                if repo["service"] == "github":
                    url = "/".join(["https://github.com", repo["username"], repo_name])
                elif repo["service"] == "gitlab":
                    url = "/".join(["https://gitlab.com", repo["username"], repo_name])
                elif repo["service"] == "bitbucket":
                    url = "/".join(["https://bitbucket.org", repo["username"], repo_name])
                else:
                    url = "Unknown"
                embed = discord.Embed(title=repo_name)
                if url != "Unknown":
                    embed.url = url
                else:
                    embed.add_field(name="URL", value=url, inline=False)
                embed.set_author(
                    name=f"{owner.name} - {repo['creator_level']}", icon_url=owner.avatar_url
                )
                if isinstance(support_channel, discord.TextChannel):
                    embed.add_field(
                        name="Support channel", value=support_channel.mention, inline=False
                    )
                else:
                    embed.add_field(
                        name="Support channel",
                        value=ctx.guild.get_channel(OTHERCOGS_ID).mention,
                        inline=False,
                    )
                await ctx.send(embed=embed)
        await ctx.message.delete()

    @commands.command()
    @checks.is_owner()
    async def setghtoken(self, ctx: commands.Context, token: str):
        async with self.session.post(
            GH_API, json={"query": TOKEN_TEST_QUERY}, headers={"Authorization": "Bearer " + token}
        ) as r:
            if r.status != 200:
                await ctx.send("Something is wrong with that token. Please try again")
                return
        await self.db.token.set(token)
        await ctx.send("Token set successfully")

    async def add_textchannel(
        self,
        name: str,
        guild: discord.Guild,
        owner: discord.Member,
        category: discord.CategoryChannel,
    ):
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                external_emojis=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            owner: discord.PermissionOverwrite(
                manage_messages=True, manage_roles=True, manage_webhooks=True, manage_channels=True
            ),
        }

        return await guild.create_text_channel(
            name,
            overwrites=overwrites,
            category=category,
            reason="Adding a V3 cog support channel for " + owner.name,
        )

    def parse_url(self, url: str):
        parsed = urlparse(url)

        service = parsed.netloc.split("www.")[-1].split(".")[0]

        repo = parsed.path.split("/", maxsplit=1)[-1]

        username, repository = repo.split("/")

        return service, username, repository
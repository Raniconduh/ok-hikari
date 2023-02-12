import re
import os
import hikari
import asyncio
import requests
import urllib.parse
from functools import wraps
from datetime import datetime
from translatepy import Translator
from scraper import Scraper as WktScraper

translator = Translator()
wkt_scraper = WktScraper("en")

bot = hikari.GatewayBot(intents=hikari.Intents.GUILD_MESSAGES
                                | hikari.Intents.MESSAGE_CONTENT,
                        token=os.getenv("DISCORD_TOKEN"), logs="ERROR")


class TxtCommand:
    commands = {}
    aliases = {}

    def __init__(self, name=None, aliases=None):
        self.name = name
        self.aliases = aliases

    def __call__(self, func):
        self.func = func
        if not self.name: self.name = func.__name__
        self.desc = func.__doc__

        TxtCommand.update_aliases(self.aliases, self.name)
        TxtCommand.update_commands(self.name, self)

        @wraps(func)
        def wrappee(*args, **kwargs):
            func(*args,**kwargs)
        return wrappee

    def update_aliases(aliases, to):
        if not aliases: return

        for alias in aliases:
            if alias in TxtCommand.aliases: return
            TxtCommand.aliases[alias] = to

    def update_commands(name, com):
        if name in TxtCommand.commands: return
        TxtCommand.commands[name] = com


class Definition:
    def __init__(self, pos, definition, info):
        self.part_of_speech = pos
        self.definition = definition
        self.info = info


class Summary:
    def __init__(self, source, text, related=None, thumb=None, image=None):
        self.source = source
        self.text = text
        self.related = related
        self.thumb = thumb
        self.image = image


def query_translator(text):
    return translator.translate(text, "english").result


def query_definition(word):
    resp = None
    try:
        resp = wkt_scraper.scrape(word)
    except FileNotFoundError:
        return None

    phonetic = ""
    p = resp.get("pronunciation", None)
    if p and len(p) > 0:
        phonetic = p[0]['values'][0]

    ret = {"phonetic": phonetic, "definitions": []}

    for meaning in resp["meanings"]:
        pos = meaning.get("part_of_speech", "")
        for val in meaning["values"]:
            # try to ignore entries that start with a year or range of years
            txt = val["text"].split('\n')[0].strip()
            if re.match(r"^([Cc]\.\s)?[0-9]{4}([-\u2010-\u2015][0-9]{4})?,?\s", txt): continue

            info = ""
            if r := re.match(r'^\([^)]+\) ', txt):
                info = r.group().strip()
                txt = txt[r.span()[1]:]
            if not txt: continue
            ret["definitions"].append(Definition(pos, txt, info))

    return ret


def query_summary(text):
    params = {
        "q": text,
        "format": "json"
    }

    resp = requests.get(f"https://api.duckduckgo.com/?{urllib.parse.urlencode(params)}")
    resp = resp.json()

    source = resp["AbstractSource"]
    link = resp["AbstractURL"]
    text = resp["AbstractText"]
    related = resp["RelatedTopics"]
    image = resp["Image"]
    thumb = None

    new_rel = []

    if not text and not len(related):
        return None

    if text:
        r = re.match(r'https?://[^/]+/?', link)
        host = r.group()
        path = urllib.parse.quote(link[r.span()[1]:])
        link = host + path

        text = f"{text[:4094 - len(link)]}\n\n{link}"
    elif len(related):
        new_rel = []
        c = 0
        for rel in related:
            if 'FirstURL' in rel:
                new_rel.append(rel)
                c += 1
            if c >= 5: break

        for x in range(len(new_rel)):
            rlink = new_rel[x]["FirstURL"]
            rtext = new_rel[x]["Text"]

            # remove the part of the path from the text
            ritem = re.match(r'https?://[^/]+/(.+)', rlink).group(1).replace('_', ' ')
            rtext = rtext[len(ritem) + 1:]

            new_rel[x] = {"item": ritem, "link": rlink, "text": rtext}

    if image:
        image = f'https://duckduckgo.com/{image}'
        if resp["ImageIsLogo"]:
            thumb = image
            image = None

    return Summary(source=source, text=text, related=new_rel,
                   image=image, thumb=thumb)


@TxtCommand()
async def ping(event, dat):
    """Pong!"""

    latency = datetime.now().timestamp() - event.message.timestamp.timestamp()
    await event.message.respond(f"pong {latency * 1000:.1f}ms", reply=True)


@TxtCommand(name="help")
async def c_help(event, dat):
    """Show command help or list all commands"""

    if dat:
        if dat in TxtCommand.aliases:
            dat = TxtCommand.aliases[dat]

        if dat in TxtCommand.commands:
            await event.message.respond(TxtCommand.commands[dat].desc, reply=True)
        else:
            await event.message.respond(f'No command {dat}', reply=True)
    else:
        embed = hikari.embeds.Embed(title="Help")
        embed.description = (
                "Commands are prefixed with an exclamation mark `!`"
                " but they can also be run by pinging first and then"
                " writing the command. E.g. ```\n!translate Hola mundo\n``` or"
                " ```\n@ok translate Hola mundo\n```"
                )
        for com in TxtCommand.commands:
            com = TxtCommand.commands[com]
            txt = f"{com.name}"
            for alias in TxtCommand.aliases:
                if TxtCommand.aliases[alias] == com.name:
                    txt += f" | {alias}"
            embed.add_field(txt, com.desc)

        await event.message.respond(embed=embed, reply=True)


@TxtCommand(aliases=["t"])
async def translate(event, dat):
    """Translate text, replied message, or latest message"""

    if not dat:
        dat = ""
        if event.message.type == hikari.MessageType.REPLY:
            repl = event.message.referenced_message
            dat = repl.content
            l = []
            for embed in repl.embeds:
                l.append(embed.title or "")
                l.append(embed.description or "")
                for field in embed.fields:
                    t = f'{field.name or ""} {field.value or ""}'
                    l.append(t.strip())
                l.append(embed.footer or "")
            if l:
                if dat: dat += '\n'
                dat += '\n'.join(l)
        else:
            chan = await event.message.fetch_channel()
            async for m in chan.fetch_history(before=event.message):
                dat = m.content
                break

    dat = re.sub("<@[!#$%^&*]?([0-9]+)>", "@-", dat)
    dat = dat.strip()

    loop = asyncio.get_running_loop()
    t = await loop.run_in_executor(None, query_translator, dat)
    await event.message.respond(t, reply=True)


@TxtCommand(aliases=["a"])
async def avatar(event, dat):
    """Fetch a user's avatar"""

    user = None

    if not dat:
        if event.message.type == hikari.MessageType.REPLY:
            user = event.message.referenced_message.author
        else:
            user = event.message.author
    else: # assume we are given a user ID
        if r := re.match("<@[!#$%^&*]?([0-9]+)>", dat):
            dat = r.group(1)
        try:
            dat = int(dat)
        except ValueError:
            await event.message.respond("Invalid user", reply=True)
            return
        user = bot.cache.get_user(dat) or await bot.rest.fetch_user(dat)

    if not user:
        await event.message.respond("Could not find user", reply=True)
        return

    e = hikari.embeds.Embed(title=f"Avatar for {user.username}#{user.discriminator}")
    e.set_image(user.avatar_url or user.default_avatar_url)
    await event.message.respond(embed=e, reply=True)


@TxtCommand(aliases=["d"])
async def define(event, dat):
    """Define a word or phrase"""

    if not dat:
        await event.message.respond("Nothing to define", reply=True)
        return

    loop = asyncio.get_running_loop()
    defs = await loop.run_in_executor(None, query_definition, dat)
    if not defs or not len(defs["definitions"]):
        await event.message.respond("Could not get definition", reply=True)
        return

    phon = ""
    if defs["phonetic"]: phon = f" *{defs['phonetic']}*"
    embed = hikari.embeds.Embed(title=f"{dat}{phon}")

    c = 1
    for d in defs["definitions"]:
        embed.add_field(f"{c}. {d.part_of_speech} {d.info}", d.definition)
        c += 1

    await event.message.respond(embed=embed, reply=True)


@TxtCommand(aliases=["s"])
async def summarize(event, dat):
    """Summarize a term or phrase or find related info"""

    if not dat:
        await event.message.respond("Nothing to summarize", reply=True)

    loop = asyncio.get_running_loop()
    summ = await loop.run_in_executor(None, query_summary, dat)

    if not summ:
        await event.message.respond("Could not get summary", reply=True)
        return

    embed = None
    if summ.text:
        embed = hikari.embeds.Embed(title=f"Summary from {summ.source}")
        embed.description = summ.text
    else:
        embed = hikari.embeds.Embed(title=f"Related topics")
        for topic in summ.related:
            embed.add_field(topic['item'], f"[{topic['text']}]({topic['link']})")

    if summ.thumb:
        embed.set_thumbnail(summ.thumb)
    elif summ.image:
        embed.set_image(summ.image)

    await event.message.respond(embed=embed, reply=True)


@bot.listen()
async def on_message(event: hikari.MessageCreateEvent) -> None:
    if not event.message or not event.message.content: return

    msg = event.message.content.partition(' ')
    msg = [msg[0], msg[2]]
    for embed in event.message.embeds:
        msg.append(embed.description)
        for field in embed.fields:
            msg.append(f'{field.name} {field.value}')
    msg = [msg[0], '\n'.join(msg[1:])]

    cmd = None
    dat = None

    # check if command or starts with ping
    me = bot.get_me()
    if msg[0].startswith('!'):
        cmd = msg[0][1:]
        dat = ' '.join(msg[1:])
    elif msg[0] == f'<@{me.id}>':
        msg = msg[1].partition(' ')
        cmd = msg[0]
        dat = msg[2]
        if not cmd:
            await TxtCommand.commands["help"].func(event, dat)
    else:
        return

    if cmd and cmd in TxtCommand.aliases:
        cmd = TxtCommand.aliases[cmd]

    if cmd and cmd in TxtCommand.commands:
        print(f"Command {cmd} from {event.message.author}")
        async with bot.rest.trigger_typing(event.channel_id):
            await TxtCommand.commands[cmd].func(event, dat)


bot.run()

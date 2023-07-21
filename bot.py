import re
import os
import hikari
import asyncio
import requests
import unicodedata
import translatepy
import urllib.parse
from functools import wraps
from datetime import datetime
from translatepy import Translator
from scraper import Scraper as WktScraper


BOT_PREFIX = '!'


translator = Translator()

bot = hikari.GatewayBot(intents=hikari.Intents.GUILD_MESSAGES
                                | hikari.Intents.MESSAGE_CONTENT
                                | hikari.Intents.ALL_UNPRIVILEGED,
                        token=os.getenv("DISCORD_TOKEN"))

class TxtCommand:
    commands = {}
    aliases = {}

    def __init__(self, name=None, aliases=None, flags=[], arguments=""):
        self.name = name
        self.aliases = aliases
        self.flags = flags
        self.arguments = arguments

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


class Flag:
    def __init__(self, flag, arg=None):
        self.flag = flag
        self.arg = arg


class Convert:
    STATIC = 0
    DYNAMIC = 1
    categories = {
        "length": {
            "CTYPE": STATIC,
            "m": 1.0,
            "km": 1000.0,
            "cm": 1/100.0,
            "mm": 1/1000.0,
            "um": 1e-6,
            "nm": 1e-9,
            "pm": 1e-12,
            "ft": 1/3.2808399,
            "yd": 1/1.0936133,
            "mi": 1609.344,
            "in": 0.0254,
        },
        "temperature": {
            "CTYPE": DYNAMIC,
            "c":   lambda c: c,
            "c_C": lambda c: c,
            "k":   lambda k: k - 273.15,
            "k_C": lambda c: c + 273.15,
            "f":   lambda f: (f - 32) * 5./9.,
            "f_C": lambda c: c * 1.8 + 32,
        },
        "data": {
            "CTYPE": STATIC,
            "b": 1.0,
            "kb": 1000,
            "kib": 1024,
            "mb": 1e6,
            "mib": 1048576,
            "gb": 1e9,
            "gib": 1073741824,
            "tb": 1e12,
            "tib": 1099511627776,
            "pb": 1e15,
            "pib": 1125899906842624,
            "eb": 1e18,
            "eib": 1152921504606846976,
            "zb": 1e21,
            "zib": 1180591620717411303424,
            "yb": 1e24,
            "yib": 1208925819614629174706176,
        },
        "weight": {
            "CTYPE": STATIC,
            "kg": 1,
            "g": 1/1000.0,
            "mg": 1e-6,
            "ug": 1e-9,
            "ton": 907.18474,
            "tonne": 1000,
            "lbs": 0.45359237,
            "lb": 0.45359237,
            "oz": 0.02834952,
        },
        "volume": {
            "CTYPE": STATIC,
            "ml": 1.0,
            "l": 1000.0,
            "oz": 29.5735295,
            "qt": 946.352945,
            "gal": 3785.41178,
            "tbsp": 14.7867648,
            "tsp": 4.92892159,
            "cup": 236.588236,
            "pint": 473.176473,
        }
    }

    def get_category(a, b):
        for cat in Convert.categories:
            ccat = Convert.categories[cat]
            if a in ccat and b in ccat:
                return cat
        return None

    def convert(a, unita, unitb, cat):
        ccat = Convert.categories[cat]
        if ccat["CTYPE"] == Convert.STATIC:
            return a * ccat[unita] / ccat[unitb]
        else:
            return ccat[unitb + "_C"](ccat[unita](a))


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


class Translation:
    def __init__(self, from_lang, to_lang, res):
        self.from_lang = from_lang
        self.to_lang = to_lang
        self.res = res


def query_translator(text, lang="english", origin="auto"):
    t = translator.translate(text, lang, origin)
    return Translation(from_lang=t.source_language.name,
                       to_lang=t.destination_language.name,
                       res=t.result)


def query_definition(word, lang="en"):
    resp = None
    wkt_scraper = WktScraper(lang)
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


def get_command_info(name):
    if name not in TxtCommand.commands: return None

    com = TxtCommand.commands[name]
    txt = f"{com.name}"
    if com.aliases:
        for alias in com.aliases:
            txt += f" | {alias}"
    for flag in com.flags:
        if flag.arg:
            txt += f" [--{flag.flag}=<{flag.arg}>]"
        else:
            txt += f" [--{flag.flag}]"
    if com.arguments:
        txt += f" {com.arguments}"

    return txt


def get_embeds_text(embeds):
    text = []
    for embed in embeds:
        if embed.title:
            text.append(embed.title)
        if embed.description:
            text.append(embed.description)
        for field in embed.fields:
            text.append(f'{field.name or ""} {field.value or ""}'.strip())
        if embed.footer and embed.footer.text:
            text.append(embed.footer.text)
    return text


def stotime(seconds):
    s = int(seconds) % 60
    m = int(seconds // 60) % 60
    h = int(seconds // 3600)

    if h: return f'{h}:{m:0>2}:{s:0>2}'
    else: return f'{m}:{s:0>2}'


def mstotime(ms):
    return stotime(ms // 1000)


@TxtCommand()
async def ping(event, dat):
    """Pong!"""

    latency = datetime.now().timestamp() - event.message.timestamp.timestamp()
    await event.message.respond(f"pong {latency * 1000:.1f}ms", reply=True)


@TxtCommand(name="help", arguments="[command]")
async def c_help(event, dat):
    """Show command help or list all commands"""

    if dat:
        if dat in TxtCommand.aliases:
            dat = TxtCommand.aliases[dat]

        if dat in TxtCommand.commands:
            com = TxtCommand.commands[dat]
            txt = "!" + get_command_info(dat) + "\n\n" + com.desc
            await event.message.respond(txt, reply=True)
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
            txt = get_command_info(com.name)

            embed.add_field(txt, com.desc)

        await event.message.respond(embed=embed, reply=True)


@TxtCommand(aliases=["t"], flags=[Flag("to", "lang"), Flag("origin", "lang")], arguments="[text]")
async def translate(event, dat, to="english", origin="auto"):
    """Translate text, replied message, or latest message"""

    if not dat:
        dat = ""
        if event.message.type == hikari.MessageType.REPLY:
            repl = event.message.referenced_message
            dat = repl.content or ""
            if l := get_embeds_text(repl.embeds):
                if dat: dat += '\n'
                dat += '\n'.join(l)

            if not dat:
                embed = hikari.embeds.Embed(title="No text", color=0xFF0000)
                await event.message.respond(embed=embed, reply=True)
                return
        else:
            chan = await event.message.fetch_channel()
            async for m in chan.fetch_history(before=event.message):
                dat = m.content or ""
                if l := get_embeds_text(m.embeds):
                    if dat: dat += '\n'
                    dat += '\n'.join(l)
                break

    dat = dat.strip()

    loop = asyncio.get_running_loop()
    try:
        t = await loop.run_in_executor(None, query_translator, dat, to, origin)
    except translatepy.exceptions.UnknownLanguage as e:
        embed = hikari.embeds.Embed(title="Unknown language", color=0xFF0000)
        embed.description = f'Maybe you meant {e.guessed_language}?'
        await event.message.respond(embed=embed, reply=True)
    except translatepy.exceptions.NoResult:
        embed = hikari.embeds.Embed(title="No translation result", color=0xFF0000)
        await event.message.respond(embed=embed, reply=True)
    except Exception:
        embed = hikari.embeds.Embed(title="Translation failed", color=0xFF0000)
        await event.message.respond(embed=embed, reply=True)
    else:
        embed = hikari.embeds.Embed(title="Translation Result")
        embed.description = t.res
        embed.set_footer(f"From {t.from_lang} to {t.to_lang}")
        await event.message.respond(embed=embed, reply=True)


@TxtCommand(aliases=["a"], arguments="[user]")
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


@TxtCommand(aliases=["d"], flags=[Flag("lang", "lang")], arguments="<word>")
async def define(event, dat, lang="en"):
    """Define a word or phrase. Language argument must be the language shorthand (e.g. Spanish -> es)"""

    if not dat:
        await event.message.respond("Nothing to define", reply=True)
        return

    loop = asyncio.get_running_loop()
    try:
        defs = await loop.run_in_executor(None, query_definition, dat, lang)
    except KeyError:
        embed = hikari.embeds.Embed(title="Invalid language", color=0xFF0000)
        await event.message.respond(embed=embed, reply=True)
        return

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


@TxtCommand(aliases=["s"], arguments="<term>")
async def summarize(event, dat):
    """Summarize a term or phrase or find related info"""

    if not dat:
        await event.message.respond("Nothing to summarize", reply=True)
        return

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


@TxtCommand(arguments="<title>, <item>...")
async def poll(event, dat):
    """Create a poll from a comma separated list"""

    dat = dat.split(',')
    title = dat[0].strip()

    if not title:
        await event.message.respond("No poll arguments", reply=True)
        return

    sanitized = []
    for i in range(1, len(dat)):
        d = dat[i].strip()
        if not d:
            continue
        sanitized.append(d)

    if not sanitized:
        await event.message.respond("Not enough poll arguments")
        return
    if len(sanitized) > 20:
        await event.message.respond("Too many poll arguments")
        return

    embed = hikari.embeds.Embed(title=title)
    embed.set_footer(f"Poll by {event.author.username}#{event.author.discriminator}")

    desc = ""
    for i in range(len(sanitized)):
        letter = chr(ord('a') + i)
        desc += f":regional_indicator_{letter}:: {sanitized[i]}\n"
    embed.description = desc

    msg = await event.message.respond(embed=embed)
    for i in range(len(sanitized)):
        letter = chr(ord('A') + i)
        emoji = unicodedata.lookup(f"REGIONAL INDICATOR SYMBOL LETTER {letter}")
        await msg.add_reaction(emoji)


@TxtCommand(aliases=["e"], arguments="<emoji>")
async def emoji(event, dat):
    """Show the image of a given emoji"""
    emoji = None

    if not dat:
        embed = hikari.embeds.Embed(title="No emoji given", color=0xFF0000)
        await event.message.respond(embed=embed, reply=True)
        return

    guild = await bot.rest.fetch_guild(event.guild_id)
    if r := re.match(r'<:[^:]+:([0-9]+)>', dat):
        e_id = r.group(1)
        emoji = bot.cache.get_emoji(e_id) or await guild.fetch_emoji(e_id)
    else:
        emojis = await guild.fetch_emojis()
        for e in emojis:
            if e.name == dat:
                emoji = e
                break

    if emoji is None:
        embed = hikari.embeds.Embed(title="No emoji found", color=0xFF0000)
        await event.message.respond(embed=embed, reply=True)
        return

    embed = hikari.embeds.Embed(title=f"Image for :{emoji.name}:")
    embed.set_image(emoji.url)

    await event.message.respond(embed=embed, reply=True)


@TxtCommand(aliases=["conv", "c"], arguments="<origin> <unit> to <final>")
async def convert(event, dat):
    """Convert `origin` with given unit to `final` unit; i.e. `5m to cm`"""
    dat = dat.split(' ')

    if len(dat) != 4:
        await event.message.respond("An invalid number of arguments has been passed", reply=True)
        return

    if dat[2].lower() != 'to':
        await event.message.respond("Third argument must be the word 'to'", reply=True)
        return

    a = dat[0]
    unita = dat[1].lower()
    b = dat[3].lower()

    try:
        a = float(a)
    except ValueError:
        await event.message.respond("Invalid origin number")
        return

    cat = Convert.get_category(unita, b)
    if cat is None:
        await event.message.respond("Either origin or final unit is unknown or invalid")
        return

    res = Convert.convert(a, unita, b, cat)
    rstr = str(res)
    if set((r := rstr.partition('.'))[2].split()) == {'0'}:
        rstr = r[0]

    embed = hikari.embeds.Embed(title=f"{cat.title()} Conversion")
    embed.description = rstr
    embed.set_footer(f"Conversion from {unita} to {b}")

    await event.message.respond(embed=embed, reply=True)


@bot.listen()
async def on_message(event: hikari.MessageCreateEvent) -> None:
    if not event.message or not event.message.content: return
    me = bot.get_me()
    if event.message.author.id == me.id: return

    # message parsing: return early if the message is clearly not a command
    msg = event.message.content.partition(' ')
    cmd = None
    if msg[0].startswith(BOT_PREFIX):
        cmd = msg[0][len(BOT_PREFIX):]
        msg = msg[2]
    elif msg[0] == f'<@{me.id}>':
        msg = msg[2].partition(' ')

        cmd = msg[0]
        msg = msg[2]

        if not cmd:
            await TxtCommand.commands["help"].func(event, '')
            return
    else:
        return

    if cmd in TxtCommand.aliases:
        cmd = TxtCommand.aliases[cmd]
    if cmd in TxtCommand.commands:
        cmd = TxtCommand.commands[cmd]
    else:
        return

    msg += '\n'.join(get_embeds_text(event.message.embeds))

    # parse flags
    new_msg = []
    flags = {}
    msg = msg.split(' ')
    i = 0
    while i < len(msg) and msg[i].startswith('--'):
        v = msg[i].partition('=')
        flag = v[0][2:]
        val = v[2]

        found = False
        for f in cmd.flags:
            if flag == f.flag:
                flags[flag] = val
                found = True
                break
        if not found:
            new_msg.append(msg[i])

        i += 1

    for d in msg[i:]:
        new_msg.append(d)
    msg = ' '.join(new_msg)

    print(f"Command {cmd.name} from {event.message.author}")
    try:
        async with bot.rest.trigger_typing(event.channel_id):
            await cmd.func(event, msg, **flags)
    except Exception as e:
        embed = hikari.embeds.Embed(title="Command failed", color=0xFF0000)
        await event.message.respond(embed=embed, reply=True)

        raise e


@bot.listen()
async def on_start(event: hikari.StartedEvent):
    print("Started")


bot.run()

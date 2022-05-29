import os
import re
import signal
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
import trio
from cachetools import TTLCache
from IrcBot.bot import IrcBot, Message, utils
from pexpect import replwrap

from conf import (CHANNELS, COQ_REPL_TTL, COQTOP_CMD, NICK, PORT, PREFIX,
                  SERVER, SSL)
from message_server import listen_loop

user_repls = TTLCache(maxsize=5, ttl=COQ_REPL_TTL)
user_history = TTLCache(maxsize=32, ttl=COQ_REPL_TTL)

utils.setHelpHeader(
    "USE: {PREFIX} [coq command here]       - (Notice the space)")
utils.setHelpBottom(
    "Nice tutorial coq at https://learnxinyminutes.com/docs/coq/")
utils.setLogging(10)
utils.setParseOrderTopBottom(True)
utils.setPrefix(PREFIX)

info = utils.log

FIFO = NamedTemporaryFile(mode='w+b', prefix='coq-repl-',
                          suffix='.fifo', delete=False).name
COQ_EXPORT_DIR = "./coq-export/"

# If export dir for coq doesn't exist, create it
if not os.path.exists(COQ_EXPORT_DIR):
    os.makedirs(COQ_EXPORT_DIR)

# Change working directory to the directory of COQ_EXPORT_DIR
os.chdir(COQ_EXPORT_DIR)

def ansi2irc(text):
    """Convert ansi colors to irc colors."""
    text = re.sub(r'\x1b\[([0-9;]+)m', lambda m: '\x03' + m.group(1), text)
    text = re.sub(r'\x1b\[([0-9;]+)[HJK]', lambda m: '\x1b[%s%s' %
                  (m.group(1), m.group(2)), text)
    return text


def reply(msg: Message, text: str):
    """Reply to a message."""
    with open(FIFO, "w") as f:
        for line in text.splitlines():
            if not line.strip():
                continue
            line = f"<{msg.nick}> {ansi2irc(line)}"
            f.write(f"[[{msg.channel}]] {line}\n")


def paste(text):
    """Paste text to ix.io."""
    info(f"Pasting {text=}")
    try:
        url = "http://ix.io"
        payload = {'f:1=<-': text}
        response = requests.request("POST", url, data=payload)
        return response.text
    except Exception as e:
        info(f"Error {e=}")
        return "Failed to paste"


def read_paste(url):
    """Read text from ix.io."""
    response = requests.request("GET", url)
    return response.text


def run_command(msg: Message, text: str):

    def _run_command(msg: Message, text: str):
        def __run_command(msg: Message, text: str):
            global user_repls, user_history
            user = msg.nick
            if user not in user_repls:
                info(f"Creating new repl for {user}")
                user_repls[user] = replwrap.REPLWrapper(
                    COQTOP_CMD, "Coq <", prompt_change=None)
            reply(msg, user_repls[user].run_command(text, timeout=2))

            if user not in user_history:
                user_history[user] = []
            user_history[user].append(text)

        t = threading.Thread(target=__run_command,
                             args=(msg, text), daemon=True)
        t.start()
        t.join(2)
        if t.is_alive():
            coqtop: replwrap.REPLWrapper = user_repls[msg.nick]
            coqtop.child.kill(signal.SIGINT)
            user_repls.pop(msg.nick, None)
            reply(msg, "Command timed out. I Cleared your environment")

    threading.Thread(target=_run_command, args=(msg, text)).start()


@utils.arg_command("clear", "Clear environment")
async def clear(bot: IrcBot, match: re.Match, message: Message):
    global user_repls
    user_repls.pop(message.nick, None)
    user_history.pop(message.nick, None)
    reply(message, "Environment cleared")


@utils.regex_cmd_with_messsage(f"^{PREFIX} (.+)$")
async def run(bot: IrcBot, match: re.Match, message: Message):
    text = match.group(1).strip()
    run_command(message, text)


@utils.arg_command("paste", "Pastes your environment code")
async def pipaste(bot: IrcBot, args: re.Match, msg: Message):
    if msg.nick not in user_repls:
        reply(msg, "You don't have an environment")
        return
    reply(msg, paste("\n".join(user_history[msg.nick])))


@utils.arg_command("read", "Populates your environment code with code from url")
async def readurl(bot: IrcBot, args: re.Match, msg: Message):
    if not args[1]:
        reply(msg, "Please provide a url")
        return
    try:
        run_command(msg, read_paste(args[1]))
    except Exception as e:
        reply(msg, "Failed to read paste: " + str(e))
    reply(msg, "Code has been read and sent!")


async def onConnect(bot: IrcBot):
    for channel in CHANNELS:
        await bot.join(channel)

    async def message_handler(text):
        for line in text.splitlines():
            match = re.match(r"^\[\[([^\]]+)\]\] (.*)$", line)
            if match:
                channel, text = match.groups()
                await bot.send_message(text, channel)

    async def update_loop():
        """Update cache to eliminate invalid keys and monitor COQ_EXPORT_DIR."""
        global user_repls, user_history
        while True:
            user_repls.pop(None, None)
            user_history.pop(None, None)
            for file in Path("./").glob("*"):
                info(f"Found {file=}")
                if not file.is_file():
                    continue
                name = file.name
                url = paste(file.read_text())

                # TODO what channel should we send this to?
                for channel in CHANNELS:
                    await bot.send_message(f"{name}: {url}", channel)
                file.unlink()

            await trio.sleep(3)

    async with trio.open_nursery() as nursery:
        nursery.start_soon(listen_loop, FIFO, message_handler)
        nursery.start_soon(update_loop)

if __name__ == "__main__":
    bot = IrcBot(SERVER, PORT, NICK, use_ssl=SSL)
    bot.runWithCallback(onConnect)

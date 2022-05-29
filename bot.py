import re
import trio
from pexpect import replwrap
from cachetools import TTLCache
from tempfile import NamedTemporaryFile
import multiprocessing

from IrcBot.bot import IrcBot, Message, utils

from conf import CHANNELS, NICK, SERVER, PORT, PREFIX, COQTOP_CMD, SSL, COQ_REPL_TTL
from message_server import listen_loop

user_repls = TTLCache(maxsize=32, ttl=COQ_REPL_TTL)

utils.setHelpHeader(
    "USE: {PREFIX} [coq command here]       - (Notice the space)")
utils.setHelpBottom(
    "Nice tutorial coq at https://learnxinyminutes.com/docs/coq/")
utils.setParseOrderTopBottom(True)
utils.setPrefix(PREFIX)

FIFO = NamedTemporaryFile(mode='w+b', prefix='coq-repl-',
                          suffix='.fifo', delete=False).name


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
            line = f"<{msg.nick}> {ansi2irc(line)}"
            f.write(f"[[{msg.channel}]] {line}")


def run_command(msg: Message, text: str):
    global user_repls

    def _run_command(user: str, text: str):
        if user not in user_repls:
            user_repls[user] = replwrap.REPLWrapper(
                COQTOP_CMD, "Coq <", prompt_change=None)
        reply(msg, user_repls[user].run_command(text, timeout=4))

    multiprocessing.Process(target=_run_command, args=(msg, text)).start()



@utils.arg_command("clear", "Clear environment")
async def clear(bot: IrcBot, match: re.Match, message: Message):
    global user_repls
    user_repls.pop(message.nick, None)
    reply(message, "Environment cleared")


@utils.regex_cmd_with_messsage(f"^{PREFIX} (.+)$")
async def run(bot: IrcBot, match: re.Match, message: Message):
    text = match.group(1).strip()
    run_command(message, text)


async def onConnect(bot: IrcBot):
    for channel in CHANNELS:
        await bot.join(channel)

    async def message_handler(text):
        for line in text.splitlines():
            match = re.match(r"^\[\[([^\]]+)\]\] (.*)$", line)
            if match:
                channel, text = match.groups()
                await bot.send_message(text, channel)

    async with trio.open_nursery() as nursery:
        nursery.start_soon(listen_loop, FIFO, message_handler)

if __name__ == "__main__":
    bot = IrcBot(SERVER, PORT, NICK, use_ssl=SSL)
    bot.runWithCallback(onConnect)

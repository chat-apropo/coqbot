import re
import trio
import signal
from pexpect import replwrap
from cachetools import TTLCache
from tempfile import NamedTemporaryFile
import threading

from IrcBot.bot import IrcBot, Message, utils

from conf import CHANNELS, NICK, SERVER, PORT, PREFIX, COQTOP_CMD, SSL, COQ_REPL_TTL
from message_server import listen_loop

user_repls = TTLCache(maxsize=32, ttl=COQ_REPL_TTL)

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


class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self, *args, **kwargs):
        super(StoppableThread, self).__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()


def run_command(msg: Message, text: str):

    def _run_command(msg: Message, text: str):
        def __run_command(msg: Message, text: str):
            global user_repls
            info(f"{user_repls=}")
            user = msg.nick
            if user not in user_repls:
                info(f"Creating new repl for {user}")
                user_repls[user] = replwrap.REPLWrapper(
                    COQTOP_CMD, "Coq <", prompt_change=None)
            info(f"Running command for {user}")
            reply(msg, user_repls[user].run_command(text, timeout=2))

        t = StoppableThread(target=__run_command, args=(msg, text), daemon=True)
        t.start()
        t.join(2)
        if t.is_alive():
            coqtop: replwrap.REPLWrapper = user_repls[msg.nick]
            coqtop.child.kill(signal.SIGINT)
            user_repls.pop(msg.nick, None)
            t.stop()
            reply(msg, "Command timed out. I Cleared your environment")

    threading.Thread(target=_run_command, args=(msg, text)).start()


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

    async def update_loop():
        """Update cache to eliminate invalid keys."""
        while True:
            global user_repls
            user_repls.pop(None, None)
            await trio.sleep(10)

    async with trio.open_nursery() as nursery:
        nursery.start_soon(listen_loop, FIFO, message_handler)
        nursery.start_soon(update_loop)

if __name__ == "__main__":
    bot = IrcBot(SERVER, PORT, NICK, use_ssl=SSL)
    bot.runWithCallback(onConnect)

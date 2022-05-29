# Coq Bot

Proof of concept coq repl bot for irc. There is not any sort of sandboxing at the moment so this bot could become literally a reverse shell bot if Coq somehow lets that happen. All this does is to wrap `coqtop` at lets each user have his instance.

## Usage

Copy `conf.py.example` to `conf.py`, edit it and start `bot.py`.

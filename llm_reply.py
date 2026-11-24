"""Sending CatGPT's answers to Discord and reading them back as chat history.

One answer can span several Discord messages (text, equation images, overflow chunks), each replying to the
previous one. Bot messages only ever reply to other bot messages within the same answer, which is how the
history side regroups them into a single turn.
"""
import asyncio
import io

import discord

from latex import render_latex_png
from md_render import prepare_reply, split_message


def _no_images(tex):
  return None


async def send_reply(msg, text, can_attach=True):
  """Replies to `msg` with LLM output: markdown adapted for Discord, display equations as images between the text."""
  # Equation rendering is CPU-bound, so keep it off the event loop.
  messages = await asyncio.to_thread(prepare_reply, text, render_latex_png if can_attach else _no_images)
  sent = msg

  async def reply(**kwargs):
    nonlocal sent
    sent = await sent.reply(mention_author=sent is msg, **kwargs)

  for message in messages or ["Mrow?"]:
    if isinstance(message, list) and can_attach:
      try:
        # The alt text carries the LaTeX source, which the history side feeds back to the LLM.
        await reply(files=[
          discord.File(io.BytesIO(eq.png), filename=f"equation{i}.png", description=f"$${eq.tex}$$"[:1024])
          for i, eq in enumerate(message)
        ])
        continue
      except discord.Forbidden:
        # The channel allows messages but not attachments: show the source and keep sending the rest.
        can_attach = False
    if isinstance(message, list):
      message = "\n".join(f"```\n{eq.tex}\n```" for eq in message)
    for chunk in split_message(message):
      await reply(content=chunk, suppress_embeds=True)


async def collect_reply_chain(parent, bot_id, resolve, max_turns, max_messages=30):
  """Walks up the reply chain from `parent`, oldest first, keeping up to `max_turns` turns.

  Consecutive bot messages are fragments of one answer and count as a single turn, so a multipart answer
  can't push the user's question out of the history. `max_messages` bounds the fetches.
  """
  chain = []
  seen = set()
  turns = 0
  prev_is_bot = False
  current = parent
  while current is not None and current.id not in seen and len(chain) < max_messages:
    is_bot = current.author.id == bot_id
    if not (is_bot and prev_is_bot):
      if turns == max_turns:
        break
      turns += 1
    seen.add(current.id)
    chain.append(current)
    prev_is_bot = is_bot
    current = await resolve(current)
  chain.reverse()
  return chain


def chain_to_history(chain, bot_id, describe):
  """Converts a reply chain into chat history, merging the fragments of each bot answer into one assistant turn."""
  history = []
  prev_is_bot = False
  for m in chain:
    is_bot = m.author.id == bot_id
    text = describe(m)
    if text:
      if is_bot and prev_is_bot and history and history[-1]["role"] == "assistant":
        history[-1]["content"] += f"\n{text}"
      elif is_bot:
        history.append({"role": "assistant", "content": text})
      else:
        history.append({"role": "user", "content": f"{m.author.display_name}: {text}"})
    prev_is_bot = is_bot
  return history

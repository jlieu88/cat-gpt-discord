import itertools
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

import llm_reply

BOT_ID = 1
_ids = itertools.count(100)


class FakeMessage:
  def __init__(self, author_id, content="", parent=None, name="Tom", sent=None, deny_files=False):
    self.id = next(_ids)
    self.author = SimpleNamespace(id=author_id, display_name=name)
    self.content = content
    self.parent = parent
    self.files = []
    self.sent = sent if sent is not None else []
    self.deny_files = deny_files

  async def reply(self, content="", files=None, mention_author=True, suppress_embeds=False):
    if files and self.deny_files:
      raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
    m = FakeMessage(BOT_ID, content, parent=self, sent=self.sent, deny_files=self.deny_files)
    m.files = files or []
    m.suppress_embeds = suppress_embeds
    self.sent.append(m)
    return m


async def resolve(m):
  return m.parent


def describe(m):
  return m.content or "\n".join(f.description for f in m.files)


def fake_render(tex):
  return b"png"


class SendReplyTests(unittest.IsolatedAsyncioTestCase):
  async def send(self, text, **kwargs):
    question = FakeMessage(2, "catgpt math pls", **kwargs)
    with patch.object(llm_reply, "render_latex_png", fake_render):
      await llm_reply.send_reply(question, text)
    return question.sent

  async def test_text_and_equations_reply_in_sequence(self):
    sent = await self.send("Area:\n$$\\pi r^2$$\nmeow")
    self.assertEqual([m.content for m in sent], ["Area:", "", "meow"])
    self.assertEqual(sent[1].files[0].description, "$$\\pi r^2$$")
    self.assertEqual([m.parent for m in sent[1:]], sent[:-1])  # each part replies to the previous one
    self.assertTrue(all(m.suppress_embeds for m in sent if m.content))

  async def test_bare_url_answer_suppresses_preview(self):
    sent = await self.send("https://example.com")
    self.assertEqual(sent[0].content, "https://example.com")
    self.assertTrue(sent[0].suppress_embeds)

  async def test_denied_attachments_fall_back_to_source_and_continue(self):
    sent = await self.send("Area:\n$$\\pi r^2$$\nmeow\n$$x^2$$\nend", deny_files=True)
    self.assertEqual(
      [m.content for m in sent],
      ["Area:", "```\n\\pi r^2\n```", "meow", "```\nx^2\n```", "end"],
    )

  async def test_no_attach_permission_skips_rendering(self):
    question = FakeMessage(2, "catgpt math pls")
    await llm_reply.send_reply(question, "Area: $$\\pi r^2$$ meow", can_attach=False)
    self.assertEqual([m.content for m in question.sent], ["Area:\n```\n\\pi r^2\n```\nmeow"])


class ReplyChainTests(unittest.IsolatedAsyncioTestCase):
  async def test_follow_up_to_multipart_answer_keeps_the_question(self):
    q1 = FakeMessage(2, "catgpt hi")
    a1 = FakeMessage(BOT_ID, "Mrow!", parent=q1)
    q2 = FakeMessage(2, "how do I solve quadratics?", parent=a1)
    with patch.object(llm_reply, "render_latex_png", fake_render):
      await llm_reply.send_reply(q2, "Use this:\n$$x = a$$\nor\n$$x = b$$\npurr")
    fragments = q2.sent
    self.assertEqual(len(fragments), 5)

    chain = await llm_reply.collect_reply_chain(fragments[-1], BOT_ID, resolve, max_turns=3)
    history = llm_reply.chain_to_history(chain, BOT_ID, describe)
    self.assertEqual(history, [
      {"role": "user", "content": "Tom: catgpt hi"},
      {"role": "assistant", "content": "Mrow!"},
      {"role": "user", "content": "Tom: how do I solve quadratics?"},
      {"role": "assistant", "content": "Use this:\n$$x = a$$\nor\n$$x = b$$\npurr"},
    ][-3:])

  async def test_turn_limit_counts_answers_not_messages(self):
    q = FakeMessage(2, "question")
    parts = [FakeMessage(BOT_ID, f"part {i}") for i in range(4)]
    parent = q
    for p in parts:
      p.parent = parent
      parent = p
    chain = await llm_reply.collect_reply_chain(parts[-1], BOT_ID, resolve, max_turns=2)
    self.assertEqual(chain, [q] + parts)
    chain = await llm_reply.collect_reply_chain(parts[-1], BOT_ID, resolve, max_turns=1)
    self.assertEqual(chain, parts)


if __name__ == "__main__":
  unittest.main()

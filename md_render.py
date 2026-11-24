"""Adapts LLM markdown to what Discord can actually render.

Discord handles bold/italic, `code`, fenced code, `#`-`###` headings, lists and `>` quotes natively,
but shows tables, `####`+ headings, horizontal rules and LaTeX as raw text, treats `__x__` as underline
instead of bold, and spawns a link preview for every URL. Replies with no markdown are left untouched.
Display LaTeX is rendered to images sent between the text parts; inline LaTeX becomes Unicode where possible.
"""
import re
import unicodedata
from dataclasses import dataclass

from latex import latex_to_unicode, render_latex_png

DISCORD_MESSAGE_LIMIT = 2000

_MARKDOWN_PATTERNS = [
  re.compile(r"^#{1,6}\s+\S", re.M),                     # heading
  re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.M),       # list item
  re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__"),            # bold
  re.compile(r"`"),                                      # inline/fenced code
  re.compile(r"^\s*>", re.M),                            # blockquote
  re.compile(r"!?\[[^\]\n]+\]\([^)\s]+\)"),              # link/image
  re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$", re.M),  # table separator row
  re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$", re.M),     # horizontal rule
  re.compile(r"\$\$|\\\(|\\\["),                         # LaTeX
  re.compile(r"\$(?=\S)[^$\n]*[\\^_{}=][^$\n]*?(?<=\S)\$(?!\d)"),  # inline LaTeX
]
MAX_ATTACHMENTS = 10

_FENCE_RE = re.compile(r"^```.*?^```[ \t]*$", re.M | re.S)
_LATEX_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
_LATEX_INLINE_RE = re.compile(r"\\\((.+?)\\\)|\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)")
_LATEX_HINT_RE = re.compile(r"[\\^_{}=]")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL = r"((?:[^()\s<>]|\([^()\s]*\))+)"  # allows one level of balanced parens, e.g. Wikipedia links
_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(" + _URL + r"\)")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(<?" + _URL + r">?\)")
_BARE_URL_RE = re.compile(r"(?<![<\w])https?://[^\s<>]+")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_TASK_RE = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+")
_DOUBLE_UNDERSCORE_RE = re.compile(r"(?<![\w_])__(?=\S)([^_\n]+?)(?<=\S)__(?![\w_])")
_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")
_EQUATION_RE = re.compile("\x01(\\d+)\x01")
_MARKDOWN_CHAR_RE = re.compile(r"([*_~|`\\])")


@dataclass(frozen=True)
class Equation:
  tex: str
  png: bytes = b""


def has_markdown(text):
  return bool(text) and any(p.search(text) for p in _MARKDOWN_PATTERNS)


def render_for_discord(text):
  """Returns `text` unchanged if it has no markdown, else rewritten into Discord-renderable markdown (equations as code blocks)."""
  text, equations = _render(text)
  return _EQUATION_RE.sub(lambda m: _code_block(equations[int(m.group(1))]), text)


def render_parts(text):
  """Like render_for_discord, but display equations are split out: returns [str | Equation] in reading order."""
  text, equations = _render(text)
  parts = []
  for i, chunk in enumerate(_EQUATION_RE.split(text)):
    if i % 2:
      parts.append(Equation(equations[int(chunk)]))
    elif chunk.strip():
      parts.append(chunk.strip())
  return parts


def prepare_reply(text, render_equation=render_latex_png):
  """Returns the Discord messages to send, in order: a str for text, or a list of rendered Equations for images.

  Equations that fail to render fall back to code blocks inside the surrounding text.
  """
  messages = []
  pending = []

  def flush_text():
    if pending:
      messages.extend(split_message("\n".join(pending)))
      pending.clear()

  for part in render_parts(text):
    png = render_equation(part.tex) if isinstance(part, Equation) else None
    if isinstance(part, str) or not png:
      pending.append(part if isinstance(part, str) else _code_block(part.tex))
      continue
    flush_text()
    if messages and isinstance(messages[-1], list) and len(messages[-1]) < MAX_ATTACHMENTS:
      messages[-1].append(Equation(part.tex, png))
    else:
      messages.append([Equation(part.tex, png)])
  flush_text()
  return messages


def _code_block(tex):
  return f"```\n{tex}\n```"


def _render(text):
  """Returns (rewritten text, equations); display equations are left as \\x01<index>\\x01 tokens."""
  if not has_markdown(text):
    return text, []

  protected = []
  equations = []

  def equation(tex):
    equations.append(tex.strip())
    return f"\x01{len(equations) - 1}\x01"

  def protect(snippet):
    protected.append(snippet)
    return f"\x00{len(protected) - 1}\x00"

  text = text.replace("\r\n", "\n")
  text = _FENCE_RE.sub(lambda m: protect(m.group(0)), text)
  text = _convert_tables(text, protect)  # before equations, so math inside a table stays in its code block
  text = _LATEX_BLOCK_RE.sub(lambda m: equation(m.group(1) or m.group(2)), text)
  text = _INLINE_CODE_RE.sub(lambda m: protect(m.group(0)), text)
  text = _LATEX_INLINE_RE.sub(_inline_latex(protect), text)
  text = _IMAGE_RE.sub(lambda m: protect(f"[{m.group(1).strip() or 'image'}]({m.group(2)})"), text)
  text = _LINK_RE.sub(lambda m: protect(_format_link(m.group(1), m.group(2))), text)
  text = _BARE_URL_RE.sub(lambda m: _suppress_bare_url(m.group(0)), text)
  text = "\n".join(_convert_line(line) for line in text.split("\n"))
  text = _DOUBLE_UNDERSCORE_RE.sub(r"**\1**", text)
  text = re.sub(r"\n{3,}", "\n\n", text).strip()

  # Placeholders can nest (e.g. inline code inside a link label), so restore until stable.
  while _PLACEHOLDER_RE.search(text):
    text = _PLACEHOLDER_RE.sub(lambda m: protected[int(m.group(1))], text)
  return text, equations


def _inline_latex(protect):
  def replace(m):
    tex = m.group(1) if m.group(1) is not None else m.group(2)
    # Bare `$...$` only counts as LaTeX if it looks like math, so prices like "$5 or $10" survive.
    if m.group(1) is None and not _LATEX_HINT_RE.search(tex):
      return m.group(0)
    plain = latex_to_unicode(tex)
    return protect(_MARKDOWN_CHAR_RE.sub(r"\\\1", plain) if plain else f"`{tex.strip()}`")
  return replace


def _format_link(label, url):
  label = label.strip()
  if label == url or re.match(r"https?://", label):
    return f"<{url}>"
  return f"[{label}](<{url}>)"


def _suppress_bare_url(url):
  trailing = ""
  while url and (url[-1] in ".,!?;:'\"" or (url[-1] == ")" and url.count("(") < url.count(")"))):
    trailing = url[-1] + trailing
    url = url[:-1]
  return f"<{url}>{trailing}"


def _convert_line(line):
  if _HR_RE.match(line):
    return ""
  heading = _HEADING_RE.match(line)
  if heading:
    level, title = len(heading.group(1)), heading.group(2)
    # Discord only renders #, ## and ###; anything deeper shows the hashes literally.
    return f"{heading.group(1)} {title}" if level <= 3 else f"**{title}**"
  task = _TASK_RE.match(line)
  if task:
    box = "☐" if task.group(2) == " " else "☑"
    return f"{task.group(1)}- {box} {line[task.end():]}"
  # Discord doesn't nest quotes, so `> > x` would show a literal `>`.
  quote = re.match(r"^\s*>\s*>[>\s]*", line)
  if quote and not line.startswith(">>> "):
    return f"> {line[quote.end():]}"
  return line


def _split_row(line):
  line = line.strip()
  if line.startswith("|"):
    line = line[1:]
  if line.endswith("|") and not line.endswith("\\|"):
    line = line[:-1]
  return [_plain_cell(cell) for cell in re.split(r"(?<!\\)\|", line)]


def _plain_cell(cell):
  cell = cell.replace("\\|", "|").strip()
  cell = _LINK_RE.sub(r"\1", cell)
  cell = re.sub(r"(\*\*|__|~~|`)", "", cell)
  return re.sub(r"(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)", r"\1", cell)


def _display_width(s):
  return sum(2 if unicodedata.east_asian_width(c) in "WF" else 0 if unicodedata.combining(c) else 1 for c in s)


def _pad(s, width):
  return s + " " * (width - _display_width(s))


def _convert_tables(text, protect):
  """Markdown tables render as raw pipes in Discord, so they become aligned monospace code blocks."""
  lines = text.split("\n")
  out = []
  i = 0
  while i < len(lines):
    is_table = (
      i + 1 < len(lines)
      and "|" in lines[i]
      and "|" in lines[i + 1]
      and _TABLE_SEPARATOR_RE.match(lines[i + 1])
    )
    if not is_table:
      out.append(lines[i])
      i += 1
      continue

    rows = [_split_row(lines[i])]
    i += 2
    while i < len(lines) and "|" in lines[i] and lines[i].strip():
      rows.append(_split_row(lines[i]))
      i += 1

    columns = max(len(r) for r in rows)
    rows = [r + [""] * (columns - len(r)) for r in rows]
    widths = [max(_display_width(r[c]) for r in rows) for c in range(columns)]
    rendered = [" | ".join(_pad(cell, widths[c]) for c, cell in enumerate(r)).rstrip() for r in rows]
    rendered.insert(1, "-+-".join("-" * w for w in widths))
    out.append(protect("```\n" + "\n".join(rendered) + "\n```"))
  return "\n".join(out)


def split_message(text, limit=DISCORD_MESSAGE_LIMIT):
  """Splits on line boundaries into chunks under `limit`, closing and reopening code fences across chunks."""
  if len(text) <= limit:
    return [text]

  chunks = []
  current = ""
  fence = None  # the opening ``` line of the code block we're inside, if any

  def flush():
    nonlocal current
    body = current.rstrip("\n")
    if fence:
      body += "\n```"
    if body.strip() and body.strip() != fence:
      chunks.append(body)
    current = f"{fence}\n" if fence else ""

  for line in text.split("\n"):
    reserve = 4 if fence else 0  # room for a closing "\n```"
    while len(current) + len(line) + 1 + reserve > limit:
      room = limit - len(current) - reserve - 1
      if current.strip() and current.strip() != fence:
        flush()
        continue
      # A single line longer than a whole message: hard-split it.
      current += line[:room] + "\n"
      line = line[room:]
      flush()
    current += line + "\n"
    if line.startswith("```"):
      fence = None if fence else line.strip()
  flush()
  return chunks

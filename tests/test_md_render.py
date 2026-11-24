import unittest

from latex import latex_to_unicode, render_latex_png
from md_render import Equation, has_markdown, prepare_reply, render_for_discord, render_parts, split_message


class HasMarkdownTests(unittest.TestCase):
  def test_plain_text_is_not_markdown(self):
    for text in ["Meow! Strawberry has three r's, human.", "It costs $5 or $10, mrow.", "snake_case_name", ""]:
      self.assertFalse(has_markdown(text), text)

  def test_detects_common_markdown(self):
    for text in ["# Title", "- item", "**bold**", "use `x`", "> quote", "[a](https://a.com)", "a | b\n--|--", "---", "$$x^2$$"]:
      self.assertTrue(has_markdown(text), text)


class RenderTests(unittest.TestCase):
  def test_plain_text_is_unchanged(self):
    text = "Mrow, *purrs*. Prices: $5-$10. See https://example.com"
    self.assertEqual(render_for_discord(text), text)

  def test_supported_markdown_is_kept(self):
    text = "## Cats\n- **fluffy**\n- *sleepy*\n> wise words"
    self.assertEqual(render_for_discord(text), text)

  def test_deep_headings_become_bold(self):
    self.assertEqual(render_for_discord("#### Deep\ntext"), "**Deep**\ntext")
    self.assertEqual(render_for_discord("### Learn C#"), "### Learn C#")

  def test_horizontal_rules_are_removed(self):
    self.assertEqual(render_for_discord("# A\n\n---\n\nB"), "# A\n\nB")

  def test_table_becomes_code_block(self):
    text = "Cats:\n\n| Name | **Age** |\n|:-----|----:|\n| Tom | 3 |\n| Whiskers | 12 |\n\nmeow"
    expected = (
      "Cats:\n\n```\n"
      "Name     | Age\n"
      "---------+----\n"
      "Tom      | 3\n"
      "Whiskers | 12\n"
      "```\n\nmeow"
    )
    self.assertEqual(render_for_discord(text), expected)

  def test_links_do_not_spawn_previews(self):
    self.assertEqual(render_for_discord("- [Cats](https://a.com/x)"), "- [Cats](<https://a.com/x>)")
    self.assertEqual(render_for_discord("- [https://a.com](https://a.com)"), "- <https://a.com>")
    self.assertEqual(render_for_discord("- see https://a.com/x."), "- see <https://a.com/x>.")
    self.assertEqual(render_for_discord("- (https://a.com)"), "- (<https://a.com>)")

  def test_links_with_parens(self):
    url = "https://en.wikipedia.org/wiki/Cat_(animal)"
    self.assertEqual(render_for_discord(f"**[Cat]({url})**"), f"**[Cat](<{url}>)**")

  def test_code_is_left_alone(self):
    text = "**Code:**\n```py\n#### not a heading\n__init__ | x\n---\n```\nand `__x__ https://a.com`"
    self.assertEqual(render_for_discord(text), text)

  def test_double_underscore_means_bold(self):
    self.assertEqual(render_for_discord("- __big__ cat"), "- **big** cat")

  def test_latex_becomes_code(self):
    self.assertEqual(render_for_discord("Area: $$\\pi r^2$$"), "Area: ```\n\\pi r^2\n```")
    self.assertEqual(render_for_discord("**E** is \\(mc^2\\)"), "**E** is mc²")
    self.assertEqual(render_for_discord("**x** $a^2$ costs $5 or $10"), "**x** a² costs $5 or $10")
    self.assertEqual(render_for_discord("Mrow $e^{-x^2}$"), "Mrow `e^{-x^2}`")  # nested superscript: no Unicode form

  def test_task_lists_and_nested_quotes(self):
    self.assertEqual(render_for_discord("- [ ] nap\n- [x] eat"), "- ☐ nap\n- ☑ eat")
    self.assertEqual(render_for_discord("> > nested"), "> nested")


class LatexUnicodeTests(unittest.TestCase):
  def test_converts_simple_math(self):
    cases = {
      "E = mc^2": "E = mc²",
      "a_{ij} \\leq x_1": "aᵢⱼ ≤ x₁",
      "\\frac{\\pi}{2}": "π/2",
      "\\frac{a+b}{2}": "(a+b)/2",
      "\\sqrt{x^2 + 1}": "√(x² + 1)",
      "\\text{area} = \\pi r^2": "area = π r²",
      "\\left( x \\right)\\,\\cdot y": "( x ) · y",
      "\\sin \\theta": "sin θ",
    }
    for tex, expected in cases.items():
      self.assertEqual(latex_to_unicode(tex), expected, tex)

  def test_gives_up_when_unfaithful(self):
    for tex in ["e^{-x^2}", "x_\\alpha", "\\begin{pmatrix}1\\end{pmatrix}", "\\unknowncmd x", "a \\\\ b"]:
      self.assertIsNone(latex_to_unicode(tex), tex)


class EquationPartsTests(unittest.TestCase):
  def test_plain_text_is_one_part(self):
    self.assertEqual(render_parts("meow"), ["meow"])

  def test_display_math_splits_text(self):
    self.assertEqual(
      render_parts("**Area:** $$\\pi r^2$$ meow \\[x\\] end"),
      ["**Area:**", Equation("\\pi r^2"), "meow", Equation("x"), "end"],
    )

  def test_math_in_code_or_tables_is_not_split_out(self):
    text = "| a | b |\n|---|---|\n| $$x$$ | 1 |\n\n```\n$$y$$\n```"
    self.assertTrue(all(isinstance(p, str) for p in render_parts(text)))

  def test_prepare_reply_groups_images_and_falls_back(self):
    fake = lambda tex: None if tex == "bad" else tex.encode()
    messages = prepare_reply("# Hi\n$$a$$\n$$b$$\nmid $$bad$$ end\n$$c$$", render_equation=fake)
    self.assertEqual(messages, [
      "# Hi",
      [Equation("a", b"a"), Equation("b", b"b")],
      "mid\n```\nbad\n```\nend",
      [Equation("c", b"c")],
    ])

  def test_prepare_reply_plain_text(self):
    self.assertEqual(prepare_reply("meow", render_equation=lambda tex: b"x"), ["meow"])


try:
  import matplotlib  # noqa: F401
  HAVE_MATPLOTLIB = True
except ImportError:
  HAVE_MATPLOTLIB = False


@unittest.skipUnless(HAVE_MATPLOTLIB, "matplotlib not installed")
class LatexImageTests(unittest.TestCase):
  def test_renders_png(self):
    png = render_latex_png("\\displaystyle \\int_0^\\infty e^{-x^2}\\,dx = \\dfrac{\\sqrt{\\pi}}{2}")
    self.assertTrue(png.startswith(b"\x89PNG"))

  def test_unsupported_returns_none(self):
    self.assertIsNone(render_latex_png("\\begin{pmatrix}1 & 2\\end{pmatrix}"))
    self.assertIsNone(render_latex_png("\\frac{"))


class SplitTests(unittest.TestCase):
  def test_short_text_is_one_chunk(self):
    self.assertEqual(split_message("meow"), ["meow"])

  def test_splits_on_lines_under_limit(self):
    text = "\n".join(f"line {i}" for i in range(100))
    chunks = split_message(text, limit=50)
    self.assertTrue(all(len(c) <= 50 for c in chunks))
    self.assertEqual("\n".join(chunks), text)

  def test_code_fences_are_reopened(self):
    text = "intro\n```py\n" + "\n".join(f"x = {i}" for i in range(30)) + "\n```\nouttro"
    chunks = split_message(text, limit=60)
    self.assertTrue(all(len(c) <= 60 for c in chunks))
    for chunk in chunks:
      self.assertEqual(chunk.count("```") % 2, 0, chunk)

  def test_hard_splits_long_lines(self):
    chunks = split_message("a" * 250, limit=100)
    self.assertTrue(all(len(c) <= 100 for c in chunks))
    self.assertEqual("".join(chunks), "a" * 250)


if __name__ == "__main__":
  unittest.main()

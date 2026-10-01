"""Run with: python3 -m unittest test_build_epub.py"""

import posixpath
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote
from zipfile import ZIP_STORED, ZipFile

import build_epub


class EpubBuildTests(unittest.TestCase):
    def test_contents_and_chapter_links(self):
        chapters = build_epub.chapters_from_readme()
        self.assertEqual(len(chapters), 42)
        book = build_epub.make_book(chapters)
        self.assertLess(book.index("# 第1部"), book.index("# 第2部"))
        self.assertLess(book.index("# 第2部"), book.index("# 第3部"))
        for chapter in chapters:
            self.assertIn(f"{{#{chapter.anchor}}}", book)
        self.assertIn("[次章 →](#chapter-02)", book)
        self.assertNotIn("[prev](../35/35.md)", book)
        self.assertNotIn("EPUB を作成する", book)
        vertical_book = build_epub.make_book(chapters, vertical=True)
        self.assertIn("[← 次章](#chapter-02)", vertical_book)
        self.assertIn("[前章 →](#chapter-01)", vertical_book)

    def test_vertical_default_output(self):
        with patch("sys.argv", ["build_epub.py", "--vertical"]), patch.object(build_epub, "build") as build:
            self.assertEqual(build_epub.main(), 0)
        build.assert_called_once_with(build_epub.ROOT / "ostep-ja-vertical.epub", "pandoc", True)

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is not installed")
    def test_epub_package_and_all_local_links(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.epub"
            build_epub.build(output, "pandoc")
            with ZipFile(output) as epub:
                names = set(epub.namelist())
                self.assertEqual(epub.namelist()[0], "mimetype")
                self.assertEqual(epub.getinfo("mimetype").compress_type, ZIP_STORED)
                self.assertEqual(epub.read("mimetype"), b"application/epub+zip")
                opf = ET.fromstring(epub.read("EPUB/content.opf"))
                dc = "{http://purl.org/dc/elements/1.1/}"
                self.assertEqual(opf.find(f".//{dc}language").text, "ja")
                self.assertEqual(opf.find(f".//{dc}title").text, build_epub.TITLE)
                self.assertEqual([creator.text for creator in opf.findall(f".//{dc}creator")],
                                 list(build_epub.AUTHORS))
                title_page = epub.read("EPUB/text/title_page.xhtml").decode("utf-8")
                for author in build_epub.AUTHORS:
                    self.assertIn(author, title_page)
                nav = epub.read("EPUB/nav.xhtml").decode("utf-8")
                self.assertEqual(nav.count("#chapter-"), 42)
                self.assertIn("第1部", nav)
                self.assertIn("第2部", nav)
                self.assertIn("第3部", nav)
                self.assertIn("EPUB/toc.ncx", names)
                self.assertEqual(len(opf.findall(".//{http://www.idpf.org/2007/opf}itemref")),
                                 48)  # title page, contents, introduction, 3 parts, 42 chapters
                self.assertGreater(len([name for name in names if "/media/" in name]), 300)
                css = epub.read("EPUB/styles/stylesheet1.css").decode("utf-8")
                self.assertIn("horizontal-tb", css)
                self.assertIn("Noto Serif CJK JP", css)
                self.assertIsNone(opf.find("{http://www.idpf.org/2007/opf}spine").get("page-progression-direction"))
                self.assertNotIn("EPUB/styles/stylesheet2.css", names)
                self.assert_links_resolve(epub)

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is not installed")
    def test_vertical_epub(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "vertical.epub"
            build_epub.build(output, "pandoc", vertical=True)
            with ZipFile(output) as epub:
                opf = ET.fromstring(epub.read("EPUB/content.opf"))
                self.assertEqual(opf.find("{http://www.idpf.org/2007/opf}spine").get("page-progression-direction"), "rtl")
                dc = "{http://purl.org/dc/elements/1.1/}"
                self.assertEqual(opf.find(f".//{dc}language").text, "ja")
                self.assertEqual(opf.find(f".//{dc}title").text, build_epub.TITLE)
                self.assertEqual([creator.text for creator in opf.findall(f".//{dc}creator")],
                                 list(build_epub.AUTHORS))
                css = epub.read("EPUB/styles/stylesheet2.css").decode("utf-8")
                self.assertIn("writing-mode: vertical-rl", css)
                self.assertIn("writing-mode: horizontal-tb", css)
                self.assertIn('href="styles/stylesheet2.css"', epub.read("EPUB/nav.xhtml").decode("utf-8"))
                self.assertTrue(any("← 次章" in epub.read(name).decode("utf-8")
                                    for name in epub.namelist() if name.endswith(".xhtml")))
                self.assert_links_resolve(epub)

    def assert_links_resolve(self, epub):
        # Check that every image, stylesheet and internal link resolves,
        # including fragment targets after Pandoc splits chapters.
        names = set(epub.namelist())
        documents = {
            name: ET.fromstring(epub.read(name))
            for name in names if name.endswith(".xhtml")
        }
        ids = {
            name: {element.get("id") for element in root.iter()}
            for name, root in documents.items()
        }
        self.assertTrue(any(b"<math" in epub.read(name) for name in documents))
        self.assertTrue(all('xml:lang="ja"' in epub.read(name).decode("utf-8")
                            for name in documents))
        for name, root in documents.items():
            for element in root.iter():
                url = element.get("href") or element.get("src")
                if not url or url.startswith(("http:", "https:", "mailto:", "data:")):
                    continue
                target, _, fragment = url.partition("#")
                path = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(target))) if target else name
                self.assertIn(path, names, (name, url))
                if fragment:
                    self.assertIn(unquote(fragment), ids[path], (name, url))


if __name__ == "__main__":
    unittest.main()

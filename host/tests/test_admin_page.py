"""The Agent settings page (`jarvis admin`) as shipped: zh/en dictionaries, user wording, packaged brand files, content types.

- Dictionaries: zh and en have the same keys and the same {placeholders}; every key the page uses exists; no internal term
  (agentjarvis/i18n/glossary.json `forbidden`) or banned Chinese word reaches the user.
- Packaging: every same-origin file index.html / the CSS reference is in admin.ASSETS and served with the right type, nothing
  outside the map is served, the brand copy matches agentjarvis/brand (`sync.mjs --check`) and admin.zh.json matches its
  source + the polish cache (`polish.py --check`). The last two need the private checkout (brand/ and i18n/ are not part of the
  public export) and are skipped without it.
Language persistence is client-side (brand/lang.js: localStorage "aj.lang" of this origin, or ?lang=); its own tests live in
agentjarvis/brand/test/lang.test.mjs.
"""
import http.client
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))

from jarvis_host import admin  # noqa: E402
from jarvis_host.state import State  # noqa: E402

AJ = HOST.parent                                  # agentjarvis/ in the private checkout
BRAND = AJ / "brand"
I18N = AJ / "i18n"
PAGE = admin.ASSET_DIR
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _dicts():
    return {lang: json.loads((PAGE / f"i18n/admin.{lang}.json").read_text(encoding="utf-8")) for lang in ("zh", "en")}


class Dictionaries(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        d = _dicts()
        src = json.loads((PAGE / "i18n/admin.zh.src.json").read_text(encoding="utf-8")) \
            if (PAGE / "i18n/admin.zh.src.json").is_file() else d["zh"]
        self.assertEqual(sorted(d["zh"]), sorted(d["en"]), "zh and en have exactly the same keys")
        self.assertEqual(sorted(src), sorted(d["zh"]), "the polished file has the source's keys")
        for k in d["zh"]:
            for lang in ("zh", "en"):
                self.assertIsInstance(d[lang][k], str, f"{lang}:{k}")
                self.assertTrue(d[lang][k].strip(), f"{lang}:{k} is empty")
            self.assertEqual(sorted(PLACEHOLDER.findall(d["zh"][k])), sorted(PLACEHOLDER.findall(d["en"][k])), k)
            self.assertEqual(d["zh"][k].count("`") % 2, 0, f"zh:{k}: unbalanced code mark")
            self.assertEqual(d["en"][k].count("`") % 2, 0, f"en:{k}: unbalanced code mark")
        self.assertFalse([k for k, v in d["en"].items() if re.search(r"[㐀-鿿]", v)], "no Chinese left in English")

    def test_every_key_the_page_uses_exists(self):
        d = _dicts()["zh"]
        html = (PAGE / "index.html").read_text(encoding="utf-8")
        js = (PAGE / "app.js").read_text(encoding="utf-8")
        used = set(re.findall(r'data-i18n="([\w.]+)"', html))
        for attrs in re.findall(r'data-i18n-attr="([^"]+)"', html):
            used |= {p.split(":")[1].strip() for p in attrs.split(",")}
        used |= set(re.findall(r"""\bt\(\s*'([\w.]+)'""", js))
        used |= set(re.findall(r"""(?:say|nameMsg|errKey\([^,]+,)\s*\(?\s*'([\w.]+)'""", js))
        used |= {f"deny.{r}" for r in admin.DENY_REASONS}
        used |= {f"name.starter_{i}" for i in range(1, 5)}
        missing = sorted(k for k in used if k not in d)
        self.assertEqual(missing, [], "keys used by the page but missing from the dictionaries")
        self.assertGreater(len(used), 60)
        for code in re.findall(r"'(\w+)'", re.search(r"const ERRS = new Set\(\[(.*?)\]\)", js, re.S).group(1)):
            self.assertIn(f"err.{code}", d)
        for code in re.findall(r"'(\w+)'", re.search(r"const NAME_ERRS = new Set\(\[(.*?)\]\)", js, re.S).group(1)):
            self.assertIn(f"name.err.{code}", d)
        for code in ("name_required", "bad_name", "name_taken", "unreachable", "not_bound", "rate_limited", "failed"):
            self.assertIn(f"name.err.{code}", d, "every names.rename() error has its own words")

    @unittest.skipUnless((I18N / "glossary.json").is_file(), "agentjarvis/i18n not in this checkout (public export)")
    def test_no_internal_terms_reach_the_user(self):
        g = json.loads((I18N / "glossary.json").read_text(encoding="utf-8"))
        html = (PAGE / "index.html").read_text(encoding="utf-8")
        visible = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S))
        texts = {f"{lang}:{k}": v for lang, d in _dicts().items() for k, v in d.items()}
        texts["index.html"] = visible
        for where, text in texts.items():
            for f in g["forbidden"]:
                m = re.search(f["pattern"], text)
                self.assertIsNone(m, f"{where}: internal term {m and m.group(0)!r} — say {f['say_zh']!r} / {f['say_en']!r}")
            for w in g["banned_words_zh"]:
                self.assertNotIn(w, text, where)
            for p in g["banned_patterns_zh"]:
                self.assertIsNone(re.search(p, text), where)
            self.assertNotRegex(text, r"(?i)dashboard(?! )|\bDashboard\b(?! ?(?:page|’|'s))" if where.startswith("zh") else r"$^",
                                f"{where}: 公司后台, not Dashboard")
            if where.startswith("zh"):
                self.assertNotRegex(text, r"中继|安全码|智能体", f"{where}: TERMS.md wording")

    def test_fixed_terms_in_chinese(self):
        zh = "\n".join(_dicts()["zh"].values())
        for term in ("Agent 管理页", "手机遥控器", "批准口令", "6 位码", "公司后台", "公司账号", "转发服务器", "电脑端程序", "席位"):
            self.assertIn(term, zh)


class Packaged(unittest.TestCase):
    def test_every_referenced_file_is_served(self):
        html = (PAGE / "index.html").read_text(encoding="utf-8")
        refs = set(re.findall(r'(?:src|href)="(/[^"]*)"', html))
        for css in ("app.css", "brand/palette.css", "brand/base.css"):
            for u in re.findall(r'url\("?([^")]+)"?\)', (PAGE / css).read_text(encoding="utf-8")):
                if not u.startswith("data:"):
                    refs.add("/" + (pathlib.PurePosixPath(css).parent / u).as_posix())
        refs |= {"/i18n/admin.zh.json", "/i18n/admin.en.json"}
        self.assertTrue({"/brand/lang.js", "/brand/ui.js", "/app.js", "/favicon.ico", "/brand/fonts/ubuntu-400.woff2"} <= refs)
        for r in sorted(refs):
            self.assertIn(r, admin.ASSETS, r)
            self.assertTrue((PAGE / admin.ASSETS[r][0]).is_file(), r)
        for path, (name, ctype) in admin.ASSETS.items():
            want = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
                    ".woff2": "font/woff2", ".png": "image/png", ".webp": "image/webp", ".ico": "image/x-icon"}[pathlib.Path(name).suffix]
            self.assertTrue(ctype.startswith(want), (path, ctype))
        self.assertFalse([p for p in admin.ASSETS if p.endswith((".src.json", ".md", ".txt"))], "only page files are served")

    def test_served_with_types_and_headers(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name))
        st.init()
        srv = admin.AdminServer(st).start()
        self.addCleanup(srv.stop)

        def get(path, host=None):
            c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
            c.request("GET", path, headers={"Host": host or f"127.0.0.1:{srv.port}"})
            r = c.getresponse()
            body = r.read()
            c.close()
            return r, body

        for path in ("/", "/?lang=en", "/?lang=zh", "/brand/lang.js", "/brand/palette.css", "/brand/fonts/lexend-700.woff2",
                     "/brand/img/shield-64.png", "/brand/img/shield-128.webp", "/favicon.ico", "/apple-touch-icon.png",
                     "/i18n/admin.zh.json", "/i18n/admin.en.json"):
            r, body = get(path)
            self.assertEqual(r.status, 200, path)
            name, ctype = admin.ASSETS[path.split("?")[0]]
            self.assertEqual(r.getheader("Content-Type"), ctype, path)
            self.assertEqual(body, (PAGE / name).read_bytes(), path)
            self.assertEqual(r.getheader("Content-Security-Policy"), admin.SECURITY_HEADERS[0][1], path)
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff", path)
            self.assertIsNone(r.getheader("Set-Cookie"), path)
        csp = admin.SECURITY_HEADERS[0][1]
        for d in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "font-src 'self'", "connect-src 'self'",
                  "frame-ancestors 'none'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(d, csp)
        self.assertNotIn("unsafe", csp)
        self.assertNotRegex(csp, r"https?:|\*", "nothing from another origin")
        for path in ("/i18n/admin.zh.src.json", "/brand/fonts/README.md", "/brand/fonts/UFL-1.0-ubuntu.txt", "/brand/",
                     "/brand/../admin.py", "/brand/%2e%2e/app.js", "/i18n/admin.fr.json", "/?lang=fr", "/?lang=en&x=1",
                     "/app.js?lang=en", "/brand/lang.js?x", "/index.html", "/brand/img/shield-source.png"):
            r, body = get(path)
            self.assertEqual((r.status, body), (404, b""), path)
        r, _ = get("/brand/lang.js", host="evil.example:80")
        self.assertEqual(r.status, 404, "foreign Host header")


@unittest.skipUnless(BRAND.joinpath("sync.mjs").is_file() and shutil.which("node"), "agentjarvis/brand or node not available")
class BrandCopy(unittest.TestCase):
    def test_brand_copy_is_current(self):
        r = subprocess.run(["node", str(BRAND / "sync.mjs"), str(PAGE), "--set", "admin", "--check"], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "run: node agentjarvis/brand/sync.mjs host/jarvis_host/admin --set admin\n"
                         + r.stdout + r.stderr)


@unittest.skipUnless(I18N.joinpath("polish.py").is_file(), "agentjarvis/i18n not in this checkout (public export)")
class PolishedCopy(unittest.TestCase):
    def test_zh_json_matches_source_and_cache(self):
        r = subprocess.run([sys.executable, str(I18N / "polish.py"), "--check", str(PAGE / "i18n")], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "run: python3 agentjarvis/i18n/polish.py --surface admin "
                         "host/jarvis_host/admin/i18n/\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()

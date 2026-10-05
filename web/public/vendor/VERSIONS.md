# Vendored web libraries

Upstream bytes, unmodified, loaded only by `js/render.js` on first need (never on first paint, never from a CDN).
Fetched once with `npm pack <name>@<version>` (registry tarball below), extracted, files copied as-is.
Every file in this list is re-checked against its sha256 by `test/p57_render.test.mjs`; the JS/CSS are also pinned in
`test/static.test.mjs` (`VENDORED`). To upgrade: pack the new exact version, copy, update both lists, re-run the browser test.
`jsQR.js` (QR decoder, see `jsQR-LICENSE.txt`) predates this file and is pinned in `static.test.mjs` only.

| Library | Version | License | Source tarball | Tarball sha256 |
|---|---|---|---|---|
| highlight.js (`@highlightjs/cdn-assets`, common-languages bundle) | 11.12.0 | BSD-3-Clause (`hljs/LICENSE.txt`) | https://registry.npmjs.org/@highlightjs/cdn-assets/-/cdn-assets-11.12.0.tgz | b8a006d30f45afe783072569f3d69c5b60c0e7b9ca28cd474e12f2584e2a3bd9 |
| KaTeX (`katex`, `dist/` UMD + CSS + woff2 fonts only) | 0.19.0 | MIT (`katex/LICENSE.txt`) | https://registry.npmjs.org/katex/-/katex-0.19.0.tgz | d8e49f2fea6eeed7cdae2cf4fd48e2f1d348758aa9e5740715e4533c2c96d1ee |
| mermaid (`mermaid`, `dist/mermaid.min.js` single-file UMD) | 12.1.0 | MIT (`mermaid/LICENSE.txt`) | https://registry.npmjs.org/mermaid/-/mermaid-12.1.0.tgz | 3e516f33246e1a6a5bc5ebcabd35434669b487c3b3791f9b0466aa2c254d004a |

## Files (sha256)

```
8ab71eb09c51f501e5e25157d9cff100e46cc29bcbfc744d0b746d451fca7f53  hljs/highlight.min.js
6c081431591d9df696c82dc598fe1423765b8a299b200ed00b281afd0f64c490  hljs/LICENSE.txt
0cdd387c9590a1a9f9794560022dbb59654a7d86f187aa0c81495ad42d3a7308  katex/fonts/KaTeX_AMS-Regular.woff2
de7701e42cf1f4cf0b766c03fb27977207eee2f4fd5d76fa82188406da43ea4c  katex/fonts/KaTeX_Caligraphic-Bold.woff2
5d53e70ad607c2352162dec9e0923fb54ecdafaccbf604cd8dcf7d00facb989b  katex/fonts/KaTeX_Caligraphic-Regular.woff2
74444efd593c005e3f4573b44524704c0af0a937fe911cca9e94068d0d140d3f  katex/fonts/KaTeX_Fraktur-Bold.woff2
51814d270d06ff0255dba0799994fa4d8c84d11f09951d47595f4abb1f3602dc  katex/fonts/KaTeX_Fraktur-Regular.woff2
99cd42a3c072d918f2f44984a807cf7aa16e13545fd0875fc07c6c65f99e715b  katex/fonts/KaTeX_Main-BoldItalic.woff2
0f60d1b897938ec918c8ce073092411baf9438f6739465693ff18b0f9d20b021  katex/fonts/KaTeX_Main-Bold.woff2
97479ca6cce906abc961ecac96faa5f9ca2e61b8e7670d475826bcdee9a7c267  katex/fonts/KaTeX_Main-Italic.woff2
c2342cd8b869e01752a9321dc17213fc40d4d04c79688c1d43f2cf316abd7866  katex/fonts/KaTeX_Main-Regular.woff2
dc47344dbb6cb5b655c8460d561f4df5f501b90c804ad3c6cec65fe322351ab1  katex/fonts/KaTeX_Math-BoldItalic.woff2
7af58c5ec8f132a2ddde9027c6d7814decce4d3b822a11192a42a20e2e973264  katex/fonts/KaTeX_Math-Italic.woff2
e99ae51144bf1232efcc1bfe5add36262c6866b0faab24fa75740e1b98577a62  katex/fonts/KaTeX_SansSerif-Bold.woff2
00b26ac825e2095056396e0553b8ac26d3f8ad158c3826e28b4c45b385c4714a  katex/fonts/KaTeX_SansSerif-Italic.woff2
68e8c73ef42afd3ccec58bf0fba302cce448938e7fc020a5e31f8a952eee1342  katex/fonts/KaTeX_SansSerif-Regular.woff2
036d4e95149b69ff9bcc0cd55771efeb25ffa3947293e69acd78d5ac328c684b  katex/fonts/KaTeX_Script-Regular.woff2
6b47c40166b6dbe21a5dfca7718413f2147fd2399be1ba605d8ad39cedf25dfe  katex/fonts/KaTeX_Size1-Regular.woff2
d04c54219f9eaec6d4d4fd42dfb28785975a4794d6b2fc71e566b9cd6db842dd  katex/fonts/KaTeX_Size2-Regular.woff2
73d591271b1604960cb10bb90fee021670af7297017e0e98480b332d11f51995  katex/fonts/KaTeX_Size3-Regular.woff2
a4af7d414440a1c1790825cfb700cf9cf43b0f2c4b04f0ebc523011ad9853ec0  katex/fonts/KaTeX_Size4-Regular.woff2
71d517d67827787cfabdf186914cc3358eda539e37931941f2b2fd4a21f68c0b  katex/fonts/KaTeX_Typewriter-Regular.woff2
d4ab5b8ee16989b070cdb0ea24bd6ad48fc8df1b787f280f29d3ae4f959f2c00  katex/katex.min.css
103a53763cc033bba8d175bf3f0ba597c3505c9b6747dd3f2c7bc2a6bfcc8ae7  katex/katex.min.js
766ccc1f306c885aa45542a9846bbd0a505b27a0374f146778171c2254ce18e3  katex/LICENSE.txt
ec9fb67dcb25eccc416ed56e1aab819222c805a2a4bfe4cb19e7556bf2ffde80  mermaid/LICENSE.txt
6484afc32872a3aa16cac9a76ba1816a1ed4cc870a6593cc2e17757750f518b2  mermaid/mermaid.min.js
```

#!/usr/bin/env python3
"""P183 preview fixtures, generated offline (no third-party code): a 2-page PDF with text, a .docx, an .xlsx (2 sheets) and a
.pptx (preview not supported → download). Run: python3 web/test/fixtures/p183/make.py"""
import pathlib, zipfile

HERE = pathlib.Path(__file__).resolve().parent
Z = dict(compression=zipfile.ZIP_DEFLATED)


def pdf():
    objs = []
    def add(s): objs.append(s); return len(objs)
    font = add(b'<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>')
    pages_id = len(objs) + 1 + 4      # catalog later; placeholder ids computed below
    kids, contents = [], []
    for i, line in enumerate([b'Quarterly report - page one', b'Page two: the numbers']):
        stream = b'BT /F1 24 Tf 40 300 Td (' + line + b') Tj ET'
        c = add(b'<</Length %d>>stream\n' % len(stream) + stream + b'\nendstream')
        contents.append(c)
    pages = len(objs) + 1 + len(contents)
    for c in contents:
        kids.append(add(b'<</Type/Page/Parent %d 0 R/MediaBox[0 0 420 400]/Resources<</Font<</F1 %d 0 R>>>>/Contents %d 0 R>>' % (pages, font, c)))
    assert add(b'<</Type/Pages/Kids[%s]/Count %d>>' % (b' '.join(b'%d 0 R' % k for k in kids), len(kids))) == pages
    root = add(b'<</Type/Catalog/Pages %d 0 R>>' % pages)
    out = bytearray(b'%PDF-1.4\n'); offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b'%d 0 obj\n' % i + o + b'\nendobj\n'
    x = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % o for o in offs)
    out += b'trailer<</Size %d/Root %d 0 R>>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, root, x)
    (HERE / 'report.pdf').write_bytes(bytes(out))


def docx():
    with zipfile.ZipFile(HERE / 'plan.docx', 'w', **Z) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        body = ''.join(f'<w:p><w:r>{"<w:rPr><w:b/></w:rPr>" if i == 0 else ""}<w:t xml:space="preserve">{t}</w:t></w:r></w:p>'
                       for i, t in enumerate(['季度计划', '第一条：把文件卡片做成虚线框。', '第二条：所有按钮改成图标。']))
        z.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body>{body}</w:body></w:document>')


def xlsx():
    def sheet(rows):
        cells = ''.join('<row r="%d">%s</row>' % (r + 1, ''.join(f'<c r="{chr(65 + c)}{r + 1}" t="inlineStr"><is><t>{v}</t></is></c>' for c, v in enumerate(row)))
                        for r, row in enumerate(rows))
        return f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{cells}</sheetData></worksheet>'
    with zipfile.ZipFile(HERE / 'sales.xlsx', 'w', **Z) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   '<sheet name="一季度" sheetId="1" r:id="rId1"/><sheet name="二季度" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', sheet([['产品', '销量'], ['卡片', '120'], ['图标', '80']]))
        z.writestr('xl/worksheets/sheet2.xml', sheet([['产品', '销量'], ['预览', '200']]))


def pptx():
    with zipfile.ZipFile(HERE / 'deck.pptx', 'w', **Z) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')


if __name__ == '__main__':
    pdf(); docx(); xlsx(); pptx()
    for f in sorted(HERE.iterdir()):
        if f.suffix != '.py': print(f.name, f.stat().st_size)

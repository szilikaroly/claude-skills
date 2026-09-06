// Build a BMJ-format .docx from a markdown source.
//   node build_docx.js [source.md] [output.docx]
// Defaults to manuscript.md -> BMJ-HCI-submission.docx.
// Heading hierarchy per BMJ: level 1 BOLD CAPS, level 2 bold lower case, body plain.
const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  WidthType, AlignmentType, BorderStyle, ShadingType, HeadingLevel,
} = require('/tmp/claude-0/-home-user-claude-skills/f0395eb1-844b-5266-b0a2-4502744a3f0e/scratchpad/node_modules/docx');

const PAGE_DXA = 9026;                       // A4 content width at 1" margins
const SRC = process.argv[2] || 'manuscript.md';
const OUT = process.argv[3] || 'BMJ-HCI-submission.docx';
const md = fs.readFileSync(SRC, 'utf8').split('\n');

// --- inline markdown (**bold**, *italic*, `code`) -> TextRun[] -------------
function runs(text, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const tok = m[0];
    if (tok.startsWith('**')) out.push(new TextRun({ text: tok.slice(2, -2), bold: true, ...base }));
    else if (tok.startsWith('`')) out.push(new TextRun({ text: tok.slice(1, -1), font: 'Consolas', ...base }));
    else out.push(new TextRun({ text: tok.slice(1, -1), italics: true, ...base }));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out.length ? out : [new TextRun({ text: '', ...base })];
}

const body = (text, opts = {}) =>
  new Paragraph({ children: runs(text), spacing: { after: 160, line: 320 }, ...opts });

const cell = (text, { bold = false, width, shaded = false, size = 18 } = {}) =>
  new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: shaded ? { type: ShadingType.CLEAR, fill: 'F2F2F2' } : undefined,
    margins: { top: 60, bottom: 60, left: 80, right: 80 },
    children: [new Paragraph({
      children: runs(text, { bold, size }),
      spacing: { after: 0, line: 240 },
    })],
  });

// --- a markdown pipe table -> docx Table -----------------------------------
function buildTable(lines) {
  const rows = lines
    .map(l => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim()))
    .filter((_, i) => i !== 1);                       // drop the |---| separator
  const n = Math.max(...rows.map(r => r.length));
  // the label column carries the longest strings; give it extra and split the rest
  const label = n > 4 ? Math.round(PAGE_DXA * 0.22) : Math.floor(PAGE_DXA / n);
  const w = Math.floor((PAGE_DXA - label) / (n - 1));
  const widths = [label, ...Array(n - 1).fill(w)];
  widths[n - 1] = PAGE_DXA - label - w * (n - 2);
  const fs = n >= 8 ? 16 : 18;                       // 8 pt when the table is wide
  return new Table({
    width: { size: PAGE_DXA, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((r, i) => {
      const padded = Array.from({ length: n }, (_, k) => r[k] ?? '');
      // a row whose only non-empty cell is bold is a sub-heading row
      const sub = i > 0 && padded.filter(c => c !== '').length === 1;
      return new TableRow({
        tableHeader: i === 0,
        children: padded.map((c, k) =>
          cell(c, { bold: i === 0 || sub, width: widths[k], shaded: i === 0, size: fs })),
      });
    }),
  });
}

// --- walk the markdown ------------------------------------------------------
const children = [];
for (let i = 0; i < md.length; i++) {
  const line = md[i];
  if (/^\|/.test(line)) {                             // table block
    const block = [];
    while (i < md.length && /^\|/.test(md[i])) block.push(md[i++]);
    i--;
    children.push(buildTable(block));
    children.push(new Paragraph({ text: '', spacing: { after: 200 } }));
  } else if (/^# /.test(line) && children.length === 0) {   // article title: not upper-cased
    children.push(new Paragraph({
      children: runs(line.slice(2), { bold: true, size: 30 }),
      spacing: { after: 320, line: 320 },
    }));
  } else if (/^# /.test(line)) {
    children.push(new Paragraph({
      children: runs(line.slice(2).toUpperCase(), { bold: true, size: 24 }),
      heading: HeadingLevel.HEADING_1,
      spacing: { before: 400, after: 200 },
    }));
  } else if (/^## /.test(line)) {
    children.push(new Paragraph({
      children: runs(line.slice(3), { bold: true, size: 22 }),
      heading: HeadingLevel.HEADING_2,
      spacing: { before: 280, after: 140 },
    }));
  } else if (/^- /.test(line)) {
    children.push(new Paragraph({
      children: runs(line.slice(2)),
      bullet: { level: 0 },
      spacing: { after: 100, line: 320 },
    }));
  } else if (/^\d+\. /.test(line)) {                  // reference list: plain, hanging indent
    children.push(new Paragraph({
      children: runs(line),
      spacing: { after: 100, line: 300 },
      indent: { left: 420, hanging: 420 },
    }));
  } else if (/^---\s*$/.test(line)) {
    children.push(new Paragraph({
      text: '',
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'CCCCCC' } },
      spacing: { before: 200, after: 200 },
    }));
  } else if (line.trim() === '') {
    continue;
  } else if (children.length === 0) {                 // title
    children.push(new Paragraph({
      children: runs(line.replace(/^#\s*/, ''), { bold: true, size: 32 }),
      spacing: { after: 300 },
    }));
  } else {
    children.push(body(line));
  }
}

const doc = new Document({
  styles: { default: { document: { run: { font: 'Times New Roman', size: 22 } } } },
  sections: [{
    properties: { page: { margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    children,
  }],
});

Packer.toBuffer(doc).then(b => {
  fs.writeFileSync(OUT, b);
  console.log('wrote', OUT, b.length, 'bytes');
});

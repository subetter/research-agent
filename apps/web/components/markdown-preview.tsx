"use client";

function escapeHtml(text: string) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(text: string) {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d+)\]/g, '<a class="md-cite" href="#ref-$1">[$1]</a>')
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderBlocks(markdown: string) {
  const lines = (markdown || "").replace(/\r\n/g, "\n").split("\n");
  const blocks: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    if (line.startsWith("> ")) {
      const quote = [];
      while (i < lines.length && lines[i].startsWith("> ")) { quote.push(lines[i].slice(2)); i += 1; }
      blocks.push(`<blockquote>${quote.map(item => `<p>${inline(item)}</p>`).join("")}</blockquote>`);
      continue;
    }
    if (line.startsWith("| ") && lines[i + 1]?.includes("---")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(lines[i].split("|").slice(1, -1).map(cell => cell.trim()));
        i += 1;
      }
      const head = rows.shift() || [];
      const body = rows.filter(row => !row.every(cell => /^:?-{3,}:?$/.test(cell)));
      blocks.push(`<div class="md-table-wrap"><table><thead><tr>${head.map(cell => `<th>${inline(cell)}</th>`).join("")}</tr></thead><tbody>${body.map(row => `<tr>${row.map(cell => `<td>${inline(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (line.startsWith("- ")) {
      const items = [];
      while (i < lines.length && lines[i].startsWith("- ")) { items.push(`<li>${inline(lines[i].slice(2))}</li>`); i += 1; }
      blocks.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !lines[i].startsWith("#") && !lines[i].startsWith("| ") && !lines[i].startsWith("- ") && !lines[i].startsWith("> ")) {
      para.push(lines[i]);
      i += 1;
    }
    const text = para.join(" ");
    const ref = /^\[(\d+)\]\s/.exec(text);
    blocks.push(`<p${ref ? ` id="ref-${ref[1]}"` : ""}>${inline(text)}</p>`);
  }
  return blocks.join("");
}

export default function MarkdownPreview({markdown}: {markdown: string}) {
  return <div className="md-preview" dangerouslySetInnerHTML={{__html: renderBlocks(markdown)}}/>;
}

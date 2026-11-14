import { Fragment, type ReactNode } from "react";

/* A small, safe Markdown renderer for assistant replies: paragraphs, bullet and
   numbered lists, headings, bold, italic and inline code. It builds React
   nodes, so nothing from the model is ever injected as HTML. */

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(\*\*([^*]+)\*\*|__([^_]+)__|`([^`]+)`|\*([^*\s][^*]*?)\*|_([^_\s][^_]*?)_)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = pattern.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = `${keyPrefix}-${i++}`;
    if (match[2] ?? match[3]) out.push(<strong key={key}>{match[2] ?? match[3]}</strong>);
    else if (match[4]) out.push(<code key={key}>{match[4]}</code>);
    else out.push(<em key={key}>{match[5] ?? match[6]}</em>);
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { type: "p"; text: string }
  | { type: "h"; text: string }
  | { type: "ul" | "ol"; items: string[] };

function parse(source: string): Block[] {
  // Models sometimes run a bullet list into one line ("open: * **A:** … * **B:** …").
  // Put each of those bullets on its own line first.
  const normalised = source
    .replace(/\r\n/g, "\n")
    .replace(/([^\n])[ \t]+([*•-])[ \t]+(?=\*\*)/g, "$1\n$2 ");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ type: "p", text: paragraph.join(" ") });
    paragraph = [];
  };
  for (const raw of normalised.split("\n")) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    const bullet = line.match(/^[*•-]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    if (heading) {
      flush();
      blocks.push({ type: "h", text: heading[1] });
    } else if (bullet || numbered) {
      flush();
      const type = bullet ? "ul" : "ol";
      const previous = blocks[blocks.length - 1];
      const item = (bullet || numbered)![1];
      if (previous && previous.type === type) previous.items.push(item);
      else blocks.push({ type, items: [item] });
    } else {
      const previous = blocks[blocks.length - 1];
      // A plain line straight after a list item continues that item.
      if (!paragraph.length && previous && (previous.type === "ul" || previous.type === "ol") && /^\s+/.test(raw)) {
        previous.items[previous.items.length - 1] += ` ${line}`;
      } else {
        paragraph.push(line);
      }
    }
  }
  flush();
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  const blocks = parse(text);
  return (
    <div className="md">
      {blocks.map((block, index) => {
        const key = `b${index}`;
        if (block.type === "p") return <p key={key}>{inline(block.text, key)}</p>;
        if (block.type === "h") return <p key={key} className="md-heading">{inline(block.text, key)}</p>;
        const items = block.items.map((item, i) => <li key={i}>{inline(item, `${key}-${i}`)}</li>);
        return block.type === "ul" ? <ul key={key}>{items}</ul> : <ol key={key}>{items}</ol>;
      })}
      {blocks.length === 0 ? <Fragment /> : null}
    </div>
  );
}

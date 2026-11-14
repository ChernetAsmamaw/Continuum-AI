import { Fragment, type ReactNode } from "react";

/**
 * Small, dependency-free Markdown renderer for assistant replies.
 *
 * Supports what the agent actually writes: paragraphs, bullet and numbered
 * lists, short headings, **bold**, *italic*, `code` and [links](https://…).
 * Everything is rendered as React elements, never as raw HTML, so model output
 * can't inject markup. Unknown syntax falls through as plain text.
 */

type Block =
  | { type: "p"; lines: string[] }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[]; start: number }
  | { type: "h"; level: number; text: string };

const BULLET = /^\s*(?:[-*•]|–)\s+(.*)$/;
const NUMBERED = /^\s*(\d{1,2})[.)]\s+(.*)$/;
const HEADING = /^\s*(#{1,4})\s+(.*)$/;

function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const heading = line.match(HEADING);
    if (heading) {
      blocks.push({ type: "h", level: heading[1].length, text: heading[2].trim() });
      i += 1;
      continue;
    }
    if (BULLET.test(line)) {
      const items: string[] = [];
      while (i < lines.length && BULLET.test(lines[i])) {
        items.push(lines[i].match(BULLET)![1]);
        i += 1;
        // Continuation lines (indented, not a new bullet) belong to the item.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i])) {
          items[items.length - 1] += ` ${lines[i].trim()}`;
          i += 1;
        }
      }
      blocks.push({ type: "ul", items });
      continue;
    }
    if (NUMBERED.test(line)) {
      const items: string[] = [];
      const start = Number(line.match(NUMBERED)![1]) || 1;
      while (i < lines.length && NUMBERED.test(lines[i])) {
        items.push(lines[i].match(NUMBERED)![2]);
        i += 1;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i])) {
          items[items.length - 1] += ` ${lines[i].trim()}`;
          i += 1;
        }
      }
      blocks.push({ type: "ol", items, start });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i]) && !HEADING.test(lines[i])) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: "p", lines: para });
  }
  return blocks;
}

// Inline: `code`, **bold**, *italic* / _italic_, [text](https://url). Code is
// matched first so markers inside it stay literal.
const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\b_[^_]+_\b)|(\[[^\]]+\]\((?:https?:\/\/|\/)[^)\s]+\))/;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let n = 0;
  while (rest.length) {
    const match = rest.match(INLINE);
    if (!match || match.index === undefined) {
      out.push(rest);
      break;
    }
    if (match.index > 0) out.push(rest.slice(0, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${n++}`;
    if (token.startsWith("`")) {
      out.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      out.push(<strong key={key}>{renderInline(token.slice(2, -2), key)}</strong>);
    } else if (token.startsWith("*") || token.startsWith("_")) {
      out.push(<em key={key}>{renderInline(token.slice(1, -1), key)}</em>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (link) {
        out.push(
          <a key={key} href={link[2]} target="_blank" rel="noreferrer noopener">
            {link[1]}
          </a>,
        );
      } else {
        out.push(token);
      }
    }
    rest = rest.slice(match.index + token.length);
  }
  return out;
}

function renderLines(lines: string[], keyPrefix: string): ReactNode[] {
  return lines.flatMap((line, index) => [
    ...(index > 0 ? [<br key={`${keyPrefix}-br-${index}`} />] : []),
    <Fragment key={`${keyPrefix}-l-${index}`}>{renderInline(line, `${keyPrefix}-${index}`)}</Fragment>,
  ]);
}

export function Markdown({ source, className }: { source: string; className?: string }) {
  const blocks = parseBlocks(source || "");
  return (
    <div className={className ? `md ${className}` : "md"}>
      {blocks.map((block, index) => {
        const key = `b${index}`;
        switch (block.type) {
          case "h": {
            // Keep headings small inside a chat bubble whatever level the model picked.
            const Tag = block.level <= 2 ? "h4" : "h5";
            return <Tag key={key}>{renderInline(block.text, key)}</Tag>;
          }
          case "ul":
            return (
              <ul key={key}>
                {block.items.map((item, i) => (
                  <li key={`${key}-${i}`}>{renderInline(item, `${key}-${i}`)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key} start={block.start}>
                {block.items.map((item, i) => (
                  <li key={`${key}-${i}`}>{renderInline(item, `${key}-${i}`)}</li>
                ))}
              </ol>
            );
          default:
            return <p key={key}>{renderLines(block.lines, key)}</p>;
        }
      })}
    </div>
  );
}

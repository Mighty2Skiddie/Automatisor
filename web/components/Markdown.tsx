import type { ReactNode } from "react";

/**
 * A four-case markdown renderer: paragraphs, `**bold**`, bullet lists, numbered lists.
 *
 * The agent's `answer` is a string field on a JSON schema, not a document, and it only
 * ever contains those four constructs. Pulling in a markdown library would add a
 * dependency plus an HTML-sanitising problem in order to implement four grammar rules,
 * so this parses them directly and emits real React elements.
 *
 * Everything it does not recognise — raw HTML included — is emitted as a text node. A
 * `<script>` in a model response is therefore inert by construction rather than by
 * sanitising, which is the difference between a guarantee and a filter that has to be
 * kept up to date. There is deliberately no `dangerouslySetInnerHTML` in this file.
 */

/** `- item`, `* item`, `+ item`. `**bold**` cannot match: no space follows the marker. */
const BULLET = /^\s*[-*+]\s+(.*)$/;

/** `1. item` or `1) item`, capped at three digits so a bare year cannot open a list. */
const NUMBERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;

/**
 * `## Section`. The compose prompt asks for "Markdown paragraphs" and does not forbid
 * headings, so a model that decides to structure a long note will occasionally emit one.
 * Recognising the marker costs a regex; not recognising it puts a literal `##` on screen.
 */
const HEADING = /^\s*#{1,6}\s+(.*)$/;

type Block =
  | { kind: "paragraph"; text: string; emphasised?: boolean }
  | { kind: "bullets"; items: string[] }
  | { kind: "numbers"; items: string[]; start: number };

function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  /**
   * Soft line breaks inside a paragraph join with a space, as markdown specifies. The
   * model wraps its prose at arbitrary widths; honouring those breaks would put ragged
   * line endings into a 66ch measure that has its own idea of where lines end.
   */
  const flushParagraph = (): void => {
    if (paragraph.length > 0) {
      blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
  };

  for (const line of source.replace(/\r\n?/g, "\n").split("\n")) {
    if (line.trim() === "") {
      flushParagraph();
      continue;
    }

    /*
     * Emitted as an emphasised paragraph rather than a real <h4>. The caller already
     * owns the heading hierarchy around this text — "Key points" and "Caveats" are
     * <h3>s that follow it — and injecting model-authored headings into that outline
     * would give a screen reader a document structure the page does not actually have.
     */
    const heading = HEADING.exec(line);
    if (heading) {
      flushParagraph();
      const text = heading[1].trim();
      // A bare `##` with no text is a marker, not content: drop it rather than emit an
      // empty paragraph that still claims its bottom margin.
      if (text !== "") blocks.push({ kind: "paragraph", text, emphasised: true });
      continue;
    }

    const bullet = BULLET.exec(line);
    if (bullet) {
      flushParagraph();
      const previous = blocks[blocks.length - 1];
      if (previous && previous.kind === "bullets") previous.items.push(bullet[1]);
      else blocks.push({ kind: "bullets", items: [bullet[1]] });
      continue;
    }

    const numbered = NUMBERED.exec(line);
    if (numbered) {
      flushParagraph();
      const previous = blocks[blocks.length - 1];
      if (previous && previous.kind === "numbers") previous.items.push(numbered[2]);
      else
        blocks.push({
          kind: "numbers",
          items: [numbered[2]],
          start: Number(numbered[1]),
        });
      continue;
    }

    paragraph.push(line.trim());
  }

  flushParagraph();
  return blocks;
}

/**
 * Split one line of text on `**…**`.
 *
 * An unterminated or empty `**` stays literal rather than swallowing the rest of the
 * paragraph — a truncated answer should lose its emphasis, not its remaining sentences.
 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let rest = text;
  let index = 0;

  while (rest.length > 0) {
    const open = rest.indexOf("**");
    if (open === -1) break;

    const close = rest.indexOf("**", open + 2);
    if (close === -1 || close === open + 2) {
      nodes.push(rest.slice(0, open + 2));
      rest = rest.slice(open + 2);
      continue;
    }

    if (open > 0) nodes.push(rest.slice(0, open));
    nodes.push(<strong key={`${keyPrefix}-s${index}`}>{rest.slice(open + 2, close)}</strong>);
    index += 1;
    rest = rest.slice(close + 2);
  }

  if (rest.length > 0) nodes.push(rest);
  return nodes;
}

export interface MarkdownProps {
  /** The raw answer text. */
  text: string;
  /** Applied to the wrapper, so the caller owns the typographic context. */
  className?: string;
}

export function Markdown({ text, className }: MarkdownProps) {
  const blocks = parseBlocks(text);
  if (blocks.length === 0) return null;

  return (
    /*
     * The trailing margin on the final block is removed here: `.answer-prose p` adds a
     * bottom margin to every paragraph, which would otherwise double the gap before
     * whatever divider the caller puts next and make the spacing scale look accidental.
     */
    <div className={className ? `${className} [&>:last-child]:mb-0` : "[&>:last-child]:mb-0"}>
      {blocks.map((block, blockIndex) => {
        const key = `b${blockIndex}`;

        if (block.kind === "paragraph") {
          return (
            <p key={key} className={block.emphasised ? "font-semibold" : undefined}>
              {renderInline(block.text, key)}
            </p>
          );
        }

        if (block.kind === "bullets") {
          return (
            <ul key={key} className="my-2 ml-5 list-disc">
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ul>
          );
        }

        return (
          <ol
            key={key}
            start={block.start}
            className="my-2 ml-5"
            /*
             * `.answer-prose ol` in globals.css sets `list-style: disc`, and because that
             * rule is unlayered it outranks any Tailwind utility regardless of
             * specificity. An inline style is the only way to restore the numerals
             * without editing a file this component does not own.
             */
            style={{ listStyleType: "decimal" }}
          >
            {block.items.map((item, itemIndex) => (
              <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
            ))}
          </ol>
        );
      })}
    </div>
  );
}

export default Markdown;

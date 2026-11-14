"""Table -> Markdown in grid and record modes."""
from dataclasses import replace

from .config import CODE_CELL_FENCE_MIN_LINES
from .inline import InlineWriter, code_fence, code_span, render_spans
from .model import Cell, CodeBlock, Image, ListItem, Paragraph, Table


def _image(src: str, context: str) -> str:
    return f"![Figure in section {context.replace(']', ')')}]({src})"


def cell_text(cell: Cell, writer: InlineWriter, context: str) -> str:
    """Render a cell's blocks as one line, parts joined with <br> (no pipe escaping)."""
    parts: list[str] = []
    for block in cell.blocks:
        if isinstance(block, Paragraph):
            parts.append(render_spans(block.spans, writer, at_line_start=False))
        elif isinstance(block, ListItem):
            text = render_spans(block.spans, writer, at_line_start=False)
            parts.append("• " + text if text else "")
        elif isinstance(block, CodeBlock):
            parts.append("<br>".join(code_span(ln.strip()) for ln in block.lines if ln.strip()))
        elif isinstance(block, Image):
            parts.append(_image(block.src, context))
    return "<br>".join(p for p in parts if p)


def _unbold(cell: Cell) -> Cell:
    """Copy of cell with bold cleared, for texts that the caller wraps in emphasis itself."""
    blocks = [replace(b, spans=[replace(s, bold=False) for s in b.spans])
              if isinstance(b, (Paragraph, ListItem)) else b for b in cell.blocks]
    return replace(cell, blocks=blocks)


def _is_record(table: Table) -> bool:
    return any(isinstance(b, CodeBlock) and len(b.lines) >= CODE_CELL_FENCE_MIN_LINES
               for row in table.rows for cell in row for b in cell.blocks)


def _gfm_line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _segment(rows: list[list[Cell]], texts: list[list[str]], ncols: int) -> list[str]:
    """Render a run of non-band rows as a GFM table."""
    counts = {len(r) for r in rows}
    if len(counts) == 1 and all(c.rowspan == 1 for r in rows for c in r):
        grid = [[t.replace("|", "\\|") for t in row] for row in texts]
        width = counts.pop()
    else:
        width = ncols
        occupied: dict[tuple[int, int], bool] = {}
        grid = []
        for r, (row, row_texts) in enumerate(zip(rows, texts)):
            out = [""] * width
            col = 0
            for cell, text in zip(row, row_texts):
                while occupied.get((r, col)):
                    col += 1
                if col < width:
                    out[col] = text.replace("|", "\\|")
                for dr in range(cell.rowspan):
                    for dc in range(cell.colspan):
                        occupied[(r + dr, col + dc)] = True
                col += cell.colspan
            grid.append(out)
    if any(c.is_header for c in rows[0]):
        header, data = grid[0], grid[1:]
    else:
        header, data = [""] * width, grid
    return [_gfm_line(header), _gfm_line(["---"] * width), *(_gfm_line(row) for row in data)]


def _grid(table: Table, writer: InlineWriter, context: str) -> list[list[str]]:
    texts = [[cell_text(c, writer, context) for c in row] for row in table.rows]
    keep = [i for i, row in enumerate(texts) if any(row)]
    rows = [table.rows[i] for i in keep]
    texts = [texts[i] for i in keep]
    if not rows:
        return []
    ncols = max(sum(c.colspan for c in row) for row in rows)

    def is_band(row: list[Cell]) -> bool:
        """A band is one cell spanning the whole grid (every row when ncols == 1)."""
        return ncols == 1 or (len(row) == 1 and row[0].colspan == ncols)

    items: list[list[str]] = []
    i = 0
    while i < len(rows):
        row, text = rows[i], texts[i]
        if is_band(row):
            label = cell_text(_unbold(row[0]), writer, context) if row[0].is_header else ""
            if row[0].is_header and i + 1 < len(rows) and is_band(rows[i + 1]) \
                    and not rows[i + 1][0].is_header:
                items.append([f"**{label.rstrip(':')}:** {texts[i + 1][0]}"])
                i += 2
                continue
            items.append([f"**{label}**" if row[0].is_header else text[0]])
            i += 1
            continue
        j = i
        while j < len(rows) and not is_band(rows[j]):
            j += 1
        items.append(_segment(rows[i:j], texts[i:j], ncols))
        i = j
    return items


def _record(table: Table, writer: InlineWriter, context: str) -> list[list[str]]:
    rows = table.rows
    header = None
    if rows and any(c.is_header for c in rows[0]):
        header = [cell_text(_unbold(c), writer, context) for c in rows[0]]
        rows = rows[1:]
    items: list[list[str]] = []
    for row in rows:
        if not row:
            continue
        first = cell_text(_unbold(row[0]), writer, context)
        items.append([f"**{header[0]}: {first}**" if header else f"**{first}**"])
        for j, cell in enumerate(row[1:], start=1):
            if header and j < len(header):
                items.append([f"*{header[j]}:*"])
            for block in cell.blocks:
                if isinstance(block, Paragraph):
                    text = render_spans(block.spans, writer)
                    if text:
                        items.append([text])
                elif isinstance(block, ListItem):
                    items.append(["- " + render_spans(block.spans, writer, at_line_start=False)])
                elif isinstance(block, CodeBlock):
                    items.append(code_fence(block.lines))
                elif isinstance(block, Image):
                    items.append([_image(block.src, context)])
    return items


def widest_table(lines: list[str]) -> int:
    """Number of columns of the widest rendered table in body lines (0 if none); fenced code is skipped."""
    widest, fence = 0, None
    for ln in lines:
        if fence is not None or ln.startswith(("```", "~~~~")):
            if fence is None:
                fence = "~~~~" if ln.startswith("~~~~") else "```"
            elif ln == fence:
                fence = None
        elif ln.startswith("| ---") and set(ln.replace("---", "").split()) == {"|"}:
            widest = max(widest, ln.count("---"))
    return widest


def render_table(table: Table, writer: InlineWriter, context: str) -> list[str]:
    """Render a Table as Markdown lines (grid or record mode)."""
    items = _record(table, writer, context) if _is_record(table) else _grid(table, writer, context)
    lines: list[str] = []
    for item in items:
        if lines:
            lines.append("")
        lines.extend(item)
    return lines

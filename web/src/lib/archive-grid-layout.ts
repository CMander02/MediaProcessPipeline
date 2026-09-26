const COVER_RATIO = 304 / 535

export function archiveGridLayout(width: number, height: number, infoHeight: number, viewportWidth: number) {
  const columnGap = viewportWidth >= 640 ? 20 : 12
  const minRowGap = viewportWidth >= 640 ? 16 : 12
  const desktop = viewportWidth >= 1024
  const baseColumns = desktop
    ? viewportWidth >= 1972 ? 7 : Math.max(1, Math.floor((width + columnGap) / (260 + columnGap)))
    : 2

  const measure = (columns: number) => {
    const cardWidth = (width - (columns - 1) * columnGap) / columns
    const cardHeight = Math.ceil((cardWidth * COVER_RATIO + infoHeight) * 64) / 64
    const rows = Math.max(1, Math.floor((height + minRowGap) / (cardHeight + minRowGap)))
    const remaining = Math.max(0, height - rows * cardHeight - (rows - 1) * minRowGap)
    return { columns, rows, cardWidth, cardHeight, remaining }
  }

  let layout = measure(baseColumns)
  // A denser desktop grid is useful only when it adds a row and meaningfully
  // reduces empty space. Always derive this from the baseline, not the last layout.
  if (viewportWidth >= 1280) {
    const denser = measure(baseColumns + 1)
    if (denser.cardWidth >= 220 && denser.rows > layout.rows
      && layout.remaining - denser.remaining > height * 0.08) layout = denser
  }

  return {
    columns: layout.columns,
    pageSize: layout.columns * layout.rows,
    rowGap: layout.rows > 1 ? minRowGap + layout.remaining / (layout.rows - 1) : minRowGap,
  }
}

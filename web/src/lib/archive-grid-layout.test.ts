import { describe, expect, it } from "vitest"
import { archiveGridLayout } from "./archive-grid-layout"

describe("archiveGridLayout", () => {
  it("preserves the 7 by 4 desktop baseline", () => {
    expect(archiveGridLayout(2016, 860, 48.1875, 2048)).toMatchObject({ columns: 7, pageSize: 28 })
  })

  it("adds a column when it fits another row into the unused height", () => {
    expect(archiveGridLayout(1685, 787, 48.1875, 1717)).toMatchObject({ columns: 7, pageSize: 28 })
    expect(archiveGridLayout(1685, 682, 48.1875, 1717)).toMatchObject({ columns: 6, pageSize: 18 })
  })

  it.each([[1024, 3], [768, 2], [430, 2], [390, 2]])("keeps responsive column counts at %ipx", (viewport, columns) => {
    expect(archiveGridLayout(viewport - 32, 700, 48.1875, viewport).columns).toBe(columns)
  })

  it("fits complete rows without overflow across resizing and font changes", () => {
    for (const width of [390, 768, 1024, 1373, 1717, 2048]) {
      for (const infoHeight of [48.1875, 65]) {
        for (let height = 300; height <= 1100; height += 17) {
          const gridWidth = width - 32
          const layout = archiveGridLayout(gridWidth, height, infoHeight, width)
          const rows = layout.pageSize / layout.columns
          const cardWidth = (gridWidth - (layout.columns - 1) * (width >= 640 ? 20 : 12)) / layout.columns
          const used = rows * (cardWidth * 304 / 535 + infoHeight) + (rows - 1) * layout.rowGap
          expect(used).toBeLessThanOrEqual(height + 0.01)
          if (rows > 1) expect(height - used).toBeLessThan(1)
        }
      }
    }
  })
})

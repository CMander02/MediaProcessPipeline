// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { TranscriptSegment } from "@/components/result/transcript-segment"

afterEach(cleanup)

function renderLine(readOnly = false) {
  const handlers = { onClick: vi.fn(), onEditStart: vi.fn() }
  render(
    <TranscriptSegment
      subtitle={{ index: 1, startTime: 6000, endTime: 9000, text: "Why are you doing AI at all?", speaker: "Dario Amodei" }}
      isActive={false}
      searchQuery=""
      readOnly={readOnly}
      editing={false}
      speakers={["Dario Amodei"]}
      onEdit={vi.fn()}
      onDelete={vi.fn()}
      onInsert={vi.fn()}
      onEditCancel={vi.fn()}
      {...handlers}
    />,
  )
  return handlers
}

describe("transcript lines", () => {
  it("seek on click and edit on double click", () => {
    const { onClick, onEditStart } = renderLine()
    const line = screen.getByText("Why are you doing AI at all?")
    fireEvent.click(line)
    expect(onClick).toHaveBeenCalled()
    fireEvent.doubleClick(line)
    expect(onEditStart).toHaveBeenCalledTimes(1)
  })

  it("stay read-only for other-language tracks", () => {
    const { onEditStart } = renderLine(true)
    fireEvent.doubleClick(screen.getByText("Why are you doing AI at all?"))
    expect(onEditStart).not.toHaveBeenCalled()
  })
})

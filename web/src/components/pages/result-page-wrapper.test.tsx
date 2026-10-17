/** @vitest-environment jsdom */
import { useState } from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { ResultPageWrapper } from "./result-page-wrapper"

const mocks = vi.hoisted(() => ({
  route: { page: "result", resultType: "task", resultId: "A", taskId: undefined as string | undefined, params: {} },
  get: vi.fn(),
}))
vi.mock("@/lib/router", () => ({ useRoute: () => mocks.route }))
vi.mock("@/lib/api", () => ({ api: { tasks: { get: mocks.get } } }))
vi.mock("./result-page-complete", () => ({ ResultPageComplete: ({ archivePath, taskId }: { archivePath: string; taskId: string }) => {
  const [mountedPath] = useState(archivePath)
  return <div data-testid="resolved">{JSON.stringify({ archivePath, taskId, mountedPath })}</div>
} }))
vi.mock("./result-page-live", () => ({ ResultPageLive: () => <div>live</div> }))
vi.mock("@/components/ui/page-state", () => ({ LoadingState: () => <div>loading</div> }))
beforeEach(() => {
  mocks.route = { ...mocks.route, resultType: "task", resultId: "A" }
  mocks.get.mockReset()
})
afterEach(cleanup)

it("ignores a late task response after navigating to another task", async () => {
  let resolveA!: (value: unknown) => void
  const delayedA = new Promise((resolve) => { resolveA = resolve })
  mocks.get.mockImplementation((id: string) => id === "A" ? delayedA : Promise.resolve({ result: { output_dir: "/B" } }))
  const { rerender } = render(<ResultPageWrapper />)
  mocks.route = { ...mocks.route, resultId: "B" }
  rerender(<ResultPageWrapper />)
  await waitFor(() => expect(screen.getByTestId("resolved").textContent).toContain("/B"))
  await act(async () => resolveA({ result: { output_dir: "/A" } }))
  expect(JSON.parse(screen.getByTestId("resolved").textContent!)).toMatchObject({ archivePath: "/B", taskId: "B" })
})

it("shows loading instead of pairing the previous archive with the new task", async () => {
  mocks.get.mockResolvedValueOnce({ result: { output_dir: "/A" } }).mockReturnValueOnce(new Promise(() => {}))
  const { rerender } = render(<ResultPageWrapper />)
  await waitFor(() => expect(screen.getByTestId("resolved")).toBeTruthy())
  mocks.route = { ...mocks.route, resultId: "B" }
  rerender(<ResultPageWrapper />)
  expect(screen.queryByTestId("resolved")).toBeNull()
  expect(screen.getByText("loading")).toBeTruthy()
})

it("remounts direct archive routes so drafts, media and task controls stay with their archive", () => {
  mocks.route = { ...mocks.route, resultType: "archive", resultId: "/A" }
  const { rerender } = render(<ResultPageWrapper />)
  mocks.route = { ...mocks.route, resultId: "/B" }
  rerender(<ResultPageWrapper />)
  expect(JSON.parse(screen.getByTestId("resolved").textContent!).mountedPath).toBe("/B")
})

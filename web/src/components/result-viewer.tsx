/**
 * Result viewer — delegates to the full ResultPage component.
 * Kept as a thin wrapper for backwards compatibility with App.tsx imports.
 */
import { ResultPage } from "./result/result-page"

export function ResultViewer() {
  return <ResultPage />
}

export function isBootstrapEntry(search = window.location.search) {
  return new URLSearchParams(search).get("bootstrap") === "1"
}

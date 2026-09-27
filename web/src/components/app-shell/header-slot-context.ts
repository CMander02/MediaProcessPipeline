import { createContext } from "react"

/** The part of the title bar a page can fill (the result page puts its title and actions there). */
export const HeaderSlotContext = createContext<HTMLElement | null>(null)

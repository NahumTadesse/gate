export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'gate-theme'

export function storedTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Private mode or blocked storage: the choice just won't persist.
  }
}

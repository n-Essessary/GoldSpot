import '@testing-library/jest-dom/vitest'

// jsdom: chart init uses ResizeObserver
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

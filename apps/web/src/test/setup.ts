import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
  value: () => {},
  writable: true
});

afterEach(() => {
  cleanup();
  if (typeof localStorage?.clear === 'function') {
    localStorage.clear();
  }
  vi.unstubAllGlobals();
});

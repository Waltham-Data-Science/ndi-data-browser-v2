import '@testing-library/jest-dom/vitest';

import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// Vitest doesn't auto-unmount between tests the way Jest+RTL does by default.
// Without this, every `render()` appends a new root, causing `getByTestId`
// to error with "Found multiple elements..." once any two tests in the same
// file render the same component.
afterEach(() => {
  cleanup();
});

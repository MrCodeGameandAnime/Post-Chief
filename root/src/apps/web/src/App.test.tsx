// @vitest-environment jsdom
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { App } from './App';

afterEach(cleanup);
test('identifies Post Chief and the organization on the landing screen', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'Post Chief', level: 1 })).toBeTruthy();
  expect(screen.getByText('Social publishing for 404 Builds.')).toBeTruthy();
});

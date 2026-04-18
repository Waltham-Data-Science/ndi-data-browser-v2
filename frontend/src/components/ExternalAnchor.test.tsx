import { describe, expect, it } from 'vitest';
import { render, within } from '@testing-library/react';

import { ExternalAnchor } from './ExternalAnchor';

describe('ExternalAnchor', () => {
  it('renders a safe https link with the canonical href', () => {
    const { container } = render(
      <ExternalAnchor href="https://doi.org/10.1/abc" label="DOI" />,
    );
    const anchor = within(container).getByRole('link', { name: /DOI/ });
    expect(anchor).toBeInTheDocument();
    expect(anchor.getAttribute('href')).toBe('https://doi.org/10.1/abc');
    expect(anchor.getAttribute('target')).toBe('_blank');
    expect(anchor.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('does not render an anchor for javascript: URLs', () => {
    const { container } = render(
      <ExternalAnchor href="javascript:alert(document.cookie)" label="DOI" />,
    );
    // No <a> with an href at all.
    expect(container.querySelector('a[href]')).toBeNull();
    expect(within(container).queryByRole('link')).toBeNull();
    // Label text is still shown so the user sees the data.
    expect(within(container).getByText('DOI')).toBeInTheDocument();
  });

  it('does not render an anchor for data: URLs', () => {
    const { container } = render(
      <ExternalAnchor href="data:text/html,<script>alert(1)</script>" label="DOI" />,
    );
    expect(container.querySelector('a[href]')).toBeNull();
    expect(within(container).getByText('DOI')).toBeInTheDocument();
  });

  it('does not render an anchor for vbscript: URLs', () => {
    const { container } = render(<ExternalAnchor href="vbscript:msgbox" label="DOI" />);
    expect(container.querySelector('a[href]')).toBeNull();
    expect(within(container).getByText('DOI')).toBeInTheDocument();
  });
});

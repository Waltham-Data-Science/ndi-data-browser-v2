/**
 * QueryBuilder — pins the amendment §4.B3 behavior change.
 *
 * The only test here is a regression tripwire for the MATLAB-contains
 * default. A new query condition must start with ``contains_string``
 * (case-insensitive substring match) — NOT ``exact_string`` / ``identical``
 * / any other operator. A silent flip of the default would lose matches
 * for researchers coming from the MATLAB tutorial convention.
 *
 * See Spike-0 Report C §7.6 and amendment §4.B3 for the spec rationale.
 * See the docstring on ``DEFAULT_QUERY_OPERATION`` in ``QueryBuilder.tsx``
 * for the user-facing story.
 *
 * Implementation-detail note: we assert the exported constant rather than
 * rendering the full component, because a render-level test would require
 * mocking ``useQueryOperations`` / ``useRunQuery`` / ``useSearchParams``
 * + TanStack Query + Router — fragile plumbing that could silently hide
 * the actual regression. The constant is the source of truth for the
 * default and is referenced once, in ``newCondition()``.
 */
import { describe, expect, it } from 'vitest';

import { DEFAULT_QUERY_OPERATION } from './QueryBuilder';

describe('QueryBuilder default operation', () => {
  it('defaults to contains_string (amendment §4.B3, Report C §7.6)', () => {
    expect(DEFAULT_QUERY_OPERATION).toBe('contains_string');
  });

  it('is NOT a stricter operator that would lose MATLAB-convention matches', () => {
    // Tripwire against silent inversion to exact_string / exact_string_anycase
    // / identical — all of which would narrow results in a way researchers
    // migrating from the MATLAB tutorial wouldn't expect.
    const stricterOperators = [
      'exact_string',
      'exact_string_anycase',
      'identical',
      'equals',
    ];
    expect(stricterOperators).not.toContain(DEFAULT_QUERY_OPERATION);
  });
});

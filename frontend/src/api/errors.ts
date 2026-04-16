export type Recovery = 'retry' | 'login' | 'contact_support' | 'none';

export type ErrorCode =
  | 'AUTH_REQUIRED'
  | 'AUTH_EXPIRED'
  | 'AUTH_INVALID_CREDENTIALS'
  | 'AUTH_RATE_LIMITED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'VALIDATION_ERROR'
  | 'RATE_LIMITED'
  | 'CLOUD_UNREACHABLE'
  | 'CLOUD_TIMEOUT'
  | 'CLOUD_INTERNAL_ERROR'
  | 'BINARY_DECODE_FAILED'
  | 'BINARY_NOT_FOUND'
  | 'QUERY_TIMEOUT'
  | 'QUERY_TOO_LARGE'
  | 'QUERY_INVALID_NEGATION'
  | 'BULK_FETCH_TOO_LARGE'
  | 'ONTOLOGY_LOOKUP_FAILED'
  | 'CSRF_INVALID'
  | 'INTERNAL';

export interface ApiErrorBody {
  error: {
    code: ErrorCode;
    message: string;
    recovery: Recovery;
    requestId: string | null;
    details?: unknown;
  };
}

export class ApiError extends Error {
  code: ErrorCode;
  recovery: Recovery;
  requestId: string | null;
  status: number;
  details: unknown;

  constructor(body: ApiErrorBody['error'], status: number) {
    super(body.message);
    this.code = body.code;
    this.recovery = body.recovery;
    this.requestId = body.requestId;
    this.status = status;
    this.details = body.details;
  }
}

const DEFAULT_API_URL = "http://localhost:8000";

export function getApiUrl(path: string): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_URL;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  return `${baseUrl.replace(/\/$/, "")}${normalizedPath}`;
}

export class ApiError extends Error {
  status: number;
  recovery: string;

  constructor(message: string, status: number, recovery: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.recovery = recovery;
  }
}

export async function requestJson<T>(
  path: string,
  options: RequestInit = {},
  recovery = "입력값을 확인한 뒤 다시 시도해 주세요.",
): Promise<T> {
  const response = await fetch(getApiUrl(path), {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let message = "요청을 처리하지 못했습니다.";
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      // A non-JSON upstream error still receives a concrete recovery path.
    }
    throw new ApiError(message, response.status, recovery);
  }
  return (await response.json()) as T;
}

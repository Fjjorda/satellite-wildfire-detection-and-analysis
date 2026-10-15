import { z } from 'zod';
import { assignmentsValid, jobSchema, uploadSchema } from './domain';
import type { AnalysisOptions, Composition, Job, UploadedImage } from './domain';

const origin = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
export function apiUrl(path: string): string { return `${origin}${path}`; }
export function assetUrl(jobId: string, filename: string): string {
  return apiUrl(`/api/analyses/${encodeURIComponent(jobId)}/files/${encodeURIComponent(filename)}`);
}

export class ApiError extends Error {
  constructor(public readonly code: string) { super(code); }
}

async function request<T>(path: string, schema: z.ZodType<T>, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try { response = await fetch(apiUrl(path), options); }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('network');
  }
  const body: unknown = await response.json();
  if (!response.ok) {
    const failure = z.object({ code: z.string() }).safeParse(body);
    throw new ApiError(failure.success ? failure.data.code : 'unknown');
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    console.error('Invalid API response', { path, issues: parsed.error.issues });
    throw new ApiError('invalid_response');
  }
  return parsed.data;
}

export function uploadImage(file: File, signal: AbortSignal): Promise<z.infer<typeof uploadSchema>> {
  const form = new FormData();
  form.append('file', file);
  return request('/api/uploads', uploadSchema, { method: 'POST', body: form, signal });
}
export function startAnalysis(images: UploadedImage[], options: AnalysisOptions, signal: AbortSignal): Promise<Job> {
  if (!assignmentsValid(images)) return Promise.reject(new ApiError('invalid_compositions'));
  const assignments: Partial<Record<Composition, string>> = {};
  for (const image of images) {
    if (image.assignment !== 'unused') assignments[image.assignment] = image.id;
  }
  return request('/api/analyses', jobSchema, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...assignments, max_side: options.max_side, smoke_recovery: true }), signal,
  });
}
export function getAnalysis(id: string, signal: AbortSignal): Promise<Job> {
  return request(`/api/analyses/${encodeURIComponent(id)}`, jobSchema, { signal });
}

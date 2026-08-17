import type {
  CandidatesResponse,
  Job,
  ResultDetail,
  ResultsResponse,
  RunStatus,
  SpecDocument,
  SpecRow,
  Template,
  UploadSlot,
} from "@/lib/types";

const API_KEY_STORAGE = "senthire_api_key";

export function getApiKey(): string {
  if (typeof window === "undefined") return "dev-local-key";
  return localStorage.getItem(API_KEY_STORAGE) ?? "dev-local-key";
}

export function setApiKey(value: string) {
  localStorage.setItem(API_KEY_STORAGE, value);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": getApiKey(),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  listTemplates: () => request<Template[]>("/templates"),
  listJobs: () => request<Job[]>("/jobs"),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  createJob: (title: string, templateSlug: string | null) =>
    request<Job>("/jobs", {
      method: "POST",
      body: JSON.stringify({ title, template_slug: templateSlug }),
    }),

  compileRequirements: (jobId: string, text: string) =>
    request<SpecRow>(`/jobs/${jobId}/requirements/compile`, {
      method: "POST",
      body: JSON.stringify({ natural_language_text: text }),
    }),
  listSpecs: (jobId: string) => request<SpecRow[]>(`/jobs/${jobId}/requirements`),
  getSpec: (specId: string) => request<SpecRow>(`/requirements/${specId}`),
  confirmSpec: (specId: string, spec: SpecDocument | null) =>
    request<SpecRow>(`/requirements/${specId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ spec }),
    }),

  requestUploads: (jobId: string, files: { filename: string; content_type: string }[]) =>
    request<{ uploads: UploadSlot[]; max_file_bytes: number }>(`/jobs/${jobId}/uploads`, {
      method: "POST",
      body: JSON.stringify({ files }),
    }),
  completeUploads: (jobId: string, files: { s3_key: string; filename: string }[]) =>
    request<{ enqueued: number }>(`/jobs/${jobId}/uploads/complete`, {
      method: "POST",
      body: JSON.stringify({ files }),
    }),
  jobCandidates: (jobId: string) => request<CandidatesResponse>(`/jobs/${jobId}/candidates`),

  listRuns: (jobId: string) =>
    request<
      {
        run_id: string;
        status: import("@/lib/types").RunPhase;
        mode: string;
        started_at: string | null;
        finished_at: string | null;
        funnel: { ranked?: number; total?: number };
      }[]
    >(`/jobs/${jobId}/runs`),
  startRun: (jobId: string) =>
    request<{ run_id: string; status: string; spec_version: number }>(`/jobs/${jobId}/runs`, {
      method: "POST",
      body: JSON.stringify({ mode: "interactive" }),
    }),
  runStatus: (runId: string) => request<RunStatus>(`/runs/${runId}`),
  runResults: (runId: string) => request<ResultsResponse>(`/runs/${runId}/results`),
  resultDetail: (runId: string, applicationId: string) =>
    request<ResultDetail>(`/runs/${runId}/results/${applicationId}`),
};

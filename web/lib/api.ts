import type {
  CandidatesResponse,
  InvitationLookup,
  Job,
  Me,
  Member,
  OrgInfo,
  PendingInvitation,
  ResultDetail,
  ResultsResponse,
  RunStatus,
  SpecDocument,
  SpecRow,
  Template,
  UploadSlot,
} from "@/lib/types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Auth is a server-side session cookie (HttpOnly), sent automatically on
 *  same-origin requests. On 401 we bounce to /login — except for the auth
 *  endpoints themselves, where the caller renders the error inline. */
async function request<T>(
  path: string,
  init?: RequestInit,
  opts?: { redirectOn401?: boolean },
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
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
    if (
      response.status === 401 &&
      (opts?.redirectOn401 ?? true) &&
      typeof window !== "undefined" &&
      !window.location.pathname.startsWith("/login")
    ) {
      window.location.assign("/login");
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  // --- auth & workspace ---
  me: () => request<Me>("/auth/me", undefined, { redirectOn401: false }),
  signup: (payload: { company_name: string; name: string; email: string; password: string }) =>
    request<Me>(
      "/auth/signup",
      { method: "POST", body: JSON.stringify(payload) },
      { redirectOn401: false },
    ),
  login: (email: string, password: string) =>
    request<Me>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ email, password }) },
      { redirectOn401: false },
    ),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  invitationLookup: (token: string) =>
    request<InvitationLookup>(`/auth/invitations/${token}`, undefined, { redirectOn401: false }),
  acceptInvitation: (token: string, payload: { name: string; password: string }) =>
    request<Me>(
      `/auth/invitations/${token}/accept`,
      { method: "POST", body: JSON.stringify(payload) },
      { redirectOn401: false },
    ),

  forgotPassword: (email: string) =>
    request<{ ok: boolean }>(
      "/auth/forgot-password",
      { method: "POST", body: JSON.stringify({ email }) },
      { redirectOn401: false },
    ),
  passwordResetLookup: (token: string) =>
    request<{ email_masked: string }>(`/auth/password-resets/${token}`, undefined, {
      redirectOn401: false,
    }),
  resetPassword: (token: string, password: string) =>
    request<Me>(
      `/auth/password-resets/${token}`,
      { method: "POST", body: JSON.stringify({ password }) },
      { redirectOn401: false },
    ),

  orgInfo: () => request<OrgInfo>("/org"),
  listMembers: () => request<Member[]>("/org/members"),
  listInvitations: () => request<PendingInvitation[]>("/org/invitations"),
  createInvitation: (email: string, role: "admin" | "member") =>
    request<PendingInvitation & { invite_url: string; email_queued: boolean }>(
      "/org/invitations",
      { method: "POST", body: JSON.stringify({ email, role }) },
    ),
  resendInvitation: (invitationId: string) =>
    request<PendingInvitation & { invite_url: string; email_queued: boolean }>(
      `/org/invitations/${invitationId}/resend`,
      { method: "POST" },
    ),
  revokeInvitation: (invitationId: string) =>
    request<void>(`/org/invitations/${invitationId}`, { method: "DELETE" }),
  updateMember: (userId: string, patch: { role?: "admin" | "member"; is_active?: boolean }) =>
    request<Member>(`/org/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  // --- product ---
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

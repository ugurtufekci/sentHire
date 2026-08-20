import type {
  AgendaItem,
  BillingDetails,
  BillingInfo,
  CandidatesResponse,
  CheckoutResponse,
  InvitationLookup,
  Job,
  JobInsights,
  Me,
  Member,
  OrgInfo,
  PendingInvitation,
  PipelineBoard,
  PipelineCard,
  PipelineEventKind,
  PipelineEventRow,
  ResultDetail,
  ResultsResponse,
  RunStatus,
  SpecDocument,
  SpecRow,
  Template,
  TimelineResponse,
  UploadSlot,
  Verdict,
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

  billingInfo: () => request<BillingInfo>("/billing"),
  saveBillingDetails: (details: BillingDetails) =>
    request<{ billing_details: BillingDetails }>("/billing/details", {
      method: "PUT",
      body: JSON.stringify(details),
    }),
  checkout: (planId: string) =>
    request<CheckoutResponse>("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ plan_id: planId }),
    }),
  cancelSubscription: () =>
    request<{ status: string }>("/billing/cancel", { method: "POST" }),

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
  startRun: (jobId: string, mode: "interactive" | "batch" = "interactive") =>
    request<{ run_id: string; status: string; spec_version: number }>(`/jobs/${jobId}/runs`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  runStatus: (runId: string) => request<RunStatus>(`/runs/${runId}`),
  runResults: (runId: string) => request<ResultsResponse>(`/runs/${runId}/results`),
  resultDetail: (runId: string, applicationId: string) =>
    request<ResultDetail>(`/runs/${runId}/results/${applicationId}`),

  overrideVerdict: (
    runId: string,
    applicationId: string,
    reqId: string,
    verdict: Verdict,
    reason: string | null,
  ) =>
    request<ResultDetail>(
      `/runs/${runId}/results/${applicationId}/requirements/${reqId}/override`,
      { method: "POST", body: JSON.stringify({ verdict, reason }) },
    ),

  // --- hiring pipeline ---
  pipelineBoard: (jobId: string) => request<PipelineBoard>(`/jobs/${jobId}/pipeline`),
  shortlist: (jobId: string, applicationIds: string[]) =>
    request<{ moved: number; skipped: number }>(`/jobs/${jobId}/pipeline/shortlist`, {
      method: "POST",
      body: JSON.stringify({ application_ids: applicationIds }),
    }),
  moveStage: (applicationId: string, stage: string, note?: string) =>
    request<PipelineCard>(`/applications/${applicationId}/stage`, {
      method: "PATCH",
      body: JSON.stringify({ stage, note: note ?? null }),
    }),
  updateApplication: (
    applicationId: string,
    patch: { owner_id?: string | null; next_action?: string | null; next_action_at?: string | null },
  ) =>
    request<PipelineCard>(`/applications/${applicationId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  addPipelineEvent: (
    applicationId: string,
    event: {
      kind: Exclude<PipelineEventKind, "stage_change">;
      note?: string | null;
      occurs_at?: string | null;
      detail?: Record<string, unknown>;
    },
  ) =>
    request<PipelineEventRow>(`/applications/${applicationId}/events`, {
      method: "POST",
      body: JSON.stringify(event),
    }),
  timeline: (applicationId: string) =>
    request<TimelineResponse>(`/applications/${applicationId}/timeline`),
  agenda: () => request<{ items: AgendaItem[] }>("/pipeline/agenda"),
  jobInsights: (jobId: string) => request<JobInsights>(`/jobs/${jobId}/insights`),
};

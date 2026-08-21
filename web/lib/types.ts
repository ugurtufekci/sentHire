// API response types — mirror src/senthire/api/routes/* response shapes.

export type Role = "admin" | "member";

export interface Me {
  user: { id: string; email: string; name: string; role: Role };
  org: { id: string; name: string };
}

export interface Member {
  id: string;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string | null;
}

export interface PendingInvitation {
  id: string;
  email: string;
  role: Role;
  created_at: string | null;
  expires_at: string;
}

export interface OrgInfo {
  id: string;
  name: string;
  seat_limit: number | null;
  active_members: number;
  pending_invitations: number;
}

export interface InvitationLookup {
  email: string;
  org_name: string;
  invited_by: string;
  expires_at: string;
}

export interface BillingPlan {
  id: string;
  name: string;
  monthly_price_try: number;
  cv_quota_per_month: number;
}

export interface BillingDetails {
  company_title: string;
  tax_number: string;
  tax_office: string;
  address: string;
  city: string;
}

export interface BillingInfo {
  plan: BillingPlan;
  status: "trial" | "pending_checkout" | "active" | "past_due" | "canceled";
  provider: string | null;
  usage: { period: string; used: number; quota: number; remaining: number };
  catalog: BillingPlan[];
  billing_details: BillingDetails | null;
  provider_mode: "mock" | "iyzico";
}

export type CheckoutResponse =
  | { mode: "mock"; status: "active"; plan: BillingPlan }
  | { mode: "iyzico"; token: string; checkout_html: string };

export interface Job {
  id: string;
  title: string;
  status: string;
  template_id: string | null;
  created_at: string | null;
}

export interface Template {
  id: string;
  slug: string;
  locale: string;
  title: string;
  requirement_count: number;
}

export type RequirementType = "hard" | "scored" | "bonus" | "penalty" | "disqualifier" | "info";
export type Verdict = "met" | "partially_met" | "not_met" | "unknown" | "disqualified";
export type InfoStatus = "explicit" | "inferred" | "ambiguous" | "missing";

export interface Requirement {
  req_id: string;
  category: string;
  label: Record<string, string>;
  type: RequirementType;
  importance: "critical" | "high" | "medium" | "low";
  evaluator: "deterministic" | "semantic" | "hybrid";
  deterministic?: {
    predicate: Record<string, unknown>;
    borderline_tolerance?: number | null;
    penalty_points?: number | null;
  } | null;
  semantic?: { rubric: string; target_field?: string | null } | null;
  missing_policy: "unknown" | "fail" | "ignore";
  weight_within_category: number;
  bonus_points?: number | null;
  clarification?: { question: string; default?: string | null; hr_answered?: boolean } | null;
  source?: { kind: string; original?: string | null } | null;
}

export interface SpecDocument {
  schema_version?: string;
  version: number;
  locale: string;
  weights: Record<string, number>;
  bonus_cap?: number;
  requirements: Requirement[];
  compliance?: { lint_passed?: boolean; flags?: ComplianceFlag[] } | null;
  compiler?: {
    model?: string;
    prompt_version?: string;
    back_translation?: { tr?: string; en?: string };
    clarifications?: { req_id: string; question: string; default?: string | null }[];
    compliance_flags?: ComplianceFlag[];
    warnings?: string[];
  } | null;
  error?: string;
}

export interface ComplianceFlag {
  original_text: string;
  issue: string;
  action: "blocked" | "rewritten";
  rewritten_to?: string | null;
}

export interface SpecRow {
  spec_id: string;
  job_id: string;
  version: number;
  status: "compiling" | "draft" | "confirmed" | "superseded" | "failed";
  created_at: string | null;
  confirmed_at: string | null;
  source_nl_text?: string | null;
  spec?: SpecDocument;
}

export interface UploadSlot {
  filename: string;
  s3_key: string;
  url: string;
  headers: Record<string, string>;
}

export interface FileRow {
  document_id: string;
  filename: string | null;
  parse_status: "pending" | "parsing" | "parsed" | "failed" | "unsupported";
  document_kind: string;
  error: string | null;
}

export interface ApplicationRow {
  application_id: string;
  status: string;
  candidate: { id: string; display_name: string | null };
  profile_summary: {
    total_experience_months: number | null;
    seniority: string | null;
    city: string | null;
    extraction_confidence: number | null;
  } | null;
}

export interface CandidatesResponse {
  job_id: string;
  funnel: Record<string, number>;
  files: FileRow[];
  applications: ApplicationRow[];
}

export type RunPhase =
  | "queued"
  | "screening"
  | "selecting"
  | "deep_analysis"
  | "scoring"
  | "complete"
  | "failed"
  | "cancelled";

export interface RunStatus {
  run_id: string;
  job_id: string;
  status: RunPhase;
  mode: string;
  funnel: {
    total?: number;
    memoized?: number;
    deep_pending?: string[];
    by_stage?: Record<string, number>;
    evaluated_so_far?: number;
    evaluated?: number;
    hard_failed?: number;
    deep_analyzed?: number;
    ranked?: number;
    error?: string;
    /** True when the run was produced by the offline demo models. */
    fake_models?: boolean;
    /** Per-criterion: how many distinct levels it produced across the cohort. */
    consistency?: {
      req_id: string;
      label: string;
      distinct_levels: number;
      levels?: number[];
      unknown: number;
      flag: "no_discrimination" | "mostly_unknown" | "all_unknown" | null;
    }[];
    batch?: Record<
      "light" | "deep",
      { id: string; submitted: number; polls?: number; status?: string } | undefined
    >;
  };
  cost: Record<
    string,
    {
      calls: number;
      input_tokens: number;
      output_tokens: number;
      cache_read_tokens: number;
      usd?: number;
      usd_saved?: number;
    }
  >;
  started_at: string | null;
  finished_at: string | null;
}

export interface ResultRow {
  /** Candidates sharing a group scored within a point of each other —
   *  the ranking orders them, but the difference is not a finding. */
  equivalent_group?: number;
  application_id: string;
  candidate: { id: string | null; display_name: string | null };
  rank: number | null;
  overall_score: number | null;
  band: string | null;
  hard_result: string;
  confidence: number | null;
  stage_reached: string;
  needs_review: boolean;
  headline: { strengths: string[]; weaknesses: string[]; summary: string | null };
  rejection_reasons?: RejectionReason[] | null;
}

export interface RejectionReason {
  req_id: string;
  label: string;
  verdict: string;
  evidence: { quote: string; page?: number | null }[];
}

export interface ResultsResponse {
  run_id: string;
  status: RunPhase;
  spec_version: number | null;
  results: ResultRow[];
  rejected?: ResultRow[];
}

export interface RequirementRow {
  req_id: string;
  label: Record<string, string>;
  category: string;
  type: RequirementType;
  verdict: Verdict;
  score: number | null;
  confidence: number;
  info_status: InfoStatus | null;
  /** Either a quote from the CV (model-judged) or the field a rule read. */
  evidence: {
    quote?: string;
    page?: number | null;
    field?: string;
    observed?: unknown;
    expected?: { op?: string; value?: unknown };
    present?: boolean;
  }[];
  source_stage: string | null;
  borderline: boolean;
  /** Which rung of the requirement's ladder this score sits on. */
  level_label?: string | null;
  /** The judge's own one-line explanation. */
  reasoning?: string | null;
}

export interface ResultDocument {
  stage_reached: string;
  gate: { status: "pass" | "fail"; failed: string[]; unverified: string[] };
  categories: Record<string, { score: number; weight: number; requirements: string[] }>;
  base_score: number;
  adjustments: { kind: "bonus" | "penalty"; req_id: string; points: number }[];
  final_score: number;
  band: string;
  confidence: number | null;
  needs_review: boolean;
  review_reasons: string[];
  missing_information: string[];
  requirements: RequirementRow[];
  rejection_reasons: RejectionReason[] | null;
  narrative: {
    strengths?: string[];
    weaknesses?: string[];
    red_flags?: string[];
    missing_information?: string[];
    summary?: string | null;
  };
  corrections: { req_id: string; from_verdict: string; to_verdict: string; note: string }[];
  /** Text in the CV addressed to the evaluator. Surfaced, never penalized. */
  integrity?: { kind: string; matched: string; quote: string }[];
  /** HR corrections to a verdict — the score below was recomputed from them. */
  human_overrides?: {
    req_id: string;
    from: string | null;
    to: string;
    reason: string | null;
    at: string | null;
  }[];
  deep_selection_reasons: string[];
  models_used: Record<string, string>;
}

export interface ResultDetail {
  application_id: string;
  rank: number | null;
  overall_score: number | null;
  band: string | null;
  hard_result: string;
  confidence: number | null;
  stage_reached: string;
  profile_version: number;
  spec_version: number;
  pipeline_version: string;
  models_used: Record<string, string>;
  result: ResultDocument;
}

// --- hiring pipeline ---

export type PipelineStage =
  | "new"
  | "shortlisted"
  | "contacted"
  | "interviewing"
  | "offer"
  | "hired"
  | "dropped";

export interface PipelineCard {
  application_id: string;
  candidate_name: string | null;
  candidate_email: string | null;
  stage: PipelineStage;
  stage_changed_at: string | null;
  owner_id: string | null;
  owner_name: string | null;
  next_action: string | null;
  next_action_at: string | null;
  score: number | null;
  band: string | null;
  rank: number | null;
}

export interface PipelineBoard {
  job_id: string;
  job_title: string;
  stages: PipelineStage[];
  tray: PipelineCard[];
  columns: Record<string, PipelineCard[]>;
  members: { id: string; name: string }[];
}

export type PipelineEventKind = "stage_change" | "note" | "contact" | "meeting" | "outcome";

export interface PipelineEventRow {
  id: string;
  kind: PipelineEventKind;
  actor_name: string | null;
  from_stage: string | null;
  to_stage: string | null;
  note: string | null;
  occurs_at: string | null;
  detail: { result?: "positive" | "negative" } & Record<string, unknown>;
  created_at: string | null;
}

export interface TimelineResponse extends PipelineCard {
  job_id: string;
  candidate_id: string;
  events: PipelineEventRow[];
}

export interface AgendaItem {
  application_id: string;
  job_id: string;
  job_title: string | null;
  candidate_name: string | null;
  stage: PipelineStage;
  next_action: string | null;
  next_action_at: string;
  overdue: boolean;
}

export interface JobInsights {
  job_id: string;
  corrections: {
    sample_size: number;
    requirements: {
      req_id: string;
      label: string;
      corrected: number;
      rate: number | null;
      directions: Record<string, number>;
    }[];
  };
  calibration: {
    sample_size: number;
    advanced: number;
    working_threshold: number | null;
    buckets: {
      from: number | null;
      to: number | null;
      count: number;
      advanced: number;
      hired: number;
      dropped: number;
      advance_rate: number;
    }[];
  };
  insights: { kind: string; severity: "info" | "notable"; message_tr: string; detail: unknown }[];
  min_sample: number;
}

export interface MessageTemplate {
  slug: string;
  name: string;
  subject: string;
  body: string;
  updated_at: string | null;
}

export interface MessagePreview {
  application_id: string;
  candidate_name: string | null;
  to_email: string | null;
  subject: string;
  body: string;
  blocked: string | null;
}

export interface SendResult {
  sent: { application_id: string; to_email: string; status: string }[];
  /** True when the interview invite carried a .ics calendar attachment. */
  calendar_attached?: boolean;
  skipped: {
    application_id: string;
    reason: string;
    sent_at?: string | null;
    needs_confirmation?: boolean;
  }[];
}

export interface SentMessage {
  id: string;
  template_slug: string | null;
  to_email: string;
  subject: string;
  body: string;
  status: string;
  error: string | null;
  sent_at: string | null;
  created_at: string | null;
}

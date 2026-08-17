// API response types — mirror src/senthire/api/routes/* response shapes.

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
  };
  cost: Record<
    string,
    { calls: number; input_tokens: number; output_tokens: number; cache_read_tokens: number }
  >;
  started_at: string | null;
  finished_at: string | null;
}

export interface ResultRow {
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
  evidence: { quote: string; page?: number | null }[];
  source_stage: string | null;
  borderline: boolean;
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

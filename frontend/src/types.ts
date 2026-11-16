export interface ImageAsset {
  id: string;
  url: string;
  width: number;
  height: number;
}
export interface User {
  id: string;
  nickname: string;
  email: string;
  role: "user" | "admin" | "moderator";
}
export interface AiGradingPoint {
  point: string;
  hit: boolean;
  note: string;
}
export interface AiGrading {
  result: "correct" | "incorrect";
  score: number;
  max_score: number;
  points: AiGradingPoint[];
  model: string;
  transcript?: string;
  created_at: string;
}
export interface AiKeyStatus {
  configured: boolean;
  hint: string;
  updated_at: string;
  encryption_ready: boolean;
  reason: string;
  available: boolean;
}
export interface Progress {
  result: "pending" | "correct" | "incorrect";
  result_source: string;
  ai_grading?: AiGrading | null;
  ever_correct: boolean;
  ever_incorrect: boolean;
  revision: number;
  problem_version: number;
  text: string;
  image_ids: string[];
  images: ImageAsset[];
  outdated: boolean;
  can_publish: boolean;
  last_at: string;
}
export interface Source {
  id: string;
  name: string;
  source_url: string;
  rights_note: string;
  verified: boolean;
}
export interface Answer {
  images: ImageAsset[];
  text: string;
  correct_options: string[];
  provenance: string;
}
export interface KnowledgePoint {
  code: string;
  name: string;
  domain: string;
}
export interface KnowledgeFilterOption extends KnowledgePoint {
  count: number;
}
export interface Problem {
  id: string;
  source_id: string;
  title: string;
  type: string;
  year: number;
  group_name: string;
  subject: string;
  tags: string[];
  knowledge_points: KnowledgePoint[];
  knowledge_codes?: string[];
  difficulty?: number | null;
  status: string;
  version: number;
  question_images: ImageAsset[];
  question_text: string;
  choice_options: string[];
  multiple: boolean;
  progress: Progress | null;
  answer_unlocked?: boolean;
  liked: boolean;
  source: Source;
  grading_mode: string;
  verified: boolean;
  answer_verified: boolean;
  answer?: Answer;
}
export interface Solution {
  id: string;
  user_id: string;
  problem_id: string;
  body: string;
  images: ImageAsset[];
  status: string;
  version: number;
  author: string;
  likes: number;
  liked: boolean;
  created_at: string;
  image_ids: string[];
}
export interface Comment {
  id: string;
  body: string;
  status: string;
  author: string;
  created_at: string;
}
export interface ForumPost {
  id: string;
  author: string;
  body: string;
  images: ImageAsset[];
  created_at: string;
  updated_at: string;
  problem: Pick<Problem, "id" | "title" | "type" | "year" | "tags">;
}
export interface Notification {
  id: string;
  event: string;
  is_read: boolean;
  created_at: string;
  target_id: string;
}
export interface Announcement {
  id: string;
  title: string;
  body: string;
  published: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
  is_read: boolean;
}
export interface ProblemFeedback {
  id: string;
  problem_id: string;
  problem_version: number;
  problem_title: string;
  current_version: number;
  kind: "incomplete" | "answer_mismatch" | "other";
  detail: string;
  status: "pending" | "resolved" | "dismissed";
  review_note: string;
  reporter: string;
  reviewer: string | null;
  created_at: string;
  reviewed_at: string | null;
}
export interface KnowledgeStat {
  code: string;
  name: string;
  domain: string;
  attempts: number;
  correct: number;
  incorrect: number;
  pending: number;
  mistakes: number;
  graded: number;
  accuracy: number | null;
  weakness: number;
}
export interface DomainStat {
  domain: string;
  name: string;
  attempts: number;
  correct: number;
  incorrect: number;
  accuracy: number | null;
}
export interface AnalysisTotals {
  practiced: number;
  correct: number;
  incorrect: number;
  pending: number;
  mistakes: number;
}
export interface VariantTotals {
  practiced: number;
  correct: number;
  incorrect: number;
}
export interface MockScore {
  paper_id: string;
  subject: string;
  title: string;
  score: number;
  max_score: number;
  submitted_at: string | null;
  knowledge: { code: string; name: string; score: number; max: number }[];
}
export interface AnalysisStats {
  totals: AnalysisTotals;
  variant_totals: VariantTotals;
  knowledge: KnowledgeStat[];
  weakest: KnowledgeStat[];
  strengths: KnowledgeStat[];
  domains: DomainStat[];
  mock: MockScore[];
  fingerprint: string;
}
export interface AnalysisReportItem {
  code: string;
  name: string;
  reason: string;
  suggestion?: string;
}
export interface AnalysisReport {
  summary: string;
  strengths: AnalysisReportItem[];
  weaknesses: AnalysisReportItem[];
  plan: string[];
  model?: string;
  created_at?: string;
}
export interface Analysis {
  stats: AnalysisStats;
  report: AnalysisReport | null;
  available: boolean;
  reason: string;
}
export interface MockKnowledge {
  code: string;
  name: string;
  count: number;
  weight: number;
}
export interface MockSubject {
  subject: string;
  problems: number;
  years: number[];
  type_mix: Record<string, number>;
  difficulty: { mean: number; sample: number };
  knowledge: MockKnowledge[];
  ready: boolean;
}
export interface MockSubjectsResponse {
  subjects: MockSubject[];
  ai: boolean;
  ai_key: boolean;
}
export interface MockItem {
  id: string;
  position: number;
  type: string;
  knowledge_points: string[];
  difficulty: number;
  points: number;
  origin: "bank" | "ai";
  question_text: string;
  choice_options: string[];
  multiple: boolean;
  model: string;
  result: "" | "correct" | "incorrect" | "pending";
  score: number;
  answer_text?: string;
  correct_options?: string[];
  explanation?: string;
  feedback?: string;
  my_text?: string;
  my_options?: string[];
}
export interface MockBlueprintSection {
  type: string;
  count: number;
  points_each: number;
  knowledge: string[];
  note: string;
}
export interface MockBlueprint {
  title?: string;
  duration_minutes?: number;
  sections: MockBlueprintSection[];
  emphasis?: { code: string; name: string; reason: string }[];
  rationale?: string;
  source?: string;
  model?: string;
}
export interface MockProfileSummary {
  years?: number[];
  problems?: number;
  type_mix?: Record<string, number>;
  knowledge?: MockKnowledge[];
  difficulty?: { mean: number; sample: number };
  welcome?: string;
}
export interface MockPaper {
  id: string;
  subject: string;
  title: string;
  status: "ready" | "submitted";
  blueprint: MockBlueprint;
  profile: MockProfileSummary;
  duration_minutes: number;
  max_score: number;
  score: number;
  items: MockItem[];
  knowledge_summary: {
    code: string;
    name: string;
    score: number;
    max: number;
    correct: number;
    count: number;
  }[];
  submitted_at: string | null;
  model: string;
  created_at: string;
  ai_items: number;
}
export interface MockPaperSummary {
  id: string;
  subject: string;
  title: string;
  status: "ready" | "submitted";
  score: number;
  max_score: number;
  duration_minutes: number;
  ai_items: number;
  created_at: string;
  submitted_at: string | null;
}
export interface Variant {
  id: string;
  problem_id: string;
  subject: string;
  type: string;
  knowledge_points: KnowledgePoint[];
  question_text: string;
  choice_options: string[];
  multiple: boolean;
  model: string;
  pooled?: boolean;
  result: "" | "correct" | "incorrect";
  created_at: string;
  answered_at: string | null;
  answer_text?: string;
  correct_options?: string[];
  explanation?: string;
}

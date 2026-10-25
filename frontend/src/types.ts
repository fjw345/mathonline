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
export interface Progress {
  result: "pending" | "correct" | "incorrect";
  result_source: string;
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
  correct_options: string[];
  provenance: string;
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
  status: string;
  version: number;
  question_images: ImageAsset[];
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

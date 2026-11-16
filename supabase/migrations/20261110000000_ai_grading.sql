-- AI grading keeps the raw model verdict in its own tables: a user
-- self-assessment changes only the progress record, so the graded snapshot
-- survives unchanged. Additive only; no existing table or policy is touched.
begin;

-- Rubric points are generated once per problem from its text answer.
create table public.problem_rubrics (
 problem_id text primary key references public.problems(id),
 answer_hash text not null,
 points jsonb not null,
 model text not null,
 created_at timestamptz not null
);

create table public.problem_grading (
 id text primary key,
 user_id text not null references public.profiles(id),
 problem_id text not null references public.problems(id),
 result text not null,
 score integer not null,
 max_score integer not null,
 points jsonb not null,
 model text not null,
 problem_version integer not null,
 created_at timestamptz not null,
 unique(user_id,problem_id)
);
create index grading_by_problem on public.problem_grading(problem_id);

alter table public.problem_rubrics enable row level security;
alter table public.problem_grading enable row level security;
revoke all on public.problem_rubrics, public.problem_grading
 from public, anon, authenticated;
grant select, insert, update, delete on public.problem_rubrics,
 public.problem_grading to service_role;

-- Make the new tables visible to PostgREST without waiting for the next DDL event.
notify pgrst, 'reload schema';

-- A user must be able to overturn an AI verdict, exactly like the local
-- guard in backend/domain.py: a pending result, or one sourced from AI, is
-- editable. Everything else in this function is the initial definition.
create or replace function public.record_progress(p_user text,p_problem text,p_text text,p_images jsonb,p_result text,p_revision integer,p_version integer,p_date date)
returns jsonb language plpgsql security definer set search_path=public as $$
declare old_record user_problem_progress; current_problem problems; output user_problem_progress;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_user||':'||p_problem,0));
 select * into current_problem from problems where id=p_problem for share;
 if current_problem.id is null or current_problem.status <> 'published' or current_problem.version<>p_version then raise exception 'stale problem'; end if;
 if p_result not in ('pending','correct','incorrect') then raise exception 'invalid result'; end if;
 select * into old_record from user_problem_progress where user_id=p_user and problem_id=p_problem for update;
 if p_revision is not null then
   if old_record.id is null or old_record.revision<>p_revision or old_record.problem_version<>p_version or (old_record.result<>'pending' and old_record.result_source<>'ai') then raise exception 'stale submission'; end if;
   p_text:=old_record.text;p_images:=old_record.image_ids;
 end if;
 insert into user_problem_progress values (
   p_user||':'||p_problem,p_user,p_problem,p_result,
   case when current_problem.grading_mode='choice' then 'automatic' else 'self_assessment' end,
   coalesce(old_record.ever_correct,false) or p_result='correct',coalesce(old_record.ever_incorrect,false) or p_result='incorrect',
   case when p_result='correct' then p_version else old_record.correct_version end,
   coalesce(p_revision,coalesce(old_record.revision,0)+1),p_version,p_text,p_images,now()
 ) on conflict(id) do update set
   result=excluded.result,result_source=excluded.result_source,ever_correct=excluded.ever_correct,ever_incorrect=excluded.ever_incorrect,correct_version=excluded.correct_version,
   revision=excluded.revision,problem_version=excluded.problem_version,text=excluded.text,image_ids=excluded.image_ids,last_at=excluded.last_at
 returning * into output;
 if p_result<>'pending' then
   update daily_assignments set completed_at=coalesce(completed_at,now()) where user_id=p_user and local_date=p_date and problem_id=p_problem;
 end if;
 return to_jsonb(output);
end $$;

commit;

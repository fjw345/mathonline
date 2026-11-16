-- Apply after the AI grading and image transcription migrations.
-- Commit progress, check-in and the AI snapshot together under the existing
-- per-user/problem transaction lock. No browser role may invoke this RPC.
begin;

alter table public.problem_grading
 add column submission_revision integer not null default 0;

create function public.record_ai_grading(
 p_user text, p_problem text, p_revision integer, p_version integer,
 p_date date, p_grading jsonb
) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
 current_state public.user_problem_progress;
 output public.user_problem_progress;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_user||':'||p_problem,0));
 select * into current_state from public.user_problem_progress
 where user_id=p_user and problem_id=p_problem for update;
 if p_revision is null or current_state.id is null
    or current_state.revision<>p_revision
    or current_state.problem_version<>p_version
    or current_state.result<>'pending' then
   raise exception 'stale submission';
 end if;
 if p_grading->>'result' is null
    or p_grading->>'result' not in ('correct','incorrect') then
   raise exception 'invalid grading result';
 end if;

 perform public.record_progress(
   p_user,p_problem,'','[]'::jsonb,p_grading->>'result',
   p_revision,p_version,p_date
 );
 update public.user_problem_progress set result_source='ai'
 where user_id=p_user and problem_id=p_problem returning * into output;

 insert into public.problem_grading(
   id,user_id,problem_id,result,score,max_score,points,model,
   problem_version,transcript,submission_revision,created_at
 ) values (
   p_user||':'||p_problem,p_user,p_problem,p_grading->>'result',
   (p_grading->>'score')::integer,(p_grading->>'max_score')::integer,
   p_grading->'points',p_grading->>'model',p_version,
   coalesce(p_grading->>'transcript',''),p_revision,now()
 ) on conflict(user_id,problem_id) do update set
   result=excluded.result,score=excluded.score,max_score=excluded.max_score,
   points=excluded.points,model=excluded.model,
   problem_version=excluded.problem_version,transcript=excluded.transcript,
   submission_revision=excluded.submission_revision,created_at=excluded.created_at;
 return to_jsonb(output);
end $$;

revoke all on function public.record_ai_grading(text,text,integer,integer,date,jsonb)
 from public,anon,authenticated;
grant execute on function public.record_ai_grading(text,text,integer,integer,date,jsonb)
 to service_role;

notify pgrst, 'reload schema';
commit;

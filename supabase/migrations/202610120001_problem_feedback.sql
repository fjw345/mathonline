-- Default publication; private user reports are reviewed after submission.
alter table public.problems alter column status set default 'published';
update public.problems set status='published'
where id like 'cmc-nonmath-%' and status='draft' and not manual_revision
and exists(select 1 from public.problem_answers where problem_id=problems.id and jsonb_array_length(images)>0);

create table public.problem_feedback (
 id text primary key,
 user_id text not null references public.profiles(id),
 problem_id text not null references public.problems(id),
 problem_version integer not null check(problem_version>0),
 kind text not null check(kind in ('incomplete','answer_mismatch','other')),
 detail text not null,
 status text not null check(status in ('pending','resolved','dismissed')),
 review_note text not null default '',
 reviewed_by text references public.profiles(id),
 created_at timestamptz not null,
 reviewed_at timestamptz
);
create index feedback_queue on public.problem_feedback(status,created_at desc);
alter table public.problem_feedback enable row level security;
revoke all on public.problem_feedback from anon,authenticated;
grant all on public.problem_feedback to service_role;

create or replace function public.save_problem(p_problem jsonb,p_answer jsonb,p_expected integer) returns jsonb language plpgsql security definer set search_path=public as $$
declare current_version integer; p problems; a problem_answers;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_problem->>'id',0));
 select version into current_version from problems where id=p_problem->>'id' for update;
 if coalesce(current_version,0)<>p_expected then raise exception 'stale problem'; end if;
 p:=jsonb_populate_record(null::problems,p_problem);a:=jsonb_populate_record(null::problem_answers,p_answer);
 if a.problem_id<>p.id then raise exception 'answer mismatch'; end if;
 if p.version<>p_expected+1 then raise exception 'invalid version'; end if;
 if p.status='published' and jsonb_array_length(a.images)=0 then raise exception 'answer images required'; end if;
 insert into problems select p.* on conflict(id) do update set
   source_id=excluded.source_id,title=excluded.title,type=excluded.type,year=excluded.year,group_name=excluded.group_name,subject=excluded.subject,tags=excluded.tags,difficulty=excluded.difficulty,status=excluded.status,version=excluded.version,
   grading_mode=excluded.grading_mode,question_images=excluded.question_images,choice_options=excluded.choice_options,multiple=excluded.multiple,verified=excluded.verified,answer_verified=excluded.answer_verified,manual_revision=excluded.manual_revision,updated_at=excluded.updated_at;
 insert into problem_answers select a.* on conflict(problem_id) do update set images=excluded.images,correct_options=excluded.correct_options,provenance=excluded.provenance;
 return to_jsonb(p);
end $$;

create function public.review_feedback(p_id text,p_actor text,p_status text,p_note text)
returns jsonb language plpgsql security definer set search_path=public as $$
declare output problem_feedback;
begin
 if p_status not in ('resolved','dismissed') then raise exception 'invalid status'; end if;
 update problem_feedback set status=p_status,review_note=p_note,reviewed_by=p_actor,reviewed_at=now()
 where id=p_id and status='pending' returning * into output;
 if output.id is null then raise exception 'stale feedback'; end if;
 insert into moderation_logs values(gen_random_uuid()::text,p_actor,p_id,'problem_feedback',p_status,p_note,now());
 return jsonb_build_object('ok',true);
end $$;
revoke all on function public.review_feedback(text,text,text,text) from public,anon,authenticated;
grant execute on function public.review_feedback(text,text,text,text) to service_role;

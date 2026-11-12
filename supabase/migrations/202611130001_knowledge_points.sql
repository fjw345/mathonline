-- Knowledge points (public filter + weak-point analysis) and the internal
-- difficulty used by mock-exam assembly. Additive only: question text, answer
-- text, images, versions and learning records are untouched.
begin;

alter table public.problems
  add column if not exists knowledge_points jsonb not null default '[]',
  add column if not exists knowledge_source text not null default '';

-- Contains queries for the knowledge filter; jsonb_path_ops supports @>.
create index if not exists problems_knowledge
  on public.problems using gin (knowledge_points jsonb_path_ops);

-- save_problem: an older client that omits the knowledge fields must not erase
-- existing tags, and a fresh insert must not violate the NOT NULL default.
-- Difficulty is preserved for the same reason: it is internal metadata used by
-- mock-exam assembly, not a rating shown to students.
create or replace function public.save_problem(p_problem jsonb,p_answer jsonb,p_expected integer) returns jsonb language plpgsql security definer set search_path=public as $$
declare current_version integer; p problems; a problem_answers;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_problem->>'id',0));
 select version into current_version from problems where id=p_problem->>'id' for update;
 if coalesce(current_version,0)<>p_expected then raise exception 'stale problem'; end if;
 p:=jsonb_populate_record(null::problems,p_problem);a:=jsonb_populate_record(null::problem_answers,p_answer);
 -- An older client that does not know the text/knowledge fields must not
 -- erase them.
 if not (p_problem ? 'question_text') then
  select question_text into p.question_text from problems where id=p.id;
 end if;
 if not (p_problem ? 'knowledge_points') then
  select knowledge_points into p.knowledge_points from problems where id=p.id;
 end if;
 if not (p_problem ? 'knowledge_source') then
  select knowledge_source into p.knowledge_source from problems where id=p.id;
 end if;
 if not (p_answer ? 'text') then
  select text into a.text from problem_answers where problem_id=a.problem_id;
 end if;
 p.question_text:=coalesce(p.question_text,'');
 p.knowledge_points:=coalesce(p.knowledge_points,'[]'::jsonb);
 p.knowledge_source:=coalesce(p.knowledge_source,'');
 a.text:=coalesce(a.text,'');
 if a.problem_id<>p.id then raise exception 'answer mismatch'; end if;
 if p.version<>p_expected+1 then raise exception 'invalid version'; end if;
 if p.status='published' and jsonb_array_length(a.images)=0 then raise exception 'answer images required'; end if;
 insert into problems select p.* on conflict(id) do update set
   source_id=excluded.source_id,title=excluded.title,type=excluded.type,year=excluded.year,group_name=excluded.group_name,subject=excluded.subject,tags=excluded.tags,
   difficulty=coalesce(excluded.difficulty,problems.difficulty),status=excluded.status,version=excluded.version,
   grading_mode=excluded.grading_mode,question_images=excluded.question_images,question_text=excluded.question_text,
   knowledge_points=excluded.knowledge_points,knowledge_source=excluded.knowledge_source,
   choice_options=excluded.choice_options,multiple=excluded.multiple,verified=excluded.verified,answer_verified=excluded.answer_verified,manual_revision=excluded.manual_revision,updated_at=excluded.updated_at;
 insert into problem_answers select a.* on conflict(problem_id) do update set images=excluded.images,text=excluded.text,correct_options=excluded.correct_options,provenance=excluded.provenance;
 return to_jsonb(p);
end $$;

-- Bulk knowledge backfill used by scripts/tag_knowledge.py. Only the three
-- metadata columns change; no version bump, no updated_at change.
create or replace function public.apply_problem_knowledge(p_items jsonb)
returns integer language plpgsql security definer set search_path=public as $$
declare item jsonb; codes jsonb; updated integer:=0;
begin
 if jsonb_typeof(p_items)<>'array' then raise exception 'items must be an array'; end if;
 for item in select value from jsonb_array_elements(p_items) as t(value) loop
  codes:=coalesce(item->'knowledge_points','[]'::jsonb);
  if jsonb_typeof(codes)<>'array' then raise exception 'knowledge_points must be an array'; end if;
  if jsonb_array_length(codes)>4 then raise exception 'too many knowledge points'; end if;
  if length(coalesce(item->>'knowledge_source',''))>40 then raise exception 'knowledge source too long'; end if;
  perform pg_advisory_xact_lock(hashtextextended(item->>'id',0));
  update problems set
    knowledge_points=case when item ? 'knowledge_points' then codes else knowledge_points end,
    knowledge_source=case when item ? 'knowledge_source' then coalesce(item->>'knowledge_source','') else knowledge_source end,
    difficulty=case when item ? 'difficulty' then nullif(item->>'difficulty','')::integer else difficulty end
  where id=item->>'id';
  if found then updated:=updated+1; end if;
 end loop;
 return updated;
end $$;

revoke all on function public.apply_problem_knowledge(jsonb) from public,anon,authenticated;
grant execute on function public.apply_problem_knowledge(jsonb) to service_role;

-- Make the new columns and function visible to PostgREST immediately.
notify pgrst, 'reload schema';

commit;

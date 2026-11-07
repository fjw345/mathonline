-- Optional Markdown/LaTeX rendering for the existing image bank.
-- Additive and non-destructive: images, versions, learning records and manual
-- revisions are untouched. Applying text through apply_problem_texts() updates
-- only the two new columns.
alter table public.problems
  add column if not exists question_text text not null default '';
alter table public.problem_answers
  add column if not exists text text not null default '';

create or replace function public.save_problem(p_problem jsonb,p_answer jsonb,p_expected integer) returns jsonb language plpgsql security definer set search_path=public as $$
declare current_version integer; p problems; a problem_answers;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_problem->>'id',0));
 select version into current_version from problems where id=p_problem->>'id' for update;
 if coalesce(current_version,0)<>p_expected then raise exception 'stale problem'; end if;
 p:=jsonb_populate_record(null::problems,p_problem);a:=jsonb_populate_record(null::problem_answers,p_answer);
 -- An older client that does not know the text fields must not erase them.
 if not (p_problem ? 'question_text') then
   select question_text into p.question_text from problems where id=p.id;
 end if;
 if not (p_answer ? 'text') then
   select text into a.text from problem_answers where problem_id=a.problem_id;
 end if;
 p.question_text:=coalesce(p.question_text,'');
 a.text:=coalesce(a.text,'');
 if a.problem_id<>p.id then raise exception 'answer mismatch'; end if;
 if p.version<>p_expected+1 then raise exception 'invalid version'; end if;
 if p.status='published' and jsonb_array_length(a.images)=0 then raise exception 'answer images required'; end if;
 insert into problems select p.* on conflict(id) do update set
   source_id=excluded.source_id,title=excluded.title,type=excluded.type,year=excluded.year,group_name=excluded.group_name,subject=excluded.subject,tags=excluded.tags,difficulty=excluded.difficulty,status=excluded.status,version=excluded.version,
   grading_mode=excluded.grading_mode,question_images=excluded.question_images,question_text=excluded.question_text,choice_options=excluded.choice_options,multiple=excluded.multiple,verified=excluded.verified,answer_verified=excluded.answer_verified,manual_revision=excluded.manual_revision,updated_at=excluded.updated_at;
 insert into problem_answers select a.* on conflict(problem_id) do update set images=excluded.images,text=excluded.text,correct_options=excluded.correct_options,provenance=excluded.provenance;
 return to_jsonb(p);
end $$;

-- Fill text without changing version, updated_at or manual_revision. Existing
-- non-empty text is preserved by the caller unless it explicitly replaces it.
create or replace function public.apply_problem_texts(p_items jsonb)
returns integer language plpgsql security definer set search_path=public as $$
declare item jsonb; updated integer:=0;
begin
 if jsonb_typeof(p_items)<>'array' then raise exception 'items must be an array'; end if;
 for item in select value from jsonb_array_elements(p_items) as t(value) loop
  if length(coalesce(item->>'question_text',''))>50000 or length(coalesce(item->>'answer_text',''))>50000 then
   raise exception 'text too long';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(item->>'id',0));
  update problems set question_text=coalesce(item->>'question_text','') where id=item->>'id';
  if found then
   update problem_answers set text=coalesce(item->>'answer_text','') where problem_id=item->>'id';
   updated:=updated+1;
  end if;
 end loop;
 return updated;
end $$;

revoke all on function public.apply_problem_texts(jsonb) from public,anon,authenticated;
grant execute on function public.apply_problem_texts(jsonb) to service_role;

-- Make the new columns visible to PostgREST without waiting for the next DDL event.
notify pgrst, 'reload schema';

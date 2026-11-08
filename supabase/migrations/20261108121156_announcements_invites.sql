-- Business access remains server-only. No access from browser roles, including
-- direct RPC calls with forged user IDs; every function is SECURITY INVOKER.
begin;

create table public.announcements (
 id text primary key,
 author_id text not null references public.profiles(id),
 title text not null check (length(btrim(title)) between 1 and 100),
 body text not null check (length(btrim(body)) between 1 and 10000),
 published boolean not null default true,
 revision integer not null default 1 check (revision>0),
 created_at timestamptz not null,
 updated_at timestamptz not null
);
create index announcement_feed on public.announcements(published,created_at desc,id desc);
create index announcement_author on public.announcements(author_id);

create table public.announcement_reads (
 id text primary key,
 user_id text not null references public.profiles(id),
 announcement_id text not null references public.announcements(id),
 revision integer not null check(revision>0),
 unique(user_id,announcement_id)
);
create index announcement_reads_parent on public.announcement_reads(announcement_id);

create table public.invite_codes (
 id text primary key references public.profiles(id),
 code text not null unique check(code ~ '^[A-Z0-9]{10}$'),
 created_at timestamptz not null default now()
);
create table public.invite_registrations (
 id text primary key references public.profiles(id),
 inviter_id text not null references public.profiles(id),
 created_at timestamptz not null default now(),
 check(id<>inviter_id)
);
create index invite_registration_owner on public.invite_registrations(inviter_id);

alter table public.announcements enable row level security;
alter table public.announcement_reads enable row level security;
alter table public.invite_codes enable row level security;
alter table public.invite_registrations enable row level security;
revoke all on public.announcements, public.announcement_reads,
 public.invite_codes, public.invite_registrations from public, anon, authenticated;
grant select, insert, update, delete on public.announcements, public.announcement_reads,
 public.invite_codes, public.invite_registrations to service_role;

create function public.ensure_invite_code(p_user text,p_code text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare result public.invite_codes;
begin
 insert into public.invite_codes(id,code) values(p_user,p_code)
 on conflict(id) do nothing;
 select * into result from public.invite_codes where id=p_user;
 return to_jsonb(result);
end $$;

-- The backend validates the authenticated email through GoTrue /user before
-- calling this function. A unique invitee plus a profile row lock makes binding
-- immutable and idempotent across concurrent requests/serverless instances.
create function public.bind_invite(p_user text,p_inviter text)
returns text language plpgsql security invoker set search_path='' as $$
declare old_inviter text;
begin
 if p_user=p_inviter then raise exception 'self invitation'; end if;
 perform id from public.profiles where id=p_user for update;
 if not found then raise exception 'unknown user'; end if;
 select inviter_id into old_inviter from public.invite_registrations where id=p_user;
 if found then
  if old_inviter=p_inviter then return 'ok'; else return 'conflict'; end if;
 end if;
 if not exists(select 1 from public.invite_codes where id=p_inviter) then
  raise exception 'unknown inviter';
 end if;
 insert into public.invite_registrations(id,inviter_id) values(p_user,p_inviter);
 return 'ok';
end $$;

create function public.invite_status(p_user text)
returns jsonb language sql stable security invoker set search_path='' as $$
 select jsonb_build_object('confirmed',count(*),'pending',0)
 from public.invite_registrations where inviter_id=p_user;
$$;

create function public.announcement_page(p_user text,p_page integer,p_size integer,p_admin boolean)
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare result jsonb;
begin
 if p_page<1 or p_size<1 or p_size>50 then raise exception 'invalid page'; end if;
 if p_admin and not exists(select 1 from public.profiles where id=p_user and role='admin') then
  raise exception 'admin required';
 end if;
 select jsonb_build_object(
  'total',(select count(*) from public.announcements where p_admin or published),
  'items',coalesce((select jsonb_agg(to_jsonb(t)) from (
   select a.*,coalesce(r.revision,0)>=a.revision as is_read
   from public.announcements a left join public.announcement_reads r
   on r.announcement_id=a.id and r.user_id=p_user
   where p_admin or a.published
   order by a.created_at desc,a.id desc limit p_size offset (p_page-1)*p_size
  ) t),'[]'::jsonb)
 ) into result;
 return result;
end $$;

create function public.save_announcement(p_item jsonb,p_expected integer)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare old_revision integer; result public.announcements;
begin
 if not exists(select 1 from public.profiles where id=p_item->>'author_id' and role='admin') then
  raise exception 'admin required';
 end if;
 -- Serialize creation and concurrent revisions by the existing/new UUID.
 perform pg_advisory_xact_lock(hashtextextended(p_item->>'id',0));
 select revision into old_revision from public.announcements where id=p_item->>'id' for update;
 if coalesce(old_revision,0)<>p_expected then return null; end if;
 insert into public.announcements(id,author_id,title,body,published,revision,created_at,updated_at)
 values(p_item->>'id',p_item->>'author_id',p_item->>'title',p_item->>'body',
  (p_item->>'published')::boolean,p_expected+1,(p_item->>'created_at')::timestamptz,(p_item->>'updated_at')::timestamptz)
 on conflict(id) do update set author_id=excluded.author_id,title=excluded.title,
 body=excluded.body,published=excluded.published,revision=excluded.revision,updated_at=excluded.updated_at
 returning * into result;
 return to_jsonb(result);
end $$;

revoke all on function public.ensure_invite_code(text,text), public.bind_invite(text,text),
 public.invite_status(text), public.announcement_page(text,integer,integer,boolean),
 public.save_announcement(jsonb,integer) from public,anon,authenticated;
grant execute on function public.ensure_invite_code(text,text), public.bind_invite(text,text),
 public.invite_status(text), public.announcement_page(text,integer,integer,boolean),
 public.save_announcement(jsonb,integer) to service_role;
notify pgrst, 'reload schema';
commit;

-- 用户自带 DeepSeek API 密钥：只保存 AES-GCM 密文与末四位掩码，不保存明文。
-- 纯新增表；RLS 开启，只有 service_role 可以读写，接口从不返回完整密钥。
begin;

create table public.user_ai_keys (
 user_id text primary key references public.profiles(id) on delete cascade,
 ciphertext text not null,
 hint text not null default '',
 created_at timestamptz not null,
 updated_at timestamptz not null
);

alter table public.user_ai_keys enable row level security;
revoke all on public.user_ai_keys from public, anon, authenticated;
grant select, insert, update, delete on public.user_ai_keys to service_role;

notify pgrst, 'reload schema';

commit;

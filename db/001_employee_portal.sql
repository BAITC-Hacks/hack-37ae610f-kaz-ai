-- Draft PostgreSQL schema for a future private employee portal.
-- Do not run against production without an auth provider, tenant checks and a migration review.
-- This schema stores neither passwords nor plaintext invitation/API tokens.

CREATE TABLE organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(trim(name)) > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE employees (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_subject text UNIQUE,
  display_name text NOT NULL CHECK (length(trim(display_name)) > 0),
  email text,
  phone_e164 text,
  status text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'active', 'disabled')),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (email IS NULL OR length(trim(email)) > 0),
  CHECK (phone_e164 IS NULL OR phone_e164 ~ '^\+[1-9][0-9]{7,14}$')
);

CREATE UNIQUE INDEX employees_email_unique ON employees (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX employees_phone_unique ON employees (phone_e164) WHERE phone_e164 IS NOT NULL;

CREATE TABLE memberships (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  employee_id uuid NOT NULL REFERENCES employees(id),
  role text NOT NULL CHECK (role IN ('admin', 'buyer', 'approver', 'viewer')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, employee_id)
);

CREATE TABLE invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  created_by uuid NOT NULL REFERENCES employees(id),
  destination_type text NOT NULL CHECK (destination_type IN ('email', 'phone', 'manual')),
  destination text,
  role text NOT NULL CHECK (role IN ('admin', 'buyer', 'approver', 'viewer')),
  token_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((destination_type = 'manual' AND destination IS NULL) OR
         (destination_type <> 'manual' AND destination IS NOT NULL)),
  CHECK (accepted_at IS NULL OR revoked_at IS NULL)
);

CREATE INDEX invitations_open_by_org ON invitations (organization_id, expires_at)
  WHERE accepted_at IS NULL AND revoked_at IS NULL;

CREATE TABLE employee_preferences (
  employee_id uuid PRIMARY KEY REFERENCES employees(id),
  language text NOT NULL DEFAULT 'ru' CHECK (language IN ('ru', 'kk')),
  coverage_days smallint NOT NULL DEFAULT 30 CHECK (coverage_days IN (14, 30, 45, 60)),
  default_supplier_code text,
  orders_only boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE organization_settings (
  organization_id uuid PRIMARY KEY REFERENCES organizations(id),
  approval_required boolean NOT NULL DEFAULT true,
  source_timezone text NOT NULL DEFAULT 'Asia/Almaty',
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE suppliers (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  code text NOT NULL,
  name text NOT NULL,
  lead_time_days integer CHECK (lead_time_days >= 0),
  safety_days integer CHECK (safety_days >= 0),
  PRIMARY KEY (organization_id, code)
);

CREATE TABLE products (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  code text NOT NULL,
  supplier_code text NOT NULL,
  name text NOT NULL,
  category text,
  moq integer CHECK (moq > 0),
  active boolean NOT NULL DEFAULT true,
  PRIMARY KEY (organization_id, code),
  FOREIGN KEY (organization_id, supplier_code) REFERENCES suppliers(organization_id, code)
);

CREATE TABLE source_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  source text NOT NULL CHECK (source IN ('demo', 'csv', '1c')),
  as_of date NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT now(),
  source_ref text,
  checksum_sha256 text,
  status text NOT NULL CHECK (status IN ('received', 'validated', 'rejected')),
  UNIQUE (id, organization_id)
);

CREATE TABLE recommendation_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  snapshot_id uuid NOT NULL,
  coverage_days smallint NOT NULL CHECK (coverage_days IN (14, 30, 45, 60)),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid REFERENCES employees(id),
  UNIQUE (id, organization_id),
  FOREIGN KEY (snapshot_id, organization_id) REFERENCES source_snapshots(id, organization_id)
);

CREATE TABLE recommendation_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL,
  organization_id uuid NOT NULL REFERENCES organizations(id),
  product_code text NOT NULL,
  forecast_quantity numeric(18,3) NOT NULL CHECK (forecast_quantity >= 0),
  free_stock numeric(18,3),
  inbound_quantity numeric(18,3),
  suggested_quantity numeric(18,3),
  review_status text NOT NULL CHECK (review_status IN ('ready', 'needs_stock', 'review_required')),
  explanation jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (id, organization_id),
  UNIQUE (run_id, product_code),
  FOREIGN KEY (run_id, organization_id) REFERENCES recommendation_runs(id, organization_id),
  FOREIGN KEY (organization_id, product_code) REFERENCES products(organization_id, code)
);

CREATE TABLE order_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  recommendation_item_id uuid NOT NULL,
  decided_by uuid NOT NULL REFERENCES employees(id),
  approved_by uuid REFERENCES employees(id),
  approved_quantity numeric(18,3) NOT NULL CHECK (approved_quantity > 0),
  status text NOT NULL CHECK (status IN ('draft', 'awaiting_approval', 'approved', 'rejected', 'exported')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (recommendation_item_id, organization_id) REFERENCES recommendation_items(id, organization_id)
);

CREATE TABLE integration_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  label text NOT NULL,
  key_hash text NOT NULL UNIQUE,
  scope text NOT NULL CHECK (scope IN ('read_snapshots', 'write_snapshots', 'read_orders')),
  created_by uuid NOT NULL REFERENCES employees(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);

CREATE TABLE integration_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  direction text NOT NULL CHECK (direction IN ('import', 'export')),
  external_id text NOT NULL,
  status text NOT NULL CHECK (status IN ('queued', 'processing', 'succeeded', 'failed')),
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (organization_id, direction, external_id)
);

CREATE TABLE audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  actor_employee_id uuid REFERENCES employees(id),
  action text NOT NULL,
  target_type text NOT NULL,
  target_id text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_events_by_org_time ON audit_events (organization_id, occurred_at DESC);

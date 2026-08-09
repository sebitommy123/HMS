import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";

import { createCatalog } from "@/api/catalogs";
import { ApiError } from "@/api/client";

// Curated list. Trino has many more, but for Phase 0 we only ship convenience
// for the connectors Core has actually been exercised against. Users can still
// type any value via the "Other (custom)" path.
const SUGGESTED_CONNECTORS = [
  "tpch",
  "tpcds",
  "memory",
  "jmx",
  "postgresql",
  "mysql",
  "mongodb",
  "kafka",
] as const;

const PROPERTY_HINTS: Record<string, readonly string[]> = {
  postgresql: ["connection-url", "connection-user", "connection-password"],
  mysql: ["connection-url", "connection-user", "connection-password"],
  mongodb: ["mongodb.connection-url"],
  kafka: ["kafka.nodes", "kafka.table-names"],
};

const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;
const CONNECTOR_PATTERN = /^[A-Za-z0-9_]+$/;

const PropertyEntry = z.object({
  key: z.string(),
  value: z.string(),
});

const FormSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "Required")
      .regex(NAME_PATTERN, "Only letters, digits, underscores, and hyphens"),
    connector: z
      .string()
      .trim()
      .min(1, "Required")
      .regex(CONNECTOR_PATTERN, "Only letters, digits, and underscores"),
    properties: z.array(PropertyEntry),
  })
  .superRefine((data, ctx) => {
    const seen = new Set<string>();
    data.properties.forEach((prop, i) => {
      if (!prop.key && prop.value) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["properties", i, "key"],
          message: "Required when a value is set",
        });
      }
      if (prop.key && seen.has(prop.key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["properties", i, "key"],
          message: "Duplicate key",
        });
      }
      if (prop.key) seen.add(prop.key);
    });
  });

type FormValues = z.infer<typeof FormSchema>;

export function NewCatalog() {
  const navigate = useNavigate();

  const {
    register,
    handleSubmit,
    control,
    watch,
    setError,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(FormSchema),
    defaultValues: {
      name: "",
      connector: "",
      properties: [{ key: "", value: "" }],
    },
  });

  const { fields, append, remove } = useFieldArray({
    control,
    name: "properties",
  });

  const connector = watch("connector");
  const hints = (connector && PROPERTY_HINTS[connector]) || [];

  const mutation = useMutation({
    mutationFn: createCatalog,
    onSuccess: (result) => {
      // 201 (all_ok) or 502 (broken): either way, the row was persisted and the
      // detail page is the right place to land.
      navigate(`/catalogs/${encodeURIComponent(result.catalog.name)}`);
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        // 502 means Core persisted the row but Trino rejected the CREATE.
        // The detail page is exactly where the operator wants to be — they need
        // to see the error and either fix the config (PATCH, when we add it)
        // or delete the row.
        if (
          err.status === 502 &&
          err.body &&
          typeof err.body === "object" &&
          "catalog" in err.body
        ) {
          const persisted = (err.body as { catalog: { name: string } }).catalog;
          navigate(`/catalogs/${encodeURIComponent(persisted.name)}`);
          return;
        }
        if (err.status === 409) {
          setError("name", { type: "server", message: "A catalog with this name already exists." });
          return;
        }
        if (err.status === 400 && err.body && typeof err.body === "object") {
          const details = (err.body as { details?: Array<{ loc: string[]; msg: string }> }).details;
          if (details) {
            for (const d of details) {
              const field = d.loc[d.loc.length - 1];
              if (field === "name" || field === "connector") {
                setError(field, { type: "server", message: d.msg });
              }
            }
            return;
          }
        }
      }
      // Fallback — surface a banner via the form-level error.
      setError("root", { type: "server", message: (err as Error).message ?? "Unknown error" });
    },
  });

  const onSubmit = handleSubmit((data) => {
    const propsObj: Record<string, string> = {};
    for (const p of data.properties) {
      if (p.key) propsObj[p.key] = p.value;
    }
    mutation.mutate({
      name: data.name.trim(),
      connector: data.connector.trim(),
      properties: propsObj,
    });
  });

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Breadcrumb />
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Register a catalog</h1>
        <p className="text-sm text-zinc-600">
          Core will persist the catalog and synchronously apply{" "}
          <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">CREATE CATALOG</code>{" "}
          to Trino. If Trino rejects the configuration, the row is still saved with{" "}
          <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">status=broken</code>{" "}
          so you can see the error and fix it.
        </p>
      </header>

      <form
        onSubmit={onSubmit}
        className="space-y-6 rounded-lg border border-zinc-200 bg-white p-6"
        data-testid="new-catalog-form"
      >
        <Field label="Name" htmlFor="name" hint="Lowercase ASCII, underscores, hyphens." error={errors.name?.message}>
          <input
            id="name"
            type="text"
            className={inputClass}
            placeholder="prod_orders"
            autoComplete="off"
            spellCheck={false}
            {...register("name")}
          />
        </Field>

        <Field
          label="Connector"
          htmlFor="connector"
          hint="The Trino plugin to use. Common choices listed below."
          error={errors.connector?.message}
        >
          <Controller
            control={control}
            name="connector"
            render={({ field }) => (
              <>
                <input
                  id="connector"
                  list="connector-suggestions"
                  className={inputClass}
                  placeholder="tpch"
                  autoComplete="off"
                  spellCheck={false}
                  {...field}
                />
                <datalist id="connector-suggestions">
                  {SUGGESTED_CONNECTORS.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </>
            )}
          />
        </Field>

        <div className="space-y-2">
          <div className="flex items-end justify-between">
            <div>
              <label className="block text-sm font-medium text-zinc-900">Properties</label>
              <p className="text-xs text-zinc-500">
                Key/value pairs forwarded to Trino as <code>WITH (...)</code>. Leave empty for connectors that don't require any (e.g. <code>tpch</code>).
              </p>
            </div>
            <button
              type="button"
              onClick={() => append({ key: "", value: "" })}
              className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
              data-testid="add-property"
            >
              + Add
            </button>
          </div>

          {hints.length > 0 && (
            <div
              className="rounded bg-zinc-50 p-2 text-xs text-zinc-600"
              data-testid="property-hints"
            >
              Common for <code>{connector}</code>:{" "}
              {hints.map((h, i) => (
                <span key={h}>
                  <button
                    type="button"
                    className="text-zinc-900 underline underline-offset-2 hover:text-zinc-700"
                    onClick={() => {
                      // Try to fill the first empty key, else append.
                      const idx = fields.findIndex((_, i2) => {
                        const k = watch(`properties.${i2}.key`);
                        return !k;
                      });
                      if (idx === -1) {
                        append({ key: h, value: "" });
                      } else {
                        setValue(`properties.${idx}.key`, h);
                      }
                    }}
                  >
                    {h}
                  </button>
                  {i < hints.length - 1 && ", "}
                </span>
              ))}
            </div>
          )}

          <div className="space-y-2" data-testid="property-list">
            {fields.map((f, i) => {
              const fieldError = errors.properties?.[i]?.key?.message;
              return (
                <div key={f.id} className="flex items-start gap-2">
                  <div className="flex-1">
                    <input
                      type="text"
                      placeholder="key"
                      className={inputClass}
                      autoComplete="off"
                      spellCheck={false}
                      {...register(`properties.${i}.key`)}
                      data-testid={`property-key-${i}`}
                    />
                    {fieldError && <p className="mt-1 text-xs text-red-700">{fieldError}</p>}
                  </div>
                  <div className="flex-[2]">
                    <input
                      type="text"
                      placeholder="value"
                      className={inputClass}
                      autoComplete="off"
                      spellCheck={false}
                      {...register(`properties.${i}.value`)}
                      data-testid={`property-value-${i}`}
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => remove(i)}
                    disabled={fields.length === 1}
                    className="rounded border border-zinc-200 bg-white px-2 py-1.5 text-sm text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
                    aria-label={`Remove property ${i + 1}`}
                    data-testid={`remove-property-${i}`}
                  >
                    ✕
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {errors.root && (
          <div
            className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
            data-testid="form-error"
          >
            {errors.root.message}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-zinc-100 pt-4">
          <Link
            to="/catalogs"
            className="rounded px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={isSubmitting || mutation.isPending}
            className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="submit-button"
          >
            {isSubmitting || mutation.isPending ? "Registering…" : "Register catalog"}
          </button>
        </div>
      </form>
    </div>
  );
}

const inputClass =
  "block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400";

function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-zinc-900">
        {label}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-zinc-500">{hint}</p>}
      {error && (
        <p className="text-xs text-red-700" data-testid={`error-${htmlFor}`}>
          {error}
        </p>
      )}
    </div>
  );
}

function Breadcrumb() {
  return (
    <nav className="text-sm text-zinc-500">
      <Link to="/catalogs" className="hover:text-zinc-900">Catalogs</Link>
      <span className="mx-2">/</span>
      <span className="text-zinc-700">New</span>
    </nav>
  );
}

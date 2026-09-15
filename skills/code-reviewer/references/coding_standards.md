# Coding Standards

## Scope

This document covers the conventions, idioms, and style rules that experienced
engineers agree on but that a linter or formatter cannot fully enforce — the
judgment calls in *how* to write code well, not *how it's laid out on the
page*. It applies across the languages and stack this skill reviews:
TypeScript, JavaScript, Python, Swift, Kotlin, Go, and the surrounding stack
of React/Next.js, Node/Express, GraphQL, PostgreSQL, Docker, Kubernetes,
Terraform, and AWS/GCP/Azure.

**Formatting is explicitly out of scope.** Whitespace, line length, import
ordering, brace placement, quote style, trailing commas — delegate all of it
to the language's standard formatter and don't hand-enforce it in review:

| Language           | Formatter                  |
|---------------------|----------------------------|
| TypeScript/JavaScript | Prettier (+ ESLint for correctness rules) |
| Python               | Black (+ isort, Ruff)      |
| Go                   | gofmt / goimports          |
| Swift                | swift-format               |
| Kotlin               | ktlint                     |

If a formatter disagrees with a human about formatting, the formatter wins;
don't debate it in review. This document is about everything formatters
can't see: naming, structure, control flow, error handling, and API shape.

For PR-review judgment calls (is this correct, is it tested, is the API
well-designed, is error handling adequate, is it secure) see
`code_review_checklist.md`. This document is about how to *write* the code
in the first place.

---

## Universal Principles

These apply regardless of language.

### Small functions with a single, clear responsibility

A function should do one thing at one level of abstraction. If you need the
word "and" to describe what a function does ("validates the input and saves
it and sends an email"), it's three functions wearing a trenchcoat. Small
functions are easier to name accurately, easier to test in isolation, and
easier to read without holding the whole call stack in your head.

This is not a line-count rule — a 40-line function that does one cohesive
thing is fine; a 10-line function that mixes validation, I/O, and business
logic is not.

### Prefer early returns over deep nesting

Guard clauses that exit early keep the "main path" of a function at a single
indentation level and make invalid states impossible to fall through.

```typescript
// Avoid
function processOrder(order: Order) {
  if (order.isValid) {
    if (order.items.length > 0) {
      if (!order.isFulfilled) {
        // actual logic, 3 levels deep
      }
    }
  }
}

// Prefer
function processOrder(order: Order) {
  if (!order.isValid) return;
  if (order.items.length === 0) return;
  if (order.isFulfilled) return;
  // actual logic, 1 level deep
}
```

### Make illegal states unrepresentable

When the type system can rule out a bad state at compile time, use it,
rather than relying on a runtime check or a comment. A classic smell is a
set of optional fields that are only sometimes valid together:

```typescript
// Avoid: four booleans imply states that don't make sense
// (e.g. isLoading=true AND data present AND error present)
interface RequestState {
  isLoading: boolean;
  data?: User;
  error?: string;
}

// Prefer: a discriminated union — invalid combinations don't type-check
type RequestState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: User }
  | { status: 'error'; error: string };
```

The same idea applies to Kotlin sealed classes, Swift enums with associated
values, and Go's practice of returning a single meaningful value instead of
several that can contradict each other.

### Avoid boolean parameter flags that change behavior at the call site

A bare `true`/`false` argument is meaningless at the call site without
jumping to the function definition, and it invites "flag arguments" that
silently fork behavior inside one function.

```typescript
// Avoid — what does `true` mean here? Reader has to go check.
createUser(userData, true);

// Prefer — self-documenting at the call site, and extensible
createUser(userData, { sendWelcomeEmail: true });
```

The named-options form also scales: adding a second flag doesn't break
every existing call site's argument order, and it reads correctly without
opening the function definition. The same principle applies in Python
(keyword-only arguments), Kotlin (named arguments, which the language
supports natively), and Go (an options struct or functional options
pattern instead of a growing positional parameter list).

### Don't catch exceptions/errors you can't meaningfully handle

Catching an error only to log it and re-throw it (or swallow it) hides the
failure from whoever's actually equipped to deal with it, and often
destroys the stack trace or context in the process. Only catch where you
can do one of: recover with a real fallback, add context and rethrow, or
translate to a boundary-appropriate error (e.g. an HTTP 4xx/5xx at an API
handler). If you're not doing one of those, let it propagate.

```python
# Avoid — catches, does nothing useful, hides the failure
try:
    result = risky_operation()
except Exception as e:
    print(f"Error: {e}")
    return None

# Prefer — either handle it meaningfully, or don't catch it at all
result = risky_operation()  # let it propagate to a boundary that can act
```

### Log at the boundary, not at every layer

Logging the same error at every function in the call stack as it propagates
produces duplicate, noisy logs and makes root-causing harder, not easier.
Log once, where you have enough context to act (a request handler, a job
runner, a top-level `main`) — not in every intermediate function that
merely passes the error along.

### Comments explain WHY, not WHAT

Code already says what it does; a comment repeating that is noise a reader
has to read twice. Comments earn their keep when they capture information
the code can't: a non-obvious constraint, a workaround for a specific bug
or API quirk, a reason a "simpler" alternative was rejected.

```go
// Avoid
// increment i by 1
i++

// Prefer
// Stripe's webhook retries can arrive out of order, so we re-check
// the event timestamp rather than trusting delivery order.
if event.Timestamp.Before(lastProcessed) {
    return nil
}
```

---

## TypeScript / JavaScript

**Prefer `unknown` over `any` at boundaries, then narrow.** `any` disables
type checking for everything it touches, including code far from where it
was introduced. `unknown` forces the caller to narrow before use, which is
exactly what you want at the edges of your system (parsed JSON, API
responses, `catch` clause bindings).

```typescript
// Avoid
function parseConfig(raw: any): Config {
  return raw; // no safety at all
}

// Prefer
function parseConfig(raw: unknown): Config {
  if (!isConfig(raw)) throw new Error('Invalid config shape');
  return raw; // narrowed by the type guard
}
```

**Avoid non-null assertions (`!`) except where genuinely provably safe.**
`value!` tells the compiler "trust me," which is exactly the class of bug
type-checking exists to prevent. It's acceptable immediately after a check
the compiler can't see through (e.g. `array[array.length - 1]!` after
confirming non-empty), but not as a routine way to silence errors.

**Prefer `const` and immutable data patterns.** Reach for `let` only when a
binding genuinely needs to be reassigned; reach for `var` never. Prefer
returning new objects/arrays (spread, `.map`, `.filter`) over mutating
inputs — it makes data flow traceable and avoids aliasing bugs, especially
in React state and Redux-style stores where mutation silently breaks
re-renders.

**Async/await over raw `.then()` chains.** `async`/`await` reads top-to-
bottom like synchronous code and gives you normal `try`/`catch` for error
handling instead of `.catch()` branching logic. Reserve raw Promise
combinators for genuine concurrency (`Promise.all`, `Promise.allSettled`),
not sequencing.

**Explicit return types on exported functions.** Inference is fine for
internal/local variables, but an exported function's return type is part of
its public contract — write it explicitly so a change in implementation
that accidentally changes the inferred type is caught at the definition,
not at every call site.

**Avoid default exports except where the framework mandates them** (e.g.
Next.js `page.tsx`/`layout.tsx`, or a config file a tool expects to
`require`). Default exports have no fixed name, so every importer can call
the same thing something different, autocomplete can't suggest the correct
import name, and automated refactors/renames are less reliable. Named
exports keep the identifier consistent everywhere and make barrel files
(`export * from './x'`) actually useful.

---

## Python

**Type hints on public functions.** Hints on anything called from outside
its own module turn a class of bugs into editor/CI-time errors and serve as
living documentation. Internal helpers can be more lax, but a public
function, method, or dataclass field should be annotated.

**Avoid mutable default arguments — the classic gotcha.** Default argument
values are evaluated once, at function-definition time, not on each call.
A mutable default is therefore shared and mutated across every call that
doesn't pass its own value.

```python
# Avoid — the same list is reused (and grows!) across every call
def add_item(item, items=[]):
    items.append(item)
    return items

# Prefer — sentinel of None, initialize fresh inside the function
def add_item(item, items=None):
    if items is None:
        items = []
    items.append(item)
    return items
```

**Prefer dataclasses/NamedTuple over loose dict "bags of data."** A dict
with string keys gives up static checking, autocomplete, and a single
source of truth for what fields exist — typos in key names fail silently at
runtime instead of at definition time.

```python
# Avoid
def make_user(name, email):
    return {"name": name, "email": email, "is_active": True}

# Prefer
from dataclasses import dataclass

@dataclass
class User:
    name: str
    email: str
    is_active: bool = True
```

**Context managers (`with`) for anything with cleanup.** Files, locks,
DB connections/transactions, and network sockets should be acquired with
`with` so cleanup runs even on exception — don't rely on manual
`.close()` calls that get skipped when an exception is raised in between.

**f-strings over `.format()` / `%`-formatting.** f-strings are more
readable (the expression sits inline where it's used), and marginally
faster. Reserve `.format()` for cases needing a runtime-supplied template
string (e.g. i18n).

**Avoid `import *`.** It pollutes the namespace with everything the module
exports, makes it impossible to tell where a name came from by reading the
file, and can silently shadow builtins or other imports.

---

## Go

**Always check returned errors.** Discarding an error with `_ = err` (or
just not checking it) means the caller has no idea an operation failed, and
downstream code proceeds on data that may be zero-valued garbage. If an
error genuinely doesn't matter in a specific spot, that's rare enough to
deserve a comment explaining why, not a silent `_`.

**Wrap errors with context using `%w`, don't lose the chain.**
`fmt.Errorf("doing X: %w", err)` preserves the original error so callers
can still `errors.Is`/`errors.As` it, while adding the context needed to
understand where in the call stack it happened. `%v` (or string
concatenation) destroys that chain.

```go
// Avoid — caller can no longer detect the underlying error type
if err != nil {
    return fmt.Errorf("failed: %v", err)
}

// Prefer — chain preserved, context added
if err != nil {
    return fmt.Errorf("reading config file %q: %w", path, err)
}
```

**Prefer small, consumer-defined interfaces over upfront interfaces on the
implementer.** Idiomatic Go defines an interface at the point where it's
*consumed*, sized to exactly what that caller needs (often one or two
methods), rather than the producer pre-declaring a large interface that
every implementation must satisfy. This keeps interfaces minimal and avoids
coupling unrelated consumers to the same contract.

```go
// Prefer: defined next to the function that needs it, not next to the implementation
type UserFetcher interface {
    GetUser(ctx context.Context, id string) (*User, error)
}

func RenderProfile(f UserFetcher, id string) (string, error) { ... }
```

**Avoid goroutine leaks — every goroutine needs a clear owner and
cancellation path.** A goroutine launched with no way to stop it (no
`context.Context`, no channel close, no `WaitGroup`) will run forever or
until the process exits, silently holding memory and possibly blocking on a
channel no one will ever read. Pass `context.Context` as the first
parameter to any function that does I/O or can block, and select on
`ctx.Done()`.

**Table-driven tests as the idiom.** Rather than one test function per
case, define a slice/map of `{name, input, want}` structs and loop over it
with `t.Run(tt.name, ...)`. This keeps test cases readable as data, makes
adding a new case a one-line diff, and gives you per-case subtest output.

---

## Swift

**Prefer `let` over `var`.** Default to immutability; only use `var` when
a value genuinely needs to change after initialization. This mirrors the
`const`-by-default convention in TypeScript and communicates intent to the
reader without needing a comment.

**Avoid force-unwrap (`!`) and force-try (`try!`) outside of
provably-safe or test contexts.** Both crash the app on a `nil` or thrown
error with no recovery path. Prefer `guard let`/`if let` for optionals and
`do`/`catch` (or `try?` when a nil result is an acceptable outcome) for
throwing calls. Force-unwrap is defensible for `@IBOutlet`s guaranteed to
be connected, or in test code exercising a known-good fixture — not in
general application logic handling any kind of external input.

**Value types (`struct`) by default; reference types (`class`) only when
identity or shared mutation is genuinely needed.** Structs give you
value semantics (copies are independent, no aliasing bugs, thread-safer by
default) which is what most model/data types want. Reach for `class` when
you need reference identity (`===`), shared mutable state across owners, or
Objective-C interop/inheritance.

**Guard-let for early-exit unwrapping over nested `if let` pyramids.**
`guard let` keeps the happy path at the top level and makes the failure
case explicit and immediate, the same early-return principle as above
applied to optional unwrapping.

```swift
// Avoid — nested pyramid, happy path buried
func greet(_ user: User?) -> String {
    if let user = user {
        if let name = user.name {
            return "Hello, \(name)"
        }
    }
    return "Hello, stranger"
}

// Prefer
func greet(_ user: User?) -> String {
    guard let user = user, let name = user.name else {
        return "Hello, stranger"
    }
    return "Hello, \(name)"
}
```

---

## Kotlin

**Avoid `!!` (non-null assertion); prefer safe calls (`?.`) and
`requireNotNull`/`checkNotNull` with a message.** `!!` throws an opaque
`NullPointerException` with no context about which value was null or why
it mattered. If a null truly should be impossible at that point, assert it
loudly with a message that explains the invariant, so a violation is
debuggable:

```kotlin
// Avoid — throws NPE with no explanation if wrong
val name = user.profile!!.name

// Prefer — safe-call when null is a valid case
val name = user.profile?.name ?: "Unknown"

// Prefer — explicit, documented assertion when null truly can't happen
val profile = requireNotNull(user.profile) { "User ${user.id} loaded without a profile" }
```

**Data classes for value-holding types.** `data class` gives you
`equals`/`hashCode`/`toString`/`copy` for free, which is what you want for
anything that represents a value rather than an identity/behavior-bearing
object — DTOs, request/response bodies, view state.

**Sealed classes/interfaces for closed sets of states, not a loose enum
plus separate nullable fields.** A UI or result state machine expressed as
one enum plus several "only valid in some states" fields lets invalid
combinations compile. A sealed hierarchy makes each state carry exactly the
data it needs and nothing else:

```kotlin
// Avoid
data class UiState(
    val status: Status, // enum: LOADING, SUCCESS, ERROR
    val data: List<Item>? = null,   // only meaningful if SUCCESS
    val error: String? = null,      // only meaningful if ERROR
)

// Prefer
sealed interface UiState {
    object Loading : UiState
    data class Success(val data: List<Item>) : UiState
    data class Error(val message: String) : UiState
}
```

**`when`-expressions over `if`-else chains for multi-branch logic, with
exhaustiveness enforced.** `when` over a sealed type (or enum) without an
`else` branch is checked exhaustively by the compiler — adding a new
subtype/case forces every `when` over it to be updated, which is exactly
the safety net you want for the sealed-class pattern above. Where the
subject isn't a closed type, keep an explicit `else` branch rather than
letting a case fall through unhandled.

---

## API Design (REST / GraphQL)

**REST: consistent resource naming and versioning.** Use plural nouns for
collections (`/users`, not `/user` or `/getUsers`), nest sub-resources
under their parent (`/users/{id}/orders`), and version from the URL or a
header from day one (`/api/v1/...`) so breaking changes don't require a
big-bang migration of every consumer. Keep response envelopes consistent
across endpoints (e.g. always `{ data, error }` or always a bare resource —
don't mix).

**GraphQL: fields nullable by default.** A non-null (`!`) field is a
promise that the resolver can *never* fail to produce a value — if it ever
can (a downstream service call, a DB lookup that might miss), a single
failure there nulls out the entire parent object up to the nearest nullable
ancestor. Default to nullable and only mark non-null where the value is
truly always derivable (e.g. an `id` on an object that was just fetched by
that id).

**GraphQL: avoid N+1 via batching/dataloader.** A naive resolver for
`Post.author` that queries the DB once per post produces one query per item
in a list, not one query for the list. Batch same-shape lookups within a
single tick (DataLoader pattern) so `posts { author { name } }` issues one
batched `WHERE id IN (...)` instead of N individual queries.

**GraphQL: don't expose internal IDs or implementation details in the
schema.** Database primary keys, internal enum values, or storage-layer
field names leaking into the public schema make it impossible to change
internals later without a breaking schema change. Map internal
representations to a stable public schema shape at the resolver boundary.

---

## Database (PostgreSQL / Prisma / etc.)

**Migrations should be additive and backward-compatible where possible.**
A migration that drops a column or renames it in place breaks any
currently-running instance of the old code during a rolling deploy. Prefer
the expand/contract pattern: add the new column/table first (deploy), dual-
write or backfill, switch reads over (deploy), then drop the old column in
a later, separate migration once nothing references it.

**Index foreign keys and frequently-filtered/sorted columns.** Postgres
does not automatically index foreign key columns (unlike the primary key
side of the relationship); an unindexed FK means every join or cascade
check does a sequential scan. Also index columns that regularly appear in
`WHERE`, `ORDER BY`, or `JOIN ON` clauses on large tables.

**Avoid N+1 queries.** The ORM equivalent of the GraphQL N+1 problem:
looping over a list and issuing one query per item (e.g. lazy-loading a
relation inside a loop) instead of eager-loading (`include`/`JOIN`) or
batching. Watch for this especially with ORM lazy-loading defaults.

**Use explicit transactions for multi-statement invariants.** Any
operation where two or more statements must succeed or fail together (e.g.
debit one row, credit another; insert a parent and its children) belongs in
an explicit transaction — without one, a crash or concurrent read between
the statements can observe or persist a partially-applied state.

---

## Infrastructure as Code (Docker / Kubernetes / Terraform)

**Docker: pin base image versions, never `:latest`.** `:latest` is a
moving target — the same `Dockerfile` can produce a different image
(different base OS packages, different language runtime patch version)
on every build, which breaks reproducibility and makes "it worked
yesterday" bugs common. Pin to a specific tag, ideally with a digest for
full reproducibility (`node:20.11.1-slim@sha256:...`).

**Docker: multi-stage builds to keep images small.** Build/compile in one
stage (with the full toolchain, dev dependencies, source) and copy only the
built artifact into a minimal final stage (no compilers, no build caches,
no dev dependencies). This shrinks the attack surface and image size
significantly, and keeps build-time secrets out of the shipped image.

**Kubernetes: set resource requests/limits explicitly.** A container with
no `requests`/`limits` can consume unbounded CPU/memory on its node,
starving neighbors, and the scheduler can't bin-pack effectively without
`requests` to plan against. Every workload should declare both.

**Kubernetes: liveness and readiness probes present.** Without a
`readinessProbe`, Kubernetes routes traffic to a pod before it's actually
ready to serve, causing errors during rollout. Without a `livenessProbe`,
a hung (but still running) process never gets restarted. Define both with
health-check endpoints appropriate to the app, not just a TCP check.

**Terraform: remote state with locking.** Local state files can't be
safely shared across a team and have no protection against two people
applying concurrently and corrupting state. Use a remote backend (S3+
DynamoDB lock table, Terraform Cloud, GCS with locking, etc.) for any
non-solo project.

**Terraform: modules for repeated patterns.** If the same block of
resources (e.g. "a VPC with these subnets," "an RDS instance with these
defaults") is copy-pasted across environments or services, extract it into
a module with inputs/outputs — copy-pasted Terraform drifts silently as
one copy gets updated and others don't.

**Terraform: no hardcoded credentials or secrets in `.tf` files.**
Credentials committed to `.tf` files end up in version control history
permanently, even if later removed. Source secrets from a secrets manager
(AWS Secrets Manager, GCP Secret Manager, Vault) via data sources, or from
environment/CI-injected variables — never as literal values in a resource
block or `terraform.tfvars` that gets committed.

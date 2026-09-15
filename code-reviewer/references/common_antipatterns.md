# Common Antipatterns

A catalog of named, recognizable antipatterns to reference during review. Each entry has a
name you can cite in a PR comment (e.g. "this looks like a God Object, see
`common_antipatterns.md#god-object`"), a short bad-code example, the concrete failure mode it
causes, and the fix.

This document is a *catalog of smells to recognize*. Pair it with:
- `code_review_checklist.md` — the judgement calls to make during a PR review (correctness,
  tests, API design, error handling, security, naming).
- `coding_standards.md` — how to write idiomatic code per language/stack in the first place.

If you're reviewing code and something feels off but you can't articulate why, scan this list —
odds are it has a name.

---

## General / cross-language antipatterns

### God Object / God Function

One class or function that knows too much and does too much — user management, billing,
notifications, and reporting all living in a single `UserService`, or a 400-line
`processOrder()` that validates input, computes pricing, charges a card, updates inventory, and
sends email.

```python
class UserService:
    def create_user(self, data): ...
    def send_welcome_email(self, user): ...
    def calculate_billing(self, user): ...
    def generate_report(self, user): ...
    def validate_password_strength(self, pw): ...
    def sync_to_crm(self, user): ...
    def log_audit_event(self, user, action): ...
    # 30 more methods across 6 unrelated concerns
```

**Why it hurts:** every change has a wide, unpredictable blast radius; unrelated features
compete for the same merge conflicts; the class can't be unit tested without dragging in every
dependency (DB, email, CRM, billing) even to test one method; nobody can hold the whole thing in
their head, so bugs hide in the interactions between its own methods.

**Fix:** split along responsibility boundaries (`UserRegistrar`, `BillingCalculator`,
`AuditLogger`, ...). Each collaborator gets injected where needed. See `codebase-design`/deep
modules guidance for how to find the right seams — the goal isn't "more, smaller classes" for
its own sake, it's classes whose names describe one job.

### Shotgun Surgery

A single logical change — e.g. "add a new order status" — requires touching a dozen unrelated
files: a switch statement in the API layer, another in the UI, another in the reporting job,
another in the notification templates, none of which reference each other.

**Why it hurts:** it's easy to update 11 of the 12 places and ship a bug that only shows up in
whichever one you missed, often in production, often in a code path with no test coverage. The
cost of a "trivial" change scales with how many places know about the concept, not with how
complex the concept is.

**Fix:** centralize the concept. A `status: OrderStatus` enum plus a single lookup table (label,
color, allowed transitions) that every layer imports beats status logic duplicated per-layer.
If you notice the same enum-like switch appearing in 3+ places, that's the signal.

### Primitive Obsession

Domain concepts represented as bare strings, ints, or plain objects with no real type instead of
a dedicated type — `status: string` that's actually a closed set of five values, `amount:
number` with no currency, `email: string` with no validation.

```typescript
function scheduleShipment(status: string, amount: number, email: string) { ... }
scheduleShipment("Pending", 49.99, "not-an-email"); // compiles fine
```

**Why it hurts:** the compiler/type-checker can't catch a typo'd status string or a swapped
argument order; validation logic gets copy-pasted at every call site (or skipped); "what are the
valid values of `status`" becomes a grep-the-codebase exercise instead of a type definition.

**Fix:** promote the primitive to a real type — a union/enum for closed sets
(`type OrderStatus = "pending" | "shipped" | "cancelled"`), a branded/value type for validated
primitives (`EmailAddress`, `Money`), a domain class if there's behavior attached.

### Boolean Blindness

Function signatures with multiple boolean/flag parameters, so the call site is unreadable
without jumping to the definition.

```typescript
createUser("alice", true, false, true);
// ^ which one is isAdmin? sendWelcomeEmail? requiresPasswordReset? nobody at the call site knows
```

**Why it hurts:** it's trivially easy to swap two booleans of the same type and have it compile
and pass code review, because `true, false, true` looks the same regardless of order. Reviewers
have to open the function definition to understand every call site.

**Fix:** named parameters / options object (`createUser("alice", { isAdmin: true, sendWelcomeEmail: false, requiresPasswordReset: true })`),
or split into separate functions if the flag combinations represent genuinely different
operations. In languages with named arguments (Python, Kotlin, Swift), use them for any call
with 2+ positional bools.

### Magic Numbers / Magic Strings

Unexplained literals scattered through logic instead of named constants.

```javascript
if (user.loginAttempts > 5) { lockAccount(user); }
setTimeout(retry, 86400000);
if (order.total > 2500) { flagForReview(order); }
```

**Why it hurts:** nobody knows if `5` is a business rule, a guess, or a typo; changing the policy
means grepping for the literal and hoping you found every occurrence (see Shotgun Surgery); the
same conceptual value (`86400000`) gets re-derived and re-typed in multiple places, sometimes
inconsistently (`86400000` vs `24 * 60 * 60 * 1000` vs a slightly-wrong `86401000`).

**Fix:** name it — `const MAX_LOGIN_ATTEMPTS = 5;`, `const ONE_DAY_MS = 24 * 60 * 60 * 1000;`.
The name documents intent and gives you one place to change the policy.

### Premature Abstraction / Speculative Generality

Building configuration systems, plugin hooks, or generic interfaces for requirements that don't
exist yet — "just in case we need to support multiple payment providers someday" when there's
one provider and no concrete plan for a second.

```typescript
interface PaymentStrategy { execute(ctx: PaymentContext): Promise<PaymentResult>; }
class PaymentStrategyFactory {
  static create(type: PaymentStrategyType, config: PaymentConfig): PaymentStrategy { ... }
}
// One concrete implementation exists: StripePaymentStrategy. It has since launch.
```

**Why it hurts:** the abstraction has to be maintained and understood by everyone touching the
code, but it's guessing at requirements nobody has confirmed — when the second payment provider
actually shows up, its real needs rarely match the speculative interface, and the abstraction
gets ripped out or bent out of shape anyway. Meanwhile every reader pays the indirection tax for
a flexibility nobody is using.

**Fix:** write the concrete thing first. Extract an interface when a second real
implementation shows up and you can see what actually varies (YAGNI). This is the direct
counterpart to Shotgun Surgery — the goal is finding the right time to abstract, not never
abstracting.

### Copy-Paste Programming

Logic duplicated across files by copying a block and tweaking it, rather than extracting a
shared function. Each copy then drifts independently as bugs are fixed in one but not the
others.

**Why it hurts:** a bug fix or business-rule change applied to one copy silently doesn't apply
to the others; reviewers approving a PR that touches one copy have no way to know three other
copies exist and now disagree with it. This is Shotgun Surgery's sibling — the difference is
Shotgun Surgery is about *one concept scattered across layers*, Copy-Paste is about *the exact
same logic block duplicated verbatim (or near-verbatim) at the same layer*.

**Fix:** extract the shared logic into a function/module once it's copied a second time (rule
of three is a reasonable threshold, but two nearly-identical 20-line blocks is already worth
asking about).

### Silent Failure / Swallowed Exception

Catching an exception and doing nothing, or only logging it, when the caller actually needed to
know the operation failed.

```java
try {
    chargeCard(order);
} catch (Exception e) {
    logger.warn("payment issue", e);
}
// order proceeds as if payment succeeded
```

**Why it hurts:** the caller (and the user) has no idea the operation failed — the order ships,
the record is marked complete, the UI shows success, and the failure only surfaces later as an
unexplained data inconsistency that's much harder to trace back to its cause than the original
exception would have been.

**Fix:** decide deliberately what "handled" means: rethrow (possibly wrapped with more context),
return a Result/Either type the caller must check, or explicitly document why swallowing is
safe here (e.g., a best-effort analytics call where failure truly doesn't matter) and log at a
level that reflects real severity. "Catch, log, continue" should be a conscious choice, not a
default. Note: this is a design-level judgement call, distinct from bare/overly-broad `except:`
clauses that mechanical linters already flag — see the Python section below.

### Leaky Abstraction

An interface that's supposed to hide implementation details but forces callers to know about
them anyway — a `Repository` interface that returns raw SQL row objects, a `HttpClient` wrapper
whose callers must know it's built on a connection pool with a specific timeout quirk.

```typescript
interface UserRepository {
  // Leaks the underlying ORM's result shape and its lazy-loading behavior
  findById(id: string): Promise<UserActiveRecordProxy>;
}
// callers must know to call `.reload()` before accessing certain fields, or they get stale data
```

**Why it hurts:** the abstraction promised to let callers stop thinking about the implementation,
but they can't — they have to understand the ORM's lazy-loading semantics, the DB driver's
connection lifecycle, or the HTTP client's retry behavior to use the "abstraction" correctly.
This usually shows up as a bug that only reproduces under specific implementation-detail
conditions the interface never mentioned.

**Fix:** design the interface around what callers actually need (a plain `User` domain object,
fully hydrated), and keep implementation-specific concerns (lazy loading, connection pooling,
retry/backoff) inside the implementation. If a caller needs to reach past the abstraction
regularly, that's a signal the abstraction boundary is in the wrong place.

---

## TypeScript / JavaScript-specific

### `any`-typing escape hatch

Reaching for `any` (or `as any`) to silence a type error instead of modeling the actual type.

```typescript
function processPayload(data: any) {
  return data.user.profile.settings.theme; // no compiler help, no autocomplete, crashes at runtime if any link is missing
}
```

**Why it hurts:** `any` doesn't just weaken checking on that one variable — it's contagious.
Once a value is `any`, everything derived from it is `any` too, silently disabling type checking
across an entire call chain while still looking like normal typed code to a reviewer skimming
the diff. It moves bugs from compile time to production.

**Fix:** model the real shape (`interface`, `zod`/`io-ts` schema for runtime-validated
boundaries like API payloads), or use `unknown` plus a narrowing check when the type genuinely
isn't known yet — `unknown` forces the caller to prove the shape before using it, `any` doesn't.

### Unhandled promise rejections

A `.then()` chain with no trailing `.catch()`, or a fire-and-forget `async` call whose returned
promise is never awaited or handled.

```javascript
function saveDraft(doc) {
  api.post("/drafts", doc).then(res => updateUI(res));
  // if the POST rejects, this is an unhandled rejection — swallowed by default in many runtimes,
  // crashes the process in strict Node configurations, and the user never learns the save failed
}
```

**Why it hurts:** failures vanish. Depending on runtime/config, an unhandled rejection either
disappears silently (the user thinks their data saved when it didn't) or crashes the whole
process (in Node with `unhandledRejection` treated as fatal) — neither is what anyone wants, and
neither is deliberate.

**Fix:** always terminate a promise chain with `.catch()`, or `await` inside a `try/catch`.
For fire-and-forget by design, be explicit about it (`void promise` with a comment, or a
`.catch(handleBackgroundError)`), not silence by omission.

### Prop drilling

Passing a prop through several intermediate components that don't use it themselves, purely to
get it from a distant ancestor to a distant descendant.

```tsx
<Page user={user}>
  <Sidebar user={user}>
    <NavList user={user}>
      <NavItem user={user} /> {/* only NavItem actually reads `user` */}
    </NavList>
  </Sidebar>
</Page>
```

**Why it hurts:** every intermediate component's signature is polluted with data it doesn't
care about, so refactoring `Sidebar` or `NavList` requires understanding a prop they never
touch; adding a new deeply-nested consumer means threading the prop through every layer again
(a mild form of Shotgun Surgery localized to a component tree).

**Fix:** React context for cross-cutting data (theme, current user, auth), or component
composition (pass the already-rendered child as `children`/a render prop so intermediate levels
don't need to know the prop exists at all). Don't reach for context for everything — a prop
passed one level down is fine; the smell is 3+ levels of pure pass-through.

### `useEffect` with a wrong/missing dependency array

An effect that reads a value from closure scope but doesn't list it as a dependency, or an empty
`[]` array on an effect that actually depends on changing props/state.

```jsx
function SearchResults({ query }) {
  const [results, setResults] = useState([]);
  useEffect(() => {
    fetchResults(query).then(setResults);
  }, []); // query is used inside but missing from deps — this closes over the *initial* query forever
}
```

**Why it hurts:** two failure modes, both common — (1) missing deps: the effect captures a stale
closure and keeps using the value from first render (search stays stuck on the first query
typed), or (2) over-inclusive/object-identity deps: an object or array recreated every render
listed as a dependency causes the effect to re-fire every render, sometimes triggering an
infinite render loop if the effect itself updates state.

**Fix:** let the exhaustive-deps lint rule guide the dependency array rather than fighting it;
if a value legitimately shouldn't retrigger the effect, that's usually a sign the effect is
doing two things (split it), or the value needs to be a `ref` instead of state/props, or the
value needs `useCallback`/`useMemo` to stabilize its identity across renders.

---

## Python-specific

### Mutable default argument

Using a mutable object (list, dict, set) as a default parameter value.

```python
def add_item(item, cart=[]):
    cart.append(item)
    return cart

add_item("apple")   # ["apple"]
add_item("banana")  # ["apple", "banana"]  <- surprise, same list reused across calls
```

**Why it hurts:** the default is evaluated once at function-definition time, not per call, so
every caller that doesn't pass `cart` explicitly shares and mutates the *same* list object
across calls — a bug that's invisible in a quick test (first call looks correct) and shows up
as inexplicable state leakage between unrelated calls in production.

**Fix:** default to `None` and create the mutable object inside the function body:
`def add_item(item, cart=None): cart = cart if cart is not None else []`.

### God-module `utils.py`

A single `utils.py` (or `helpers.py`, `common.py`) that accumulates unrelated functions over
time — date formatting, string sanitization, a DB connection helper, an email sender, a
retry decorator — with no organizing theme beyond "didn't know where else to put it."

**Why it hurts:** it becomes the module every other module imports, so it can't be safely
refactored (everything depends on it) and it can't be reasoned about (it has no single
responsibility to reason against). New contributors add to it because it's the path of least
resistance, accelerating the sprawl. It's a slow-motion God Object at module scope.

**Fix:** name modules after what they actually do (`date_formatting.py`, `retry.py`,
`email_client.py`). If a function is genuinely a one-off with no natural home, that's a signal
it belongs next to its single caller, not in a shared dumping ground.

### Overly broad `except Exception:`

Not the bare `except:` (which mechanical linters/scripts in this skill already flag) — this is
catching `Exception` broadly when the code only actually expects and can meaningfully handle one
or two specific failure modes.

```python
try:
    response = requests.get(url, timeout=5)
    data = response.json()
    process(data)
except Exception:
    return None
```

**Why it hurts:** this catches and silently converts to `None` everything from a timeout, to a
malformed JSON response, to a genuine bug in `process()` like an `AttributeError` from a typo.
Real programming errors get masked as "the request must have failed" and debugging becomes
guesswork because the exception type and message are discarded.

**Fix:** catch the specific exceptions you can actually do something about
(`requests.Timeout`, `requests.ConnectionError`, `json.JSONDecodeError`) and let genuinely
unexpected exceptions propagate — they indicate a bug that should surface, not be silently
absorbed. If you must have a catch-all boundary (e.g. at a request handler's top level), log the
full exception with traceback, don't just swallow it.

### Circular imports from poor module boundaries

Module A imports from module B, and module B imports from module A (directly or transitively),
usually because a shared concept wasn't given its own home.

```python
# models/user.py
from services.billing import calculate_plan_cost

# services/billing.py
from models.user import User  # circular
```

**Why it hurts:** it works until it doesn't — the failure depends on which module happens to be
imported first, so it can pass locally and break in a different entry point, or break only after
an unrelated reordering of imports. Developers "fix" it with local imports inside functions,
which hides the real problem (the module boundary is wrong) instead of fixing it.

**Fix:** the recurring root cause is a missing module for a concept both sides need — extract
the shared piece (e.g. a `PlanPricing` value object) into its own module that both `models/user`
and `services/billing` import from, rather than having them import each other.

---

## Go-specific

### Ignored errors

Discarding a returned `error` with `_` or simply not checking it.

```go
data, _ := os.ReadFile(configPath)
json.Unmarshal(data, &config) // if ReadFile failed, data is nil/empty and this fails confusingly downstream
```

**Why it hurts:** Go's explicit error returns are the language's primary correctness mechanism;
discarding them converts a clear, immediate signal ("the file doesn't exist") into a confusing
failure several lines later with a much less informative error (or worse, silently wrong
behavior, like proceeding with a zero-value config).

**Fix:** check every error, even when the local handling is just `return fmt.Errorf("reading
config: %w", err)`. If a lint pass flags this mechanically already, treat any manual `_ = err`
as requiring an explicit one-line justification in review, not a rubber stamp.

### Goroutine leaks

Spawning a goroutine that blocks on a channel or does work with no cancellation path, so it
never exits even after the caller has stopped caring about the result.

```go
func fetchWithTimeout(url string) string {
    ch := make(chan string)
    go func() {
        ch <- slowHTTPGet(url) // no way to cancel this if we give up waiting
    }()
    select {
    case result := <-ch:
        return result
    case <-time.After(2 * time.Second):
        return "" // the goroutine above is still blocked on slowHTTPGet, leaked forever
    }
}
```

**Why it hurts:** each leaked goroutine holds its stack and any captured resources (HTTP
connections, file handles, mutex locks) for the life of the process. Under load, this shows up
as a slow, hard-to-diagnose memory/goroutine-count climb that only reproduces after hours of
production traffic, not in a quick local test.

**Fix:** pass a `context.Context` through and make the goroutine respect cancellation
(`req.WithContext(ctx)` for HTTP calls, `select` on `ctx.Done()` in loops). Every goroutine
should have a clear answer to "what makes this exit."

### Interface pollution

Defining an interface on the producer side "just in case it's needed generically," rather than
letting the consumer define the (usually much smaller) interface it actually needs.

```go
// storage package defines this speculatively:
type Storage interface {
    Get(key string) ([]byte, error)
    Set(key string, value []byte) error
    Delete(key string) error
    List(prefix string) ([]string, error)
    Watch(key string) (<-chan Event, error)
    // 6 more methods, because "some caller might need them"
}
```

**Why it hurts:** Go interfaces are meant to be satisfied implicitly and kept small at the point
of consumption (`io.Reader` is one method for a reason). A large producer-side interface forces
every implementation (including test mocks) to implement methods it doesn't need, and forces
every consumer to depend on a huge surface even if they call one method.

**Fix:** define interfaces where they're consumed, sized to exactly what that consumer calls
(`type KeyGetter interface { Get(key string) ([]byte, error) }`). Let the concrete struct in the
producer package satisfy multiple small consumer-defined interfaces implicitly — that's
idiomatic Go, not the reverse.

---

## Swift-specific

### Force-unwrap / force-try landmines

Using `!` or `try!` outside a context where safety is actually provable (e.g. a `guard let`
already established non-nil, or a compile-time-constant literal).

```swift
let url = URL(string: userProvidedString)!  // crashes if the user typed something invalid
let data = try! JSONDecoder().decode(Response.self, from: networkData) // crashes on any malformed server response
```

**Why it hurts:** both are unconditional crashes at runtime the moment the assumption is wrong —
and both examples above have their assumption controlled by external input (user text, network
response) that is not guaranteed to be well-formed. This turns a recoverable error into an app
crash, often reported by users as "the app just closes."

**Fix:** `guard let url = URL(string: userProvidedString) else { /* handle */ }`, or
`do { let data = try JSONDecoder()... } catch { /* handle */ }`. Reserve `!`/`try!` for cases
where the value is provably non-nil/non-throwing by construction (e.g. a hardcoded, tested
constant), and consider a comment noting why it's safe.

### Massive View Controller

A `UIViewController` (or SwiftUI `View` doing equivalent work) that owns networking code,
business logic/validation, persistence, and UI layout all in one file — the iOS-specific
flavor of God Object.

```swift
class ProfileViewController: UIViewController {
    func viewDidLoad() {
        // 200 lines: URLSession networking, JSON parsing, validation rules,
        // Core Data saves, and UIKit layout, all inline in this one class
    }
}
```

**Why it hurts:** the view controller can't be unit tested without spinning up the full UIKit
lifecycle and mocking the network; a change to validation logic risks breaking layout code in
the same file because everything shares state and nothing is isolated; onboarding someone to
"just fix the validation bug" means reading through networking and layout code first.

**Fix:** extract networking into a service/repository, business rules into a view model or
use-case type, and keep the view controller responsible only for wiring the view model to UIKit
(MVVM, or whatever pattern the rest of the codebase already uses — see `coding_standards.md`
for the project's chosen pattern). The view controller's job becomes "translate view model
state into UI calls," nothing else.

### Retain cycles from strong closure captures

A closure stored on `self` (e.g. a completion handler, a Combine subscription, a timer) that
captures `self` strongly, while `self` also holds a strong reference to the closure.

```swift
class ProfileLoader {
    var onComplete: (() -> Void)?
    func load() {
        networkClient.fetch { result in
            self.onComplete?()  // strong capture of self; if self also owns this closure, neither can deallocate
        }
    }
}
```

**Why it hurts:** neither object can ever be deallocated — `self` keeps the closure alive (as a
stored property or ongoing subscription), and the closure keeps `self` alive (via the strong
capture), so ARC's reference count never reaches zero. The object silently leaks: it stays in
memory for the life of the app, its deinit-driven cleanup never runs, and memory grows with
every leaked instance (e.g. every screen the user navigates to and "away" from).

**Fix:** `[weak self]` in the capture list for closures that outlive the immediate call and are
stored on `self` or a long-lived object, then `guard let self else { return }` inside. Not every
closure needs `weak self` — a closure that runs and completes within the same scope (e.g. a
synchronous `map`) is fine with a strong capture; the risk is specifically closures retained
past the current scope.

---

## Kotlin-specific

### Overuse of `!!` (non-null assertion)

Using `!!` to force a nullable type to non-null without actually proving it can't be null,
mirroring Swift's force-unwrap problem.

```kotlin
fun greet(user: User?) {
    println("Hello, ${user!!.name}") // NullPointerException if user is ever null, defeating the point of Kotlin's null safety
}
```

**Why it hurts:** Kotlin's whole null-safety system exists to move null checks to compile time;
`!!` opts back out of that guarantee at exactly the point where it'd otherwise catch a bug,
converting a compile-time-preventable `NullPointerException` back into a runtime crash — the
exact failure mode Kotlin's type system was designed to eliminate.

**Fix:** `user?.let { ... }`, `user ?: return`, safe calls with the Elvis operator, or a proper
`require`/precondition with a meaningful message if null truly should never happen at that point
(`requireNotNull(user) { "user must be loaded before greet()" }` at least fails with an
explanatory message instead of a bare NPE).

### `else` branch masking unhandled sealed-class cases

A `when` over a `sealed class`/`sealed interface` that includes a catch-all `else` branch,
rather than an exhaustive `when` that the compiler forces you to update when a new subtype is
added.

```kotlin
sealed class PaymentState
class Pending : PaymentState()
class Succeeded : PaymentState()
class Failed(val reason: String) : PaymentState()

fun describe(state: PaymentState) = when (state) {
    is Succeeded -> "Payment complete"
    else -> "Payment in progress" // silently wrong for both Pending and Failed, and for any future subtype
}
```

**Why it hurts:** the entire point of a sealed class is that the compiler can verify a `when` is
exhaustive and flag it when a new subtype is added elsewhere in the codebase. An `else` branch
throws that guarantee away — adding `Refunded` later compiles silently and falls into `else`,
even though it almost certainly needs its own distinct handling (here, describing a refund as
"Payment in progress" is actively misleading).

**Fix:** enumerate every subtype explicitly and let the compiler enforce exhaustiveness
(remove `else` entirely for sealed types where practical). If a true wildcard fallback is
intentional (e.g. deliberately treating all-but-one case the same way), say so with a comment
explaining why new cases are safe to fall through — don't let it look like an oversight.

---

## API / Backend-specific (Node / Express / GraphQL / PostgreSQL)

### N+1 query pattern

Fetching a list, then issuing one additional query per item in a loop instead of a single
batched query (or a join / `DataLoader`).

```javascript
const orders = await Order.findAll();
for (const order of orders) {
  order.customer = await Customer.findByPk(order.customerId); // one query per order
}
```

**Why it hurts:** query count scales linearly with result size — 20 orders means 21 queries
instead of 2. It's invisible in dev/test with a handful of rows and a local DB, then shows up as
a production latency cliff once a list grows to hundreds of rows, especially over a
higher-latency DB connection. This is the single most common GraphQL resolver bug (each field
resolver independently re-fetching per parent).

**Fix:** batch — a single `WHERE customer_id IN (...)` query, an eager-load/`include` on the
initial query, or `DataLoader` in GraphQL resolvers to coalesce per-request fetches
automatically.

### Fat controller / anemic domain model

An Express route handler (or GraphQL resolver) that inlines request validation, business rules,
and direct DB persistence all in the handler body, with no domain layer in between.

```javascript
app.post("/orders", async (req, res) => {
  if (!req.body.items || req.body.items.length === 0) return res.status(400).send("no items");
  let total = 0;
  for (const item of req.body.items) {
    total += item.price * item.qty;
    if (item.qty > 100) return res.status(400).send("qty too high"); // business rule inline
  }
  if (total > 10000) total *= 0.95; // discount rule inline
  const order = await db.query("INSERT INTO orders ...", [req.body.customerId, total]);
  res.json(order);
});
```

**Why it hurts:** business rules (bulk discount, quantity limits) are untestable without spinning
up an HTTP server and a real DB — there's no function to unit test in isolation. The same rules
get re-typed slightly differently in a second handler (an admin-only order-creation endpoint,
say) and drift out of sync, since there's no shared place they live.

**Fix:** the route handler's job is parsing the request, calling a domain/service function, and
shaping the response — `const order = await orderService.createOrder(parsedInput)`. Validation
and business rules live in the service layer where they can be unit tested without HTTP or a
live DB, and are reused by any other entry point (CLI, background job, admin panel) that needs
to create an order.

### Chatty API

A client that must make many small sequential round-trips to accomplish one logical operation,
where a single batched/composed endpoint would do — e.g. fetching a list, then a detail call per
item, then a permissions check per item.

**Why it hurts:** each round-trip pays full network latency, and on mobile or high-latency
connections this compounds into a visibly slow feature even though each individual call is
fast. It's the REST/HTTP analog of the N+1 query problem, just at the client-server boundary
instead of the app-database boundary.

**Fix:** design endpoints (or GraphQL queries) around what the client actually needs in one
screen/operation, not a 1:1 mirror of DB tables — a single `GET /orders/:id?include=customer,items`
or one GraphQL query with nested fields beats four separate REST calls.

---

## Infra-specific (Docker / Kubernetes / Terraform)

### `:latest` tag drift

Referencing `:latest` (or no tag at all) for a base image or dependency instead of a pinned
version.

```dockerfile
FROM node:latest
RUN npm install -g some-cli
```

**Why it hurts:** the exact same Dockerfile produces a different image depending on when it's
built — a rebuild six months from now pulls a different Node version with different behavior,
possibly breaking the build or introducing a subtle runtime difference that's nearly impossible
to bisect later ("it worked in CI yesterday" with no code change). It also defeats reproducible
security scanning, since the image contents aren't pinned to what was actually tested.

**Fix:** pin to a specific version, ideally with a digest for full reproducibility
(`FROM node:20.11.1-bookworm-slim@sha256:...`). Bump deliberately via a dependency-update PR,
not implicitly on every rebuild.

### Secrets baked into image layers or committed IaC files

`ENV API_KEY=sk-live-...` in a Dockerfile, a secret hardcoded in a `.tf` file, or a real
credential committed in a `.env` file, instead of runtime injection or a secrets manager.

```dockerfile
FROM python:3.12-slim
ENV DATABASE_PASSWORD=hunter2
```

**Why it hurts:** a Docker layer is immutable and cached — even if a later layer removes the
env var or a later commit deletes the line, the secret is still recoverable from the image
history / git history indefinitely. Anyone who can pull the image or clone the repo (including
through a leaked image on a public registry, or a fork) gets the credential, and rotating it
means the leaked value is permanently valid until explicitly revoked.

**Fix:** inject secrets at runtime — Docker/Kubernetes secrets mounted as files or env vars set
by the orchestrator (not baked into the image), a secrets manager (Vault, AWS Secrets Manager,
GCP Secret Manager) fetched at startup, or `--build-arg` combined with a multi-stage build that
never persists the secret into the final image layer. Never commit real credentials to `.tf` or
`.env` files tracked by git — use `.tfvars`/`.env` files that are gitignored, with a checked-in
`.env.example` showing the shape without real values.

### Missing resource limits

A Kubernetes pod spec with no `resources.limits` (CPU/memory), so a single misbehaving pod can
consume all available resources on its node.

```yaml
containers:
  - name: worker
    image: myapp/worker:1.4.2
    # no resources.requests or resources.limits at all
```

**Why it hurts:** without a memory limit, a leak or a spike in one pod can exhaust node memory
and trigger the kernel OOM killer, which may kill *other, unrelated* pods on the same node
rather than the offending one — a single misbehaving service takes down neighbors that had
nothing to do with the problem. Without CPU limits, one pod can starve others of CPU time,
causing unrelated latency spikes that look unrelated to the actual root cause during an
incident.

**Fix:** always set `resources.requests` (for scheduling) and `resources.limits` (for the hard
ceiling) sized from observed usage, and review them periodically as usage patterns change. A pod
with no limits is effectively a "trust me" promise to every other workload on the node.

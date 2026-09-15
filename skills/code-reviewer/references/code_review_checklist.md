# Code Review Checklist

This is the human-judgment layer of the code-reviewer skill. The skill's scripts
(`pr_analyzer.py`, `code_quality_checker.py`, `review_report_generator.py`) already catch
mechanical issues heuristically: TODO/FIXME markers, debug statements (`console.log`,
`print`, `debugger`), overly long functions/files/lines, deep nesting, bare `except:`,
empty `catch {}`, possible hardcoded secrets, and high cyclomatic complexity.

Don't re-run those checks by eye. This checklist is for the things a linter or a regex
can't tell you: whether the code actually does what it claims, whether the tests prove
anything, whether the design is sound, and whether a failure mode was actually thought
through rather than just wrapped in a `try` block. Use it to focus your reading time on
judgment calls, not on things a script already flagged.

---

## 1. Correctness & Logic

The single most important question: **does this code do what the PR description says it
does, in every case, not just the case the author tested?**

- Re-derive the intent from the ticket/PR description first, then read the diff against
  that intent — don't just read the diff in isolation and assume it's right.
- Walk through the logic with concrete inputs, especially the edges:
  - Empty input: empty string, empty array/list, empty object, zero-length file.
  - Null/nil/undefined/None at every parameter, not just the ones the type system forces
    you to handle.
  - Boundary values: 0, -1, 1, `MAX_INT`/`MAX_SAFE_INTEGER`, the first and last element of
    a collection, exactly-at-the-limit vs one-past-the-limit.
  - Off-by-one conditions in loops and slices — `<` vs `<=`, `array[len]` vs
    `array[len - 1]`, inclusive vs exclusive ranges.
  - Concurrent/parallel access where relevant: can two requests race on the same resource?
    Is there a check-then-act gap (e.g., "check row exists" then "update row" as two
    separate operations)? Is shared mutable state actually guarded?
- Check the failure paths as carefully as the happy path. If the happy path is 10 lines
  and correct, and the error path is 2 lines and untested, that's the riskier code.
- Look for logic that was *copied* from elsewhere and subtly adapted — the classic source
  of bugs is a pasted block where one variable name wasn't updated.
- If a function claims to be pure/idempotent, verify it actually is (no hidden mutation of
  arguments, no hidden I/O).
- Does this change interact with existing code in a way the author may not have
  considered — a shared cache, a shared queue, a global config flag, an ORM model used
  elsewhere with different assumptions?

## 2. Tests

Scripts can confirm a test file exists. They cannot confirm the test proves anything.

- **New or changed logic needs new or changed tests.** If the diff changes behavior and
  the test diff is empty, that's a gap worth calling out explicitly.
- **Tests must assert behavior, not just absence of exceptions.** A test that calls a
  function and asserts nothing (or asserts `result !== null`) is not a test — it's a smoke
  check. Look for a real assertion on the actual output/state.
  ```ts
  // Weak — proves almost nothing
  it("processes the order", async () => {
    await processOrder(order);
  });

  // Meaningful — asserts the actual contract
  it("marks the order as paid and decrements inventory", async () => {
    await processOrder(order);
    expect(order.status).toBe("paid");
    expect(inventory.get(order.sku)).toBe(startingStock - order.qty);
  });
  ```
- **The edge cases identified in Correctness above should show up as test cases** — empty
  input, boundary values, null handling, the error path. If you found an edge case while
  reading the logic and there's no test for it, that's a concrete, actionable comment.
- **Test names should describe intent/behavior, not mechanics.** `test1`, `testFoo`, or
  `it("works")` tell the next person nothing when the test fails. Prefer
  `it("rejects transfers that exceed the daily limit")`.
- **Watch for flaky patterns in unit tests:**
  - `sleep()`/`setTimeout` used to "wait for" async work instead of awaiting the actual
    promise/event — a timing-dependent test that will flake under load.
  - Real network calls or real database connections in what's supposed to be a unit test
    (vs. an integration test that's explicitly labeled and isolated).
  - Reliance on the real system clock (`Date.now()`, `time.time()`) without freezing/
    injecting it — tests that pass today and fail at midnight or on a leap day.
  - Shared mutable fixtures/state across tests that create order-dependence (test B only
    passes because test A ran first and mutated a global).
- A test that was clearly written to make coverage green rather than to catch a
  regression (e.g., it re-implements the function's logic inline to compute the expected
  value) provides false confidence — flag it.
- Deleted or weakened tests in a diff deserve the same scrutiny as new code — ask why.

## 3. API & Interface Design

Applies to function signatures, exported types/interfaces, REST endpoints, and GraphQL
schema changes.

- **Is the public surface as small as it needs to be?** New exports, new public methods,
  new REST routes, new GraphQL fields/mutations are a long-term maintenance commitment —
  question ones that only exist to serve one internal caller when a private helper would
  do.
- **Does the shape match existing conventions in the codebase?** Inconsistent pagination
  styles, inconsistent error envelope shapes, a new endpoint that doesn't follow the
  existing `/api/v1/...` versioning or response-format convention, a function that takes
  positional args when every sibling function in the module takes an options object.
- **Parameter and return types should make illegal states hard to represent.** A function
  signature like `save(user, isUpdate: boolean, skipValidation: boolean)` invites misuse;
  compare to `createUser(user)` / `updateUser(user)`.
- **Breaking changes must be flagged and versioned appropriately** — a changed REST
  response shape, a removed/renamed GraphQL field, a changed function signature on an
  exported module. Ask: who else calls this? Is there a deprecation path, or does this
  need a major version bump / new API version?
- **Nullability and optionality in schemas should reflect reality.** A GraphQL field
  marked non-null that the resolver can actually return `null` for will crash clients at
  runtime; an optional REST field that's actually always present just adds friction to
  every consumer.
- **Look at the diff's shape relative to its stated goal.** A PR titled "fix null pointer
  in checkout" that also renames twelve unrelated functions and reformats three files
  makes the actual fix harder to verify and harder to revert independently — ask for the
  unrelated changes to be split out.

## 4. Error Handling

The scripts flag *mechanical* smells — bare `except:`, empty `catch {}`. That only tells
you an error was structurally caught. It says nothing about whether the response to that
error is correct. Go further:

- **Errors should carry enough context to debug the failure without reproducing it
  locally.** Compare `throw new Error("failed")` to
  `throw new Error(\`failed to sync order ${orderId} to warehouse: ${cause.message}\`)`.
  Swallowing the original error/cause (not chaining it) throws away the actual root cause.
- **A caught error that isn't `except:`/`catch {}` can still be a silent swallow.** Watch
  for broad catches that log-and-continue when continuing is unsafe, or that catch a
  generic exception type when only one specific failure was anticipated:
  ```python
  # The tooling won't flag this — it's not bare except — but it's still too broad.
  try:
      charge_card(order)
  except Exception as e:
      logger.warning(f"charge failed: {e}")
      # falls through and marks the order as paid anyway
  ```
  Ask: what specific exceptions can this call actually raise, and is catching
  `Exception`/`Error` hiding ones that should propagate (e.g., a programming bug getting
  treated the same as a declined card)?
- **Does the failure mode match the recovery strategy?**
  - Network/IO calls to external services should generally have a timeout — an unbounded
    call can hang a request thread/worker indefinitely.
  - Retries are appropriate for transient failures (timeouts, 502/503, connection resets)
    and should use backoff (ideally with jitter) to avoid hammering a struggling
    dependency. Retrying a 4xx client error or a validation failure is usually wrong — it
    won't succeed on attempt two and just delays the correct response.
  - Is the retry bounded? An unbounded retry loop is a resource leak / potential outage
    amplifier.
  - Is the operation idempotent if it's going to be retried? Retrying a non-idempotent
    payment charge or a non-idempotent "increment counter" can double-apply the effect.
- **User-facing error messages should not leak internals.** Stack traces, SQL fragments,
  internal file paths, or raw exception messages returned directly to an API consumer or
  rendered in a UI are both a security and UX problem. The user-facing message should be
  generic/actionable; the detailed message belongs in server-side logs (correlated by a
  request/trace ID the user-facing error can reference).
- **Failures should fail loud where correctness matters, quiet where it doesn't.** A
  background analytics event that fails to send should probably log and continue; a
  failed inventory decrement during checkout should not silently continue as if it
  succeeded. Judge based on the actual consequence of "pretend this worked."

## 5. Security

- **Validate input at trust boundaries**, not deep inside business logic where it's easy
  to forget on a new code path. Every place external input enters (HTTP body/query/params,
  message queue payload, file upload, third-party webhook) should validate shape, type,
  and range before it's used.
- **SQL/query injection**: any place a value is interpolated into a query string is
  suspect. Confirm parameterized queries / prepared statements / an ORM's parameter
  binding is used — never raw string concatenation or f-string/template interpolation
  into SQL, even for values that "can't" contain user input today.
  ```python
  # Vulnerable
  cursor.execute(f"SELECT * FROM users WHERE email = '{email}'")
  # Safe
  cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
  ```
  The same principle applies to shell command construction (command injection),
  NoSQL query objects built from raw user input, and dynamic GraphQL/ORM `where` clauses.
- **Auth/authz on every new endpoint or resolver, not just the primary one.** It's common
  to add a new `GET /orders/:id` and correctly check the requester owns the order, then
  add `DELETE /orders/:id` later and forget the same ownership check — verify each new
  route/mutation independently, including "internal" or "admin" ones that might be
  reachable without an internal-only network boundary.
  - Check for IDOR: does the endpoint use an ID from the request to fetch a resource
    without confirming the authenticated principal is allowed to access *that specific*
    resource (not just "is authenticated at all")?
  - Check GraphQL field-level auth specifically — a query can be authorized at the root
    but expose a field that should have its own restriction (e.g., a `user` type
    resolving an `ssn` or `internalNotes` field to any caller who can query any user).
- **Secrets never hardcoded.** The scripts' possible-secrets check catches obvious
  patterns (API-key-shaped strings, `password = "..."` literals). Human review should also
  catch *indirect* leaks the pattern-matcher can't:
  - Logging a full request/response object that happens to contain an auth header,
    password, or token (`logger.info(f"request: {request}")`).
  - Including secrets in error messages or stack traces that get sent to an external
    error tracker.
  - Committing a `.env.example` that was accidentally filled with a real value instead of
    a placeholder.
  - Passing secrets as CLI args or URL query params, which land in shell history / access
    logs / server logs.
- **Deserialization of untrusted data** (`pickle.loads`, `yaml.load` without
  `SafeLoader`, unchecked `JSON.parse` feeding into `eval`-like code paths) — flag any use
  of an unsafe deserializer on external input.
- **File paths built from user input** — check for path traversal (`../`) if a filename
  or path segment comes from a request and is used to read/write a file.

## 6. Naming & Readability

- **Names should communicate intent, not implementation.** `data`, `temp`, `result2`,
  `handleClick2` tell the reader nothing. `pendingInvoiceIds`, `retryAfterMs` do. Prefer a
  name specific enough that the variable's purpose is clear without reading its
  initialization.
- **A comment explaining *what* a poorly-named thing does is a signal to rename, not to
  comment.** `// this is the count of active users minus suspended ones\nconst n = ...`
  should become `const activeNonSuspendedUserCount = ...` with no comment needed. Reserve
  comments for *why* (a non-obvious business rule, a workaround for a specific bug/library
  quirk, a link to the ticket explaining an odd threshold) — not *what*, which the code
  itself should say.
- **Booleans should read as predicates**: `isValid`, `hasPermission`, `canRetry` — not
  `valid`, `flag`, `status` (which could be a boolean, string, or enum from the name
  alone).
- **Function names should match what they actually do.** A function named `getUser` that
  also writes a last-login timestamp has a misleading name (and probably a hidden side
  effect worth separating out or at least renaming, e.g. `getUserAndTouchLastLogin`, so
  callers aren't surprised).
- **Prefer the smallest diff that accomplishes the stated goal.** Renaming variables,
  reordering imports, or reformatting untouched code in the same PR as a behavioral change
  makes the review harder and the git blame noisier. If a rename/cleanup is valuable,
  suggest it as a separate PR rather than blocking on it here — but do flag when it's
  mixed in, since it obscures the actual change under review.
- **Consistency beats personal preference.** If the file/module already has a convention
  (naming case, error-handling style, import order), a locally "better" alternative
  introduced in one function creates inconsistency that costs more than it gains — match
  the surrounding code unless the surrounding code itself is being fixed.

## 7. Language-Specific Gotchas

Keep this tight — a few well-known, high-value traps per language the skill covers.
The mechanical scripts don't (and can't reliably) catch these; they require reading.

**TypeScript**
- `any` used as an escape hatch to silence a type error (`as any`, untyped `any`
  parameter) defeats the purpose of the type system at exactly the point where it was
  inconvenient — check whether a real type (or `unknown` + a narrowing check) was
  actually harder, or just skipped.
- Non-null assertions (`value!`) turn a real, potentially-null case into a runtime crash
  instead of a compile-time error — check whether the value can actually be null at that
  point (e.g., after an async gap, after a `.find()`, after an optional chain) and whether
  the assertion is masking a real bug rather than documenting a genuine invariant.
- `as SomeType` casts across unrelated shapes bypass structural checking the same way
  `any` does — distinguish a narrowing cast (safe, from a wider type to a known-narrower
  one) from a casting-around-a-mismatch (the type doesn't actually match, and the cast is
  there to make the compiler stop complaining).

**Python**
- Mutable default arguments (`def f(items=[])`) are created once at function definition
  time and shared/mutated across every call — a classic, easy-to-miss bug.
  ```python
  def add_item(item, items=[]):   # bug: shared across calls
      items.append(item)
      return items
  ```
- The tooling flags bare `except:`, but it doesn't flag `except Exception:` — which is
  almost as broad. It catches `KeyboardInterrupt`-adjacent issues aside, but still hides
  `TypeError`/`AttributeError`/programming bugs behind the same handling as the specific
  failure that was anticipated. Prefer catching the narrowest exception type the call can
  actually raise.
- Late-binding closures in loops (`[lambda: i for i in range(5)]` — every lambda returns
  the final value of `i`) trip up both list comprehensions and loop-created callbacks.

**Go**
- Ignored error returns — `value, _ := doThing()` or a bare `doThing()` where the second
  return is dropped — silently discard failure information. Every `error` return should
  either be checked or explicitly and intentionally ignored with a comment explaining why
  it's safe to ignore.
- Goroutine leaks: a goroutine started with no way to signal it to stop (no context
  cancellation, no closed channel to select on) keeps running/holding memory for the life
  of the process. Check that every long-running or blocking goroutine has a cancellation
  path tied to the caller's lifecycle.
- Loop variable capture in goroutines (`for _, v := range items { go func(){ use(v) }() }`)
  — prior to Go 1.22 this captures the shared loop variable, not a per-iteration copy,
  and every goroutine can observe the same (usually last) value. Confirm the Go version in
  use, or that the value is passed as a parameter / copied inside the loop body.

**Swift**
- Force-unwraps (`value!`) in non-test code turn an optional into a guaranteed crash if
  the assumption is ever wrong — acceptable in tests/prototypes, a red flag in production
  paths handling anything derived from network, disk, or user input. Prefer `guard let`/
  `if let`/nil-coalescing with an explicit fallback or error.
- Retain cycles in closures: a closure stored on `self` (e.g., a completion handler, an
  observer, a `Timer` callback) that captures `self` strongly, while `self` holds a strong
  reference back to the closure, leaks both. Check for `[weak self]` (or `[unowned self]`
  when the lifetime is truly guaranteed) on closures assigned to long-lived properties.

**Kotlin**
- Unnecessary `!!` non-null assertions convert a nullable into an immediate
  `NullPointerException` if the value is ever actually null — same judgment as Swift's
  force-unwrap: is this documenting a real invariant, or papering over a case that should
  be handled with `?.`/`?:`/an explicit null check?
- `when` expressions used as expressions (assigned to a value or returned) without an
  `else` branch will fail to compile only if the subject is a sealed class/enum with
  exhaustive branches — but a `when` over a non-sealed type (e.g., `Int`, `String`) with
  no `else` silently falls through with no value in statement position, or is a compile
  error in expression position missing a case. When a new case is added to a sealed
  hierarchy later, confirm existing `when` blocks are still exhaustive rather than relying
  on a stale `else -> {}` that swallows the new case.

## 8. Review Etiquette

- **Ask instead of asserting when intent is unclear.** "What happens if `items` is empty
  here?" invites explanation and is often faster to resolve than "this is broken" when the
  author may have a reason you're missing.
- **Distinguish blocking issues from suggestions explicitly.** Prefix or label comments so
  the author can triage at a glance — e.g., "Must fix: this drops the transaction on
  error" vs. "Nit: could inline this" vs. "Consider: a named constant here would help the
  next reader." Don't make the author guess which comments are optional.
- **Approve when the code is good enough, not only when it's perfect.** A PR that is
  correct, tested, and reasonably clear shouldn't be held hostage to a reviewer's
  personal stylistic preference — leave those as non-blocking suggestions and approve.
- **Explain the "why" behind a requested change**, not just the "what" — it helps the
  author generalize the lesson instead of just satisfying this one review, and lets them
  push back with context you might be missing.
- **Acknowledge good decisions, not just problems.** A review that's 100% criticism reads
  as harsher than intended and buries the signal on what actually needs to change.

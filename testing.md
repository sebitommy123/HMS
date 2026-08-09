HMS — Testing

Testing is essential because the two hard problems — injectors and the user interface — are both complex and performance-critical. The goal is to catch bugs early, enforce performance guarantees, and keep the system reliable as it grows. Every layer should be testable, and performance should be a first-class concern in the test suite from day one.

---

## 1. User Interface Components

UI components are hard to test in the traditional sense. We don't want to rely on heavyweight browser-based test frameworks. Instead, the approach is to **extract all logic out of the rendering layer into pure functions**, and then unit test those functions exhaustively.

### 1.1 Logic Extraction

Every component must separate its logic from its rendering. The rendering layer should be as thin as possible — ideally just calling the logic functions and mapping the results to DOM elements.

Example: the graph component. The function that computes x-axis ticks is a pure function. Given an interval, a start value, and an end value, it returns a list of tick marks — each with a label and a pixel offset from the origin. This function is unit tested independently of the graph ever being rendered.

What we test for the tick function:
- Always produces at least some minimum number of ticks (e.g. 10)
- Never produces more than some maximum (e.g. 50)
- Ticks are evenly spaced
- Labels are correctly formatted for different scales (seconds, minutes, hours, days)
- Edge cases: very small ranges, very large ranges, negative values, zero-width ranges

This pattern applies to every component. Tables have logic for pagination, column sorting, row grouping. Lists have logic for item ordering and overflow handling. Every piece of logic that a component depends on should be a testable function.

### 1.2 Performance Benchmarks

UI components must handle scale. A graph might need to render 10,000 data points. A table might have 50,000 rows (virtualized). Components need to be fast regardless of input size.

The approach: benchmark tests that measure render time (of the logic functions, not the DOM) across a range of input sizes. We set **upper bounds** — not tight targets, but ceilings that indicate something has gone seriously wrong.

Example bounds:
- Graph tick computation: < 50ms for any input (goal: < 2ms)
- Table row computation for 50,000 rows: < 100ms (goal: < 10ms)
- Component configuration resolution: < 10ms

These bounds are intentionally generous. They exist to catch regressions, not to optimize. If a change pushes tick computation from 1ms to 80ms, the test fails and we know something is wrong — even though 80ms might still feel fast in isolation.

Benchmark tests should run across a variety of cases:
- Small data (10 points), medium data (1,000 points), large data (100,000 points)
- Different aggregation strategies
- Deeply nested component trees (composability stress test)

These won't be perfectly deterministic across machines, but the upper bounds should be generous enough that they pass everywhere while still catching genuine regressions.

---

## 2. Injector Modules

Modules are the hand-coded building blocks that injectors are assembled from. They handle the hard parts: SQL aggregation, time-range queries, filtering, etc. They must be both correct and fast.

### 2.1 Unit Tests

Each module gets thorough unit tests against mock backends. For SQL modules, this means running against actual database instances (not mocked query builders — real queries against real databases).

### 2.2 Scale Tests

**The mock databases must have scale.** Testing against a table with 20 rows tells you almost nothing. The test databases need to reflect production-like conditions:
- Millions of rows, ideally hundreds of millions
- Realistic data distributions (not uniform random — real-world skew, nulls, outliers)
- Realistic schemas (multiple columns, indexes, foreign keys)

This is critical. A filtering module might work perfectly on 100 rows and fall apart at 10 million because it's doing a full table scan instead of hitting an index. We need to catch that in testing, not in production.

### 2.3 Performance Bounds

Just like UI components, modules get performance ceilings:
- A filter applied to a 10M-row table should return in < X seconds
- An aggregation over a 100M-row time series should return in < Y seconds

The exact numbers depend on the backend and the operation, but the principle is the same: generous upper bounds that catch catastrophic regressions.

### 2.4 Correctness Tests

Beyond performance, modules need correctness tests:
- Filtering: assert that results actually match the filter criteria, no rows are dropped or duplicated
- Aggregation: assert that averages, sums, min/max, percentiles are mathematically correct against a known dataset
- Standardization: assert that the same standardized query produces equivalent results across different backend types (e.g., the same time-range query against Postgres and against a CSV file should return the same data)

---

## 3. AI — Automated Testing

The AI layer is non-deterministic by nature, which makes testing harder. We split it into two categories: things we can test automatically and things that require manual verification.

### 3.1 Deterministic Configuration Tests (Automated)

When a user gives a precise, unambiguous instruction — "make a line graph of column X from data source Y with time on the x-axis" — the AI output should be effectively deterministic. The right component is obvious, the configuration is obvious.

These cases get automated tests that actually hit the AI endpoint (e.g. OpenAI API). The test:
1. Provides a clear user instruction
2. Receives the AI-generated configuration
3. Validates that the configuration is correct: right component type, right data source, right axis mappings, valid structure

We maintain a suite of these deterministic cases. They should always pass. If one starts failing, either the AI model degraded or our prompt/instructions regressed.

### 3.2 Injector Creation Tests (Automated)

When AI is given a data source specification — "here's a Postgres database with these tables and columns" — it should be able to produce a working injector. This is testable:

1. Provide AI with a database schema description
2. AI produces an injector (standardizer, filter definitions, aggregation setup, module wiring)
3. Run the injector against the actual test database
4. Verify that standardized queries return correct results
5. Verify that filters work
6. Verify that aggregation produces correct output

These tests use the same large-scale test databases from the module tests (Section 2.2). If the AI-generated injector works correctly against millions of rows, we have high confidence it'll work in production.

### 3.3 Creative / Complex Tasks (Manual)

Some AI tasks are inherently subjective or complex:
- Building custom components from scratch (the escape hatch)
- Laying out many components spatially on the dashboard
- Setting up links between data sources with ambiguous relationships
- Handling vague or underspecified user requests

These require manual testing. A human reviews the AI output and judges whether it's reasonable. Over time, as patterns emerge, some of these may graduate to automated tests — but initially they're manual.

---

## 4. Integration Tests

Beyond unit tests and module tests, we need end-to-end integration tests that verify the full pipeline:

1. **Data source → Injector → Client query → Rendered component**: A test that starts with a real (test) database, creates an injector, issues a query through the standardization/filter/aggregation layers, and verifies the output matches what the client would receive.

2. **Cross-data-source links**: A test that sets up two data sources with a link between them, queries through the link, and verifies the joined results are correct.

3. **Component composition**: A test that configures a nested component tree (e.g. a table with list children), feeds it data, and verifies the logic output is correct and performant.

---

## 5. Summary

| Area | Test Type | What It Catches |
|---|---|---|
| UI logic functions | Unit tests | Incorrect tick generation, pagination bugs, sorting errors |
| UI performance | Benchmark tests with upper bounds | Regressions that make components slow at scale |
| Injector modules | Unit + scale tests against real databases | Incorrect queries, poor performance at production scale |
| Module correctness | Assertion tests against known datasets | Wrong aggregation results, dropped/duplicated rows |
| AI configuration | Automated tests hitting AI endpoint | Prompt regressions, model degradation, invalid configs |
| AI injector creation | Automated tests with schema → injector → validation | AI producing broken or incorrect injectors |
| AI creative tasks | Manual testing | Subjective quality, complex multi-component layouts |
| Full pipeline | Integration tests | Breaks in the seams between layers |

Performance is a first-class concern everywhere. The test suite is not just about correctness — it's about making sure the system stays fast as it grows.

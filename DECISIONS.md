# Design decisions

Fill these answers in your own words after you have tested the implementation.

1. **Indexes:** Explain the unique employee-code and attendance natural-key indexes, plus indexes supporting common filters and sorts.
2. **Concurrent punch-ins:** Explain how the unique `(emp_code, date)` index makes one insert win and duplicate-key errors become HTTP 409.
3. **Leaderboard ties:** Explain competition ranking (1, 2, 2, 4) and why the limit is applied after ranking.
4. **Employees with no logs:** Explain why headcount starts from employees joined by the period end, rather than starting from attendance records.
5. **100x scale:** Discuss query plans, indexes, aggregation costs, pagination, monitoring, and possibly pre-aggregated reporting data.

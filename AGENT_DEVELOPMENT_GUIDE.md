# AGENT DEVELOPMENT GUIDE

## Standard
1. One agent = one responsibility.
2. Follow repository conventions.
3. Expose `as_agent_handler()`.
4. Use shared logger, validators, retry, exceptions and API router.
5. Avoid duplicated schemas and business logic.
6. Keep providers abstract.
7. Add dedicated pytest tests.
8. Freeze only after review + green CI.

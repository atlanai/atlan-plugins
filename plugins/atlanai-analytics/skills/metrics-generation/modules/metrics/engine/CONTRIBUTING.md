# Contributing to the metrics engine

Edit this source in the Analytics plugin. Keep core formats neutral and add a new source or
destination as a plug. Schemas precede artifacts, authored gates validate every answer, SQL
is read-only, and credentials remain in the environment.

A behavior change includes a regression test and documentation. Run the plugin's checks and focused runtime integration tests in tools/tests.
The ported engine test suite is omitted from this repository for now. Follow the repository's contribution and security rules.

Python files use these headers:

```python
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
```

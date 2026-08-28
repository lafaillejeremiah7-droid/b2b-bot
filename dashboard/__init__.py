"""The Deal Room Dashboard application.

Package layout mirrors the four layering rules of design §3.0.1 so that an
import-linter contract (task 1.4) can express them as package-to-package
constraints:

- ``dashboard.views``    — parse the request, call exactly one service, render.
                           Never writes; never imports ``dashboard.adapter``.
- ``dashboard.services`` — owns ``transaction.atomic()`` boundaries; the only
                           layer permitted to reach ``dashboard.adapter``, and
                           (via ``release_gate``) the only writer of
                           ``release_authorizations``.
- ``dashboard.adapter``  — the ``Pipeline_Adapter`` boundary, stub and live.
- ``dashboard.models``   — persistence only.

Each is a package rather than a module precisely so those contracts have stable
import targets; do not collapse any of them back into a single file.
"""

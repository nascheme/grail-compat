# Design documents

These pages are for people designing Grail, not for people using it. They
hold the project overview, the PEP drafts and the design notes behind the
API: proposed language semantics, the reasons for each decision, open
questions and the status of the native CPython prototype.

They describe proposals, not tested behavior. Much of what they propose is
not implemented, by grail-compat or by the prototype. For what works today,
read the [user documentation](../index.md).

- [Project Grail overview](00-project-grail-overview.md): goals, what holds
  the VM back, what Grail changes, roadmap and open questions.
- [PEP draft: Records](01-pep-draft-records.md)
- [PEP draft: Generic functions](02-pep-draft-generic-functions.md)
- [PEP draft: Record syntax](03-pep-draft-record-syntax.md)
- [PEP draft: C API](04-pep-draft-c-api.md) (very provisional)
- [Design: Persistent collections](05-design-persistent-collections.md)
  (very provisional)
- [CPython prototype: status and findings](06-cpython-prototype.md): a
  status report, not a PEP.
- [Appendix: Concurrency toolkit](07-appendix-concurrency-toolkit.md):
  outside the core scope.

These files are the source of the design docs. The "Project Grail" Claude
Doc mirrors them for sharing and comments; edit here first.

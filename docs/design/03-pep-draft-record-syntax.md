# PEP draft: Record syntax

Oct 25, 2026 · @Neil Schemenauer

*Status: syntax proposal only. The native prototype supports decorator-form records; neither a `record` grammar extension nor the compatibility import hook described here is implemented.*

## Abstract

This PEP adds a `record` statement as a convenience over the `@grail.record` decorator from the Records PEP. A `record` statement builds exactly the same type; the one semantic addition is that default expressions are evaluated at each construction, so mutable and side-effecting defaults need no factory. A proposed compatibility import hook could provide the statement before acceptance and on older interpreters.

## Motivation

The decorator form evaluates defaults once, in the class body, so non-value defaults must be spelled `grail.field(factory=...)`, and a value default with side effects, such as `time.time()`, is silently evaluated once. A dedicated statement removes both problems and states the intent, "this is a record, not a class", at the top of the declaration.

## Specification

```python
record Request:
    method: str = "GET"
    headers: dict[str, str] = {}      # a fresh dict per construction
    hits: Atom[int] = Atom(0)         # a fresh atom per construction
    created: float = time.time()      # evaluated at each construction
```

A `record` statement is equivalent to `@grail.record` applied to a class with the same fields, except for defaults:

- **Per-construction evaluation.** Each default expression is evaluated afresh for every construction that omits the field, like a dataclass `default_factory` without the factory function. The result is stored without annotation enforcement, as in decorator-form records; a mismatch may make the instance nonconforming.
- **No access to other fields.** A default is evaluated in the scope where the record is defined, as the body of a function defined there would be: it can use module globals and enclosing-function variables, not other fields. Derived values belong in a factory function.
- **Order.** The caller's arguments are evaluated first, as for any call. Then the defaults of omitted fields run top to bottom in declaration order, each exactly once; a supplied field never evaluates its default. If a default raises, construction fails and side effects of earlier defaults stand.

`record` is a soft keyword, so existing uses of `record` as a name keep working. The body may contain only field declarations and a docstring.

## The `grail` import hook and tooling

The proposed compatibility hook would parse `record` statements and rewrite each into `@grail.record` plus generated default factories. Operator-expression rewriting for exact-pair ordering is another proposed use of the hook (Generic functions PEP), not an implemented feature. Decorator-form records need no hook; full native exact-pair arithmetic ordering comes from the interpreter.

The prototype must test the hook with pip and wheels, byte-compilation (`compileall`, `py_compile`), pytest collection, multiprocessing spawn, editable installs, `zipimport`, coverage and debuggers, tracebacks, and type checkers, formatters, linters and IDE parsers. A generated ordinary-Python form may be needed as an official bridge for tools that cannot run the hook.

## Alternative: explicit late-bound defaults (PEP 671)

[PEP 671](https://peps.python.org/pep-671/) (Chris Angelico, Draft) proposes `name=>expression` for function parameters: a default evaluated at call time when the argument is omitted, while `name=expression` keeps today's define-time evaluation. Extending the same spelling to annotated fields would let records get per-construction defaults without giving `=` a special meaning inside a `record` statement:

```python
@grail.record
class Request:
    method: str = "GET"                 # evaluated once, when the record is defined
    headers: dict[str, str] => {}       # evaluated at each construction
    hits: Atom[int] => Atom(0)
    created: float => time.time()
```

|  | Special `=` in the `record` statement (this draft) | Explicit `=>` (PEP 671 style) |
| --- | --- | --- |
| Reading a default | Depends on whether the enclosing statement is `record` or a decorated class | Visible at the default itself |
| Moving code between the decorator and the statement | Silently changes when defaults run | No change |
| Decorator form | Must reject non-value defaults and require `grail.field(factory=...)` | Uses `=>` directly; the `ValueError` rule stays only for `=` |
| Reach | Records only | The same rule could serve dataclasses, attrs and Pydantic through `dataclass_transform`, and function parameters |
| Dependency | None | PEP 671, or a Grail-specific extension of it to class bodies, which also needs grammar |
| Access to other fields | Not allowed | PEP 671 lets a parameter default see earlier parameters; records would need to decide whether to follow it |

The explicit spelling is better for readers: how an expression is evaluated is clear where it is written, and the same rule holds in every context. If it is adopted, the `record` statement no longer needs special default semantics, and its value reduces to stating intent and dropping the decorator; this PEP might then be unnecessary. The open question is sequencing: PEP 671 is a draft for function parameters only, so Grail would either champion PEP 671 extended to annotated class-body fields, or ship the `record` statement first and accept the inconsistency.

## Backwards compatibility

`record` is a soft keyword, so no existing code breaks. Source using the statement does not run on Pythons without the PEP unless loaded through the `grail` import hook.

## Rejected ideas

- **Evaluating defaults once, as in the decorator form.** That is what the statement exists to fix.
- **Defaults that refer to earlier fields** (`let*`-style). Rejected for ordering and scoping complexity; derived values belong in a factory function.
- **Making the statement the only spelling.** The decorator keeps records usable with no grammar change and is what the VM optimizes.

## Open issues

- Ambiguous class-body forms such as `record R: x: int = 0; y: int` must have an explicit constructor-order rule. Per-construction defaults do not by themselves determine positional/keyword-only parameter ordering.
- Whether a default that names another field of the same record is a definition-time error, instead of silently resolving to an outer variable of that name.
- Exact scoping for names in the record body and type parameters of generic records; exceptions during default evaluation; interaction with pickling and forward annotations.
- The pegen grammar, and the impact on black, ruff, type checkers and IDEs.
- Whether sum types (`enum`) share the statement machinery when they come into scope.
- Whether to adopt explicit `=>` late-bound defaults (PEP 671 style) instead of special `=` semantics in the `record` statement, and if so whether this PEP is still needed.

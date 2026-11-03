/* grail._core_native: C implementation of grail-compat's record core.
 *
 * A drop-in for the pure-Python grail._core (same private API), ported from
 * cpython-grail's Objects/recordobject.c to the public C API of CPython
 * 3.14+.  Not shared with cpython-grail, which has the native records.
 *
 * A record type is a frozen heap type (PyType_Freeze) with object as its only
 * base: immutable, not subclassable, not instantiable through
 * object.__new__.  Instances store the fields inline, exposed through
 * read-only member descriptors, plus a conformance bit.  The type's schema
 * is a Schema object held in the type's __grail_schema__ entry; the type's
 * Py_tp_token points to it, so the C code reaches it without a lookup.
 *
 * Generic functions stay Python (grail._generic).  The protocol and operator
 * generics are watched: a registration for a record type sets a bit in its
 * schema, and only types with the bit set call the hook.  Others run the C
 * defaults directly.
 */
#include "Python.h"
#include "grail_compat.h"

#include <math.h>
#include <stddef.h>
#include <string.h>

/* ------------------------------------------------------------ layouts */

typedef struct {
    PyObject_HEAD
    int conforming;
    PyObject *fields[];
} RecordObject;

enum {
    PLAN_ANY,       /* no plan */
    PLAN_EXACT,     /* exact scalar type */
    PLAN_RECORD,    /* exact record type, and the value conforms */
    PLAN_UNION,
    PLAN_TUPLE,     /* fixed tuple: child i for item i */
    PLAN_VARTUPLE,  /* variable tuple: child 0 for every item */
};

/* Plans compiled at declaration.  Node i (i < number of fields) is field
 * i's plan; a union or tuple node's children are the contiguous nodes
 * [first, first + n).  Types are borrowed from the plans, which the schema
 * owns. */
typedef struct {
    PyTypeObject *type;
    uint32_t kind;
    uint32_t n;
    uint32_t first;
    uint32_t height;  /* nesting below this node: 0 for a leaf */
} PlanNode;

/* Conformance recursion follows the plan, so checking a plan no taller than
 * this needs no recursion guard. */
#define PLAN_UNGUARDED_HEIGHT 32

/* Hook bits: a registration on the protocol or operator generic for this
 * record type exists. */
#define HOOK_REPR (1u << 0)
#define HOOK_EQ (1u << 1)
#define HOOK_HASH (1u << 2)
#define HOOK_OP(i) (1u << (3 + (i)))

typedef struct {
    PyObject_HEAD
    PyObject *names;        /* interned str */
    PyObject *plans;
    PyObject *defaults;     /* (kind, value): 0 required, 1 value, 2 factory */
    PyObject *annotations;
    PlanNode *nodes;
    Py_ssize_t n;
    int eligible;
    uint32_t hooks;         /* HOOK_* bits; atomic */
} SchemaObject;

typedef struct {
    PyObject_HEAD
    vectorcallfunc vectorcall;
    PyTypeObject *record_type;
    int op;
    int kind;
} KernelObject;

typedef struct {
    PyTypeObject *schema_type;
    PyTypeObject *kernel_type;
    PyObject *generic_type;
    PyObject *repr_hook;
    PyObject *eq_hook;
    PyObject *hash_hook;
    PyObject *operators;    /* (add, sub, mul, truediv, neg, abs) */
    PyObject *str_state;
} CoreState;

static PyObject *record_new(PyTypeObject *, PyObject *, PyObject *);

static inline CoreState *
module_state(PyObject *module)
{
    return (CoreState *)PyModule_GetState(module);
}

static inline CoreState *
record_state(PyObject *obj)
{
    return (CoreState *)PyType_GetModuleState(Py_TYPE(obj));
}

static inline int
is_record_type(PyObject *obj)
{
    return PyType_Check(obj) && ((PyTypeObject *)obj)->tp_new == record_new;
}

static inline SchemaObject *
schema_of(PyTypeObject *type)
{
    return (SchemaObject *)((PyHeapTypeObject *)type)->ht_token;
}

static inline Py_ssize_t
record_count(PyObject *obj)
{
    return (Py_TYPE(obj)->tp_basicsize - offsetof(RecordObject, fields)) /
           sizeof(PyObject *);
}

static inline uint32_t
record_hooks(PyObject *obj)
{
    return _Py_atomic_load_uint32_relaxed(&schema_of(Py_TYPE(obj))->hooks);
}

static int
require_record_type(PyObject *obj)
{
    if (!is_record_type(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected a Grail record type");
        return -1;
    }
    return 0;
}

static int
require_record(PyObject *obj)
{
    return require_record_type((PyObject *)Py_TYPE(obj));
}

static PyObject *
type_name(PyTypeObject *type)
{
    return PyType_GetName(type);
}

/* -------------------------------------------------------------- plans */

/* 1 for a valid plan, 0 otherwise (None is not a plan here), -1 on error. */
static int
plan_check(PyObject *plan)
{
    if (plan == Py_None) {
        return 0;
    }
    if (PyType_Check(plan)) {
        if (plan == (PyObject *)Py_TYPE(Py_None) ||
            plan == (PyObject *)&PyBool_Type || plan == (PyObject *)&PyLong_Type ||
            plan == (PyObject *)&PyFloat_Type || plan == (PyObject *)&PyUnicode_Type ||
            plan == (PyObject *)&PyBytes_Type) {
            return 1;
        }
        return is_record_type(plan) && schema_of((PyTypeObject *)plan)->eligible;
    }
    if (!PyTuple_CheckExact(plan) || PyTuple_GET_SIZE(plan) != 2 ||
        !PyLong_CheckExact(PyTuple_GET_ITEM(plan, 0)) ||
        !PyTuple_CheckExact(PyTuple_GET_ITEM(plan, 1))) {
        return 0;
    }
    int overflow;
    long tag = PyLong_AsLongAndOverflow(PyTuple_GET_ITEM(plan, 0), &overflow);
    if (overflow || tag < 1 || tag > 3) {
        return 0;
    }
    PyObject *children = PyTuple_GET_ITEM(plan, 1);
    Py_ssize_t n = PyTuple_GET_SIZE(children);
    if ((tag == 1 && n == 0) || (tag == 3 && n != 1)) {
        return 0;
    }
    if (Py_EnterRecursiveCall(" while validating a record annotation")) {
        return -1;
    }
    int result = 1;
    for (Py_ssize_t i = 0; i < n; i++) {
        result = plan_check(PyTuple_GET_ITEM(children, i));
        if (result != 1) {
            break;
        }
    }
    Py_LeaveRecursiveCall();
    return result;
}

static int plan_conforms_nested(const PlanNode *, const PlanNode *, PyObject *);

/* 1 if value conforms to leaf plan node p (any, exact or record), else 0. */
static inline int
plan_conforms_leaf(const PlanNode *p, PyObject *value)
{
    if (p->kind == PLAN_ANY) {
        return 1;
    }
    return Py_TYPE(value) == p->type &&
           (p->kind == PLAN_EXACT || ((RecordObject *)value)->conforming);
}

/* 1 if value conforms to plan node p, 0 if not, -1 on error.  A union of
 * leaves, such as `T | None`, is checked inline. */
static inline int
plan_conforms(const PlanNode *nodes, const PlanNode *p, PyObject *value)
{
    switch (p->kind) {
    case PLAN_ANY:
    case PLAN_EXACT:
    case PLAN_RECORD:
        return plan_conforms_leaf(p, value);
    case PLAN_UNION:
        if (p->height == 1) {
            const PlanNode *children = nodes + p->first;
            for (uint32_t i = 0; i < p->n; i++) {
                if (plan_conforms_leaf(&children[i], value)) {
                    return 1;
                }
            }
            return 0;
        }
        return plan_conforms_nested(nodes, p, value);
    default:
        return plan_conforms_nested(nodes, p, value);
    }
}

static int
plan_conforms_nested(const PlanNode *nodes, const PlanNode *p, PyObject *value)
{
    int guarded = p->height > PLAN_UNGUARDED_HEIGHT;
    if (guarded && Py_EnterRecursiveCall(" while checking record conformance")) {
        return -1;
    }
    const PlanNode *children = nodes + p->first;
    int result = 0;
    if (p->kind == PLAN_UNION) {
        for (uint32_t i = 0; i < p->n; i++) {
            result = plan_conforms(nodes, &children[i], value);
            if (result != 0) {
                break;
            }
        }
    }
    else if (p->kind == PLAN_VARTUPLE && children->height == 0) {
        /* tuple[T, ...] of a leaf: the common long case. */
        if (PyTuple_CheckExact(value)) {
            Py_ssize_t n = PyTuple_GET_SIZE(value);
            result = 1;
            for (Py_ssize_t i = 0; i < n; i++) {
                if (!plan_conforms_leaf(children, PyTuple_GET_ITEM(value, i))) {
                    result = 0;
                    break;
                }
            }
        }
    }
    else if (PyTuple_CheckExact(value) &&
             (p->kind == PLAN_VARTUPLE || PyTuple_GET_SIZE(value) == p->n)) {
        int var = p->kind == PLAN_VARTUPLE;
        result = 1;
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(value); i++) {
            result = plan_conforms(nodes, &children[var ? 0 : i],
                                   PyTuple_GET_ITEM(value, i));
            if (result != 1) {
                break;
            }
        }
    }
    if (guarded) {
        Py_LeaveRecursiveCall();
    }
    return result;
}

/* Number of nodes below plan, which plan_check has validated. */
static Py_ssize_t
plan_descendants(PyObject *plan)
{
    if (!PyTuple_Check(plan)) {
        return 0;
    }
    PyObject *children = PyTuple_GET_ITEM(plan, 1);
    Py_ssize_t count = PyTuple_GET_SIZE(children);
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(children); i++) {
        count += plan_descendants(PyTuple_GET_ITEM(children, i));
    }
    return count;
}

static void
plan_compile(PlanNode *nodes, PlanNode *node, PyObject *plan, uint32_t *next)
{
    *node = (PlanNode){0};
    if (plan == Py_None) {
        node->kind = PLAN_ANY;
        return;
    }
    if (PyType_Check(plan)) {
        node->type = (PyTypeObject *)plan;
        node->kind = is_record_type(plan) ? PLAN_RECORD : PLAN_EXACT;
        return;
    }
    long tag = PyLong_AsLong(PyTuple_GET_ITEM(plan, 0));
    PyObject *children = PyTuple_GET_ITEM(plan, 1);
    node->kind = tag == 1 ? PLAN_UNION : tag == 2 ? PLAN_TUPLE : PLAN_VARTUPLE;
    node->n = (uint32_t)PyTuple_GET_SIZE(children);
    node->first = *next;
    *next += node->n;
    for (uint32_t i = 0; i < node->n; i++) {
        PlanNode *child = &nodes[node->first + i];
        plan_compile(nodes, child, PyTuple_GET_ITEM(children, i), next);
        if (child->height >= node->height) {
            node->height = child->height + 1;
        }
    }
    if (node->height == 0) {
        node->height = 1;
    }
}

/* The nodes for validated plans, allocated with PyMem_Malloc. */
static PlanNode *
plans_compile(PyObject *plans)
{
    Py_ssize_t n = PyTuple_GET_SIZE(plans);
    Py_ssize_t total = n;
    for (Py_ssize_t i = 0; i < n; i++) {
        total += plan_descendants(PyTuple_GET_ITEM(plans, i));
    }
    if (total > UINT32_MAX || (size_t)total > PY_SSIZE_T_MAX / sizeof(PlanNode)) {
        PyErr_NoMemory();
        return NULL;
    }
    PlanNode *nodes = PyMem_Malloc((total ? total : 1) * sizeof(PlanNode));
    if (nodes == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    uint32_t next = (uint32_t)n;
    for (Py_ssize_t i = 0; i < n; i++) {
        plan_compile(nodes, &nodes[i], PyTuple_GET_ITEM(plans, i), &next);
    }
    assert(next == total);
    return nodes;
}

/* ------------------------------------------------------------- schema */

static int
schema_traverse(PyObject *self, visitproc visit, void *arg)
{
    SchemaObject *s = (SchemaObject *)self;
    Py_VISIT(Py_TYPE(self));
    Py_VISIT(s->names);
    Py_VISIT(s->plans);
    Py_VISIT(s->defaults);
    Py_VISIT(s->annotations);
    return 0;
}

/* Never cleared: a record type in the same garbage cycle may still be used
 * by finalizers, and its plan nodes borrow from plans.  The cycle is broken
 * by clearing the record type's dict instead. */
static void
schema_dealloc(PyObject *self)
{
    SchemaObject *s = (SchemaObject *)self;
    PyTypeObject *tp = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    Py_XDECREF(s->names);
    Py_XDECREF(s->plans);
    Py_XDECREF(s->defaults);
    Py_XDECREF(s->annotations);
    PyMem_Free(s->nodes);
    tp->tp_free(self);
    Py_DECREF(tp);
}

static PyType_Slot schema_slots[] = {
    {Py_tp_dealloc, schema_dealloc},
    {Py_tp_traverse, schema_traverse},
    {0, NULL}
};

static PyType_Spec schema_spec = {
    .name = "grail._core_native.Schema",
    .basicsize = sizeof(SchemaObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE |
             Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = schema_slots,
};

/* ------------------------------------------------------- construction */

/* Fields are filled before the record is tracked; nothing can call back in
 * between, so no reference to a partial record escapes. */
static PyObject *
record_build(PyTypeObject *type, PyObject *const *values, int conforming)
{
    Py_ssize_t n = schema_of(type)->n;
    RecordObject *self = PyObject_GC_New(RecordObject, type);
    if (self == NULL) {
        return NULL;
    }
    self->conforming = conforming;
    for (Py_ssize_t i = 0; i < n; i++) {
        self->fields[i] = Py_NewRef(values[i]);
    }
    PyObject_GC_Track(self);
    return (PyObject *)self;
}

static PyObject *
record_finish(PyTypeObject *type, PyObject *const *values)
{
    SchemaObject *schema = schema_of(type);
    const PlanNode *nodes = schema->nodes;
    int conforming = 1;
    for (Py_ssize_t i = 0; i < schema->n; i++) {
        int ok = plan_conforms(nodes, &nodes[i], values[i]);
        if (ok < 0) {
            return NULL;
        }
        conforming &= ok;
    }
    return record_build(type, values, conforming);
}

/* Index of the field called name, n if there is none, or -1 on error.
 * Field names are interned at declaration and keyword names usually are, so
 * a pointer pass finds them; the comparison pass covers other strings. */
static Py_ssize_t
field_index(PyObject *names, PyObject *name)
{
    Py_ssize_t n = PyTuple_GET_SIZE(names);
    for (Py_ssize_t i = 0; i < n; i++) {
        if (PyTuple_GET_ITEM(names, i) == name) {
            return i;
        }
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        int eq = PyObject_RichCompareBool(name, PyTuple_GET_ITEM(names, i), Py_EQ);
        if (eq != 0) {
            return eq < 0 ? -1 : i;
        }
    }
    return n;
}

static PyObject *record_vectorcall_general(PyObject *, PyObject *const *,
                                           size_t, PyObject *);

/* Every field passed positionally: no staging buffer, defaults or extra
 * references. */
static PyObject *
record_vectorcall(PyObject *callable, PyObject *const *args, size_t nargsf,
                  PyObject *kwnames)
{
    PyTypeObject *type = (PyTypeObject *)callable;
    if (kwnames != NULL || PyVectorcall_NARGS(nargsf) != schema_of(type)->n) {
        return record_vectorcall_general(callable, args, nargsf, kwnames);
    }
    return record_finish(type, args);
}

static PyObject *
record_vectorcall_general(PyObject *callable, PyObject *const *args,
                          size_t nargsf, PyObject *kwnames)
{
    PyTypeObject *type = (PyTypeObject *)callable;
    SchemaObject *schema = schema_of(type);
    PyObject *names = schema->names;
    Py_ssize_t n = schema->n;
    Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
    if (nargs > n) {
        PyObject *name = type_name(type);
        if (name != NULL) {
            PyErr_Format(PyExc_TypeError,
                         "%U expects at most %zd positional arguments", name, n);
            Py_DECREF(name);
        }
        return NULL;
    }
    /* values are borrowed (record_finish takes its own references), except
     * those made by default factories, which are marked in made. */
    PyObject *small[8] = {NULL};
    char small_made[8] = {0};
    PyObject **values = small;
    char *made = small_made;
    if (n > (Py_ssize_t)Py_ARRAY_LENGTH(small)) {
        values = PyMem_Calloc(n, sizeof(PyObject *) + 1);
        if (values == NULL) {
            return PyErr_NoMemory();
        }
        made = (char *)(values + n);
    }
    PyObject *result = NULL;
    int factories = 0;
    for (Py_ssize_t i = 0; i < nargs; i++) {
        values[i] = args[i];
    }
    if (kwnames != NULL) {
        for (Py_ssize_t k = 0; k < PyTuple_GET_SIZE(kwnames); k++) {
            PyObject *key = PyTuple_GET_ITEM(kwnames, k);
            Py_ssize_t i = field_index(names, key);
            if (i < 0) {
                goto done;
            }
            if (i == n) {
                PyErr_Format(PyExc_TypeError, "unexpected record field %R", key);
                goto done;
            }
            if (values[i] != NULL) {
                PyErr_Format(PyExc_TypeError, "multiple values for field %R", key);
                goto done;
            }
            values[i] = args[nargs + k];
        }
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        if (values[i] != NULL) {
            continue;
        }
        PyObject *entry = PyTuple_GET_ITEM(schema->defaults, i);
        long kind = PyLong_AsLong(PyTuple_GET_ITEM(entry, 0));
        PyObject *value = PyTuple_GET_ITEM(entry, 1);
        if (kind == 0) {
            PyErr_Format(PyExc_TypeError, "missing required record field %R",
                         PyTuple_GET_ITEM(names, i));
            goto done;
        }
        if (kind == 2) {
            value = PyObject_CallNoArgs(value);
            if (value == NULL) {
                goto done;
            }
            made[i] = 1;
            factories = 1;
        }
        values[i] = value;
    }
    result = record_finish(type, values);
done:
    if (factories) {
        for (Py_ssize_t i = 0; i < n; i++) {
            if (made[i]) {
                Py_DECREF(values[i]);
            }
        }
    }
    if (values != small) {
        PyMem_Free(values);
    }
    return result;
}

static PyObject *
record_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    /* Also covers direct R.__new__(R, ...) calls. */
    return PyVectorcall_Call((PyObject *)type, args, kwargs);
}

static int
record_traverse(PyObject *self, visitproc visit, void *arg)
{
    RecordObject *r = (RecordObject *)self;
    Py_VISIT(Py_TYPE(self));
    for (Py_ssize_t i = 0; i < record_count(self); i++) {
        Py_VISIT(r->fields[i]);
    }
    return 0;
}

static int
record_clear(PyObject *self)
{
    RecordObject *r = (RecordObject *)self;
    for (Py_ssize_t i = 0; i < record_count(self); i++) {
        Py_CLEAR(r->fields[i]);
    }
    return 0;
}

static void
record_dealloc(PyObject *self)
{
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    Py_TRASHCAN_BEGIN(self, record_dealloc);
    record_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
    Py_TRASHCAN_END;
}

static int
record_setattr(PyObject *self, PyObject *name, PyObject *value)
{
    PyErr_SetString(PyExc_AttributeError, "record fields are immutable");
    return -1;
}

/* ------------------------------------------------ equality and hashing */

static int record_substitutable(PyObject *, PyObject *);

/* a == b for two exact strs or two compact ints, without the rich
 * comparison dispatch; -1 for other values. */
static inline int
exact_scalar_eq(PyObject *a, PyObject *b)
{
    PyTypeObject *t = Py_TYPE(a);
    if (t != Py_TYPE(b)) {
        return -1;
    }
    if (t == &PyUnicode_Type) {
        return PyUnicode_Equal(a, b);
    }
    if (t == &PyLong_Type && PyUnstable_Long_IsCompact((PyLongObject *)a) &&
        PyUnstable_Long_IsCompact((PyLongObject *)b)) {
        return PyUnstable_Long_CompactValue((PyLongObject *)a) ==
               PyUnstable_Long_CompactValue((PyLongObject *)b);
    }
    return -1;
}

/* p is NULL when the values have no plan to compare under. */
static int
value_substitutable(const PlanNode *nodes, const PlanNode *p,
                    PyObject *a, PyObject *b)
{
    if (a == b) {
        return 1;
    }
    if (p == NULL || p->kind == PLAN_ANY || Py_TYPE(a) != Py_TYPE(b)) {
        return 0;
    }
    if (p->kind == PLAN_EXACT || p->kind == PLAN_RECORD) {
        if (Py_TYPE(a) != p->type) {
            return 0;
        }
        if (p->kind == PLAN_RECORD) {
            return record_substitutable(a, b);
        }
        if (PyFloat_CheckExact(a)) {
            double x = PyFloat_AS_DOUBLE(a), y = PyFloat_AS_DOUBLE(b);
            return memcmp(&x, &y, sizeof(double)) == 0;
        }
        int eq = exact_scalar_eq(a, b);
        return eq >= 0 ? eq : PyObject_RichCompareBool(a, b, Py_EQ);
    }
    const PlanNode *children = nodes + p->first;
    if (p->kind == PLAN_UNION) {
        for (uint32_t i = 0; i < p->n; i++) {
            int ok = plan_conforms(nodes, &children[i], a);
            if (ok < 0) {
                return -1;
            }
            if (ok) {
                return value_substitutable(nodes, &children[i], a, b);
            }
        }
        return 0;
    }
    if (!PyTuple_CheckExact(a) || PyTuple_GET_SIZE(a) != PyTuple_GET_SIZE(b)) {
        return 0;
    }
    if (Py_EnterRecursiveCall(" while comparing record values")) {
        return -1;
    }
    int var = p->kind == PLAN_VARTUPLE;
    int result = 1;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(a); i++) {
        result = value_substitutable(nodes, &children[var ? 0 : i],
                                     PyTuple_GET_ITEM(a, i), PyTuple_GET_ITEM(b, i));
        if (result != 1) {
            break;
        }
    }
    Py_LeaveRecursiveCall();
    return result;
}

static int
record_substitutable(PyObject *a, PyObject *b)
{
    if (a == b) {
        return 1;
    }
    if (Py_TYPE(a) != Py_TYPE(b)) {
        return 0;
    }
    if (Py_EnterRecursiveCall(" while comparing records")) {
        return -1;
    }
    RecordObject *x = (RecordObject *)a, *y = (RecordObject *)b;
    const PlanNode *nodes = schema_of(Py_TYPE(a))->nodes;
    int both = x->conforming && y->conforming;
    int result = 1;
    for (Py_ssize_t i = 0; i < record_count(a); i++) {
        result = value_substitutable(nodes, both ? &nodes[i] : NULL,
                                     x->fields[i], y->fields[i]);
        if (result != 1) {
            break;
        }
    }
    Py_LeaveRecursiveCall();
    return result;
}

/* Default record equality: 1, 0, -1 on error, or 2 for NotImplemented. */
static int
record_default_eq(PyObject *a, PyObject *b)
{
    if (Py_TYPE(a) != Py_TYPE(b)) {
        return 2;
    }
    if (a == b) {
        return 1;
    }
    RecordObject *x = (RecordObject *)a, *y = (RecordObject *)b;
    const PlanNode *nodes = schema_of(Py_TYPE(a))->nodes;
    int both = x->conforming && y->conforming;
    /* Conforming fields of exact scalar plans compare without recursion. */
    int recursive = !both;
    for (Py_ssize_t i = 0; !recursive && i < record_count(a); i++) {
        recursive = nodes[i].kind != PLAN_EXACT;
    }
    if (recursive && Py_EnterRecursiveCall(" in record equality")) {
        return -1;
    }
    int result = 1;
    for (Py_ssize_t i = 0; i < record_count(a); i++) {
        PyObject *fa = x->fields[i], *fb = y->fields[i];
        if (fa == fb) {
            continue;
        }
        result = exact_scalar_eq(fa, fb);
        if (result >= 0) {
            if (!result) {
                break;
            }
            continue;
        }
        result = value_substitutable(nodes, both ? &nodes[i] : NULL, fa, fb);
        if (result == 0) {
            result = PyObject_RichCompareBool(fa, fb, Py_EQ);
        }
        if (result != 1) {
            break;
        }
    }
    if (recursive) {
        Py_LeaveRecursiveCall();
    }
    return result;
}

/* Hashes combine like tuple_hash over the lane hashes, without building
 * tuples: a conforming tuple field then hashes exactly like the equal tuple
 * stored in an ordinary field. */
static inline Py_uhash_t
hash_lane(Py_uhash_t acc, Py_hash_t lane)
{
    acc += (Py_uhash_t)lane * GRAIL_HASH_XXPRIME_2;
    acc = GRAIL_HASH_XXROTATE(acc);
    return acc * GRAIL_HASH_XXPRIME_1;
}

static inline Py_hash_t
hash_finish(Py_uhash_t acc, Py_ssize_t len)
{
    acc += len ^ (GRAIL_HASH_XXPRIME_5 ^ 3527539UL);
    return acc == (Py_uhash_t)-1 ? 1546275796 : (Py_hash_t)acc;
}

/* Hash value fields according to their representation where Python's hash
 * is identity-dependent (NaNs). */
static Py_hash_t
value_hash(const PlanNode *nodes, const PlanNode *p, PyObject *value)
{
    if (p == NULL || p->kind == PLAN_ANY) {
        return PyObject_Hash(value);
    }
    if (PyFloat_CheckExact(value) && isnan(PyFloat_AS_DOUBLE(value))) {
        double bits = PyFloat_AS_DOUBLE(value);
        return Py_HashBuffer(&bits, sizeof(bits));
    }
    const PlanNode *children = nodes + p->first;
    if (p->kind == PLAN_UNION) {
        for (uint32_t i = 0; i < p->n; i++) {
            int ok = plan_conforms(nodes, &children[i], value);
            if (ok < 0) {
                return -1;
            }
            if (ok) {
                return value_hash(nodes, &children[i], value);
            }
        }
    }
    else if (p->kind == PLAN_TUPLE || p->kind == PLAN_VARTUPLE) {
        if (Py_EnterRecursiveCall(" in record hashing")) {
            return -1;
        }
        int var = p->kind == PLAN_VARTUPLE;
        Py_ssize_t len = PyTuple_GET_SIZE(value);
        Py_uhash_t acc = GRAIL_HASH_XXPRIME_5;
        Py_hash_t result = 0;
        for (Py_ssize_t i = 0; i < len; i++) {
            Py_hash_t hash = value_hash(nodes, &children[var ? 0 : i],
                                        PyTuple_GET_ITEM(value, i));
            if (hash == -1) {
                result = -1;
                break;
            }
            acc = hash_lane(acc, hash);
        }
        Py_LeaveRecursiveCall();
        return result == -1 ? -1 : hash_finish(acc, len);
    }
    return PyObject_Hash(value);
}

static Py_hash_t
record_default_hash(PyObject *obj)
{
    if (Py_EnterRecursiveCall(" in record hashing")) {
        return -1;
    }
    RecordObject *self = (RecordObject *)obj;
    const PlanNode *nodes = schema_of(Py_TYPE(obj))->nodes;
    Py_ssize_t n = record_count(obj);
    Py_uhash_t acc = GRAIL_HASH_XXPRIME_5;
    Py_hash_t result = 0;
    for (Py_ssize_t i = 0; i < n; i++) {
        int ok = plan_conforms(nodes, &nodes[i], self->fields[i]);
        if (ok < 0) {
            result = -1;
            break;
        }
        /* A mismatch in another field must not change the hash of a
         * substitutable NaN/tuple here: conforming and nonconforming
         * instances can still compare equal. */
        Py_hash_t hash = value_hash(nodes, ok ? &nodes[i] : NULL, self->fields[i]);
        if (hash == -1) {
            result = -1;
            break;
        }
        acc = hash_lane(acc, hash);
    }
    Py_LeaveRecursiveCall();
    return result == -1 ? -1 : hash_finish(acc, n);
}

static PyObject *
record_default_repr(PyObject *obj)
{
    int entered = Py_ReprEnter(obj);
    if (entered != 0) {
        return entered < 0 ? NULL : PyUnicode_FromString("...");
    }
    RecordObject *self = (RecordObject *)obj;
    PyObject *names = schema_of(Py_TYPE(obj))->names;
    PyObject *name = type_name(Py_TYPE(obj));
    PyUnicodeWriter *writer = name == NULL ? NULL : PyUnicodeWriter_Create(0);
    if (writer == NULL || PyUnicodeWriter_WriteStr(writer, name) < 0 ||
        PyUnicodeWriter_WriteChar(writer, '(') < 0) {
        goto error;
    }
    for (Py_ssize_t i = 0; i < record_count(obj); i++) {
        if ((i > 0 && PyUnicodeWriter_WriteUTF8(writer, ", ", 2) < 0) ||
            PyUnicodeWriter_WriteStr(writer, PyTuple_GET_ITEM(names, i)) < 0 ||
            PyUnicodeWriter_WriteChar(writer, '=') < 0 ||
            PyUnicodeWriter_WriteRepr(writer, self->fields[i]) < 0) {
            goto error;
        }
    }
    if (PyUnicodeWriter_WriteChar(writer, ')') < 0) {
        goto error;
    }
    Py_DECREF(name);
    Py_ReprLeave(obj);
    return PyUnicodeWriter_Finish(writer);
error:
    if (writer != NULL) {
        PyUnicodeWriter_Discard(writer);
    }
    Py_XDECREF(name);
    Py_ReprLeave(obj);
    return NULL;
}

/* ------------------------------------------------------- record slots */

static PyObject *
record_repr(PyObject *obj)
{
    if (!(record_hooks(obj) & HOOK_REPR)) {
        return record_default_repr(obj);
    }
    return PyObject_CallOneArg(record_state(obj)->repr_hook, obj);
}

static PyObject *
record_richcompare(PyObject *a, PyObject *b, int op)
{
    if (op != Py_EQ && op != Py_NE) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (!(record_hooks(a) & HOOK_EQ)) {
        int result = record_default_eq(a, b);
        if (result == 2) {
            Py_RETURN_NOTIMPLEMENTED;
        }
        return result < 0 ? NULL : PyBool_FromLong(result == (op == Py_EQ));
    }
    PyObject *args[] = {a, b};
    PyObject *result = PyObject_Vectorcall(record_state(a)->eq_hook, args, 2, NULL);
    if (result == NULL || result == Py_NotImplemented || op == Py_EQ) {
        return result;
    }
    int truth = PyObject_IsTrue(result);
    Py_DECREF(result);
    return truth < 0 ? NULL : PyBool_FromLong(!truth);
}

static Py_hash_t
record_hash(PyObject *obj)
{
    if (!(record_hooks(obj) & HOOK_HASH)) {
        return record_default_hash(obj);
    }
    PyObject *result = PyObject_CallOneArg(record_state(obj)->hash_hook, obj);
    if (result == NULL) {
        return -1;
    }
    Py_hash_t hash = PyLong_AsSsize_t(result);
    if (hash == -1 && PyErr_ExceptionMatches(PyExc_OverflowError)) {
        PyErr_Clear();
        hash = PyObject_Hash(result);
    }
    Py_DECREF(result);
    return hash == -1 && !PyErr_Occurred() ? -2 : hash;
}

/* The implementation registered on generic for key, borrowed from its
 * published table (kept alive by the new reference in *state), or NULL
 * without an error if there is none. */
static PyObject *
generic_lookup(CoreState *st, PyObject *generic, PyObject *key, PyObject **state)
{
    *state = PyObject_GetAttr(generic, st->str_state);
    if (*state == NULL) {
        return NULL;
    }
    if (!PyTuple_CheckExact(*state) || PyTuple_GET_SIZE(*state) < 2 ||
        !PyDict_CheckExact(PyTuple_GET_ITEM(*state, 1))) {
        PyErr_SetString(PyExc_RuntimeError, "invalid generic state");
        return NULL;
    }
    PyObject *func;
    if (PyDict_GetItemRef(PyTuple_GET_ITEM(*state, 1), key, &func) < 0) {
        return NULL;
    }
    Py_XDECREF(func);   /* the table keeps it alive */
    return func;
}

static PyObject *
type_key(PyTypeObject *type)
{
    return PyLong_FromVoidPtr(type);
}

/* Binary operators: the registration for the exact (type(a), type(b))
 * pair, whichever operand is the record.  Two records of different types
 * share this slot, so CPython calls it once, with the left operand first. */
static PyObject *
record_binary(PyObject *a, PyObject *b, int index)
{
    uint32_t bit = HOOK_OP(index);
    int ra = is_record_type((PyObject *)Py_TYPE(a));
    int rb = is_record_type((PyObject *)Py_TYPE(b));
    if (!(ra && (record_hooks(a) & bit)) && !(rb && (record_hooks(b) & bit))) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    CoreState *st = record_state(ra ? a : b);
    PyObject *ka = type_key(Py_TYPE(a));
    PyObject *kb = ka == NULL ? NULL : type_key(Py_TYPE(b));
    PyObject *key = kb == NULL ? NULL : PyTuple_Pack(2, ka, kb);
    Py_XDECREF(ka);
    Py_XDECREF(kb);
    if (key == NULL) {
        return NULL;
    }
    PyObject *state;
    PyObject *func = generic_lookup(st, PyTuple_GET_ITEM(st->operators, index),
                                    key, &state);
    Py_DECREF(key);
    PyObject *result;
    if (func != NULL) {
        PyObject *args[] = {a, b};
        result = PyObject_Vectorcall(func, args, 2, NULL);
    }
    else if (PyErr_Occurred()) {
        result = NULL;
    }
    else {
        result = Py_NewRef(Py_NotImplemented);
    }
    Py_XDECREF(state);
    return result;
}

static PyObject *record_add(PyObject *a, PyObject *b) { return record_binary(a, b, 0); }
static PyObject *record_sub(PyObject *a, PyObject *b) { return record_binary(a, b, 1); }
static PyObject *record_mul(PyObject *a, PyObject *b) { return record_binary(a, b, 2); }
static PyObject *record_div(PyObject *a, PyObject *b) { return record_binary(a, b, 3); }

static PyObject *
record_unary(PyObject *obj, int index, const char *symbol)
{
    PyObject *func = NULL, *state = NULL;
    if (record_hooks(obj) & HOOK_OP(index)) {
        CoreState *st = record_state(obj);
        PyObject *key = type_key(Py_TYPE(obj));
        if (key == NULL) {
            return NULL;
        }
        func = generic_lookup(st, PyTuple_GET_ITEM(st->operators, index), key, &state);
        Py_DECREF(key);
        if (func == NULL && PyErr_Occurred()) {
            Py_XDECREF(state);
            return NULL;
        }
    }
    if (func == NULL) {
        Py_XDECREF(state);
        PyObject *name = type_name(Py_TYPE(obj));
        if (name != NULL) {
            PyErr_Format(PyExc_TypeError, "bad operand type for unary %s: '%U'",
                         symbol, name);
            Py_DECREF(name);
        }
        return NULL;
    }
    PyObject *result = PyObject_CallOneArg(func, obj);
    Py_DECREF(state);
    return result;
}

static PyObject *record_neg(PyObject *obj) { return record_unary(obj, 4, "-"); }
static PyObject *record_abs(PyObject *obj) { return record_unary(obj, 5, "abs()"); }

/* ------------------------------------------------------------ replace */

/* Conformance of a value against a frozen plan never changes, so when the
 * source conforms only the replaced fields are checked. */
static PyObject *
record_replace_impl(PyObject *obj, PyObject *const *args, PyObject *kwnames)
{
    PyTypeObject *type = Py_TYPE(obj);
    SchemaObject *schema = schema_of(type);
    const PlanNode *nodes = schema->nodes;
    int source_conforms = ((RecordObject *)obj)->conforming;
    int conforming = 1;
    Py_ssize_t n = schema->n;
    PyObject *small[8];
    PyObject **values = small;
    if (n > (Py_ssize_t)Py_ARRAY_LENGTH(small)) {
        values = PyMem_Malloc(n * sizeof(PyObject *));
        if (values == NULL) {
            return PyErr_NoMemory();
        }
    }
    PyObject *result = NULL;
    if (n) {
        memcpy(values, ((RecordObject *)obj)->fields, n * sizeof(PyObject *));
    }
    Py_ssize_t nkwargs = kwnames == NULL ? 0 : PyTuple_GET_SIZE(kwnames);
    for (Py_ssize_t k = 0; k < nkwargs; k++) {
        PyObject *key = PyTuple_GET_ITEM(kwnames, k);
        Py_ssize_t i = field_index(schema->names, key);
        if (i < 0) {
            goto done;
        }
        if (i == n) {
            PyErr_Format(PyExc_TypeError, "unexpected record field %R", key);
            goto done;
        }
        values[i] = args[k];
        if (source_conforms && conforming) {
            int ok = plan_conforms(nodes, &nodes[i], args[k]);
            if (ok < 0) {
                goto done;
            }
            conforming = ok;
        }
    }
    result = source_conforms ? record_build(type, values, conforming) :
                               record_finish(type, values);
done:
    if (values != small) {
        PyMem_Free(values);
    }
    return result;
}

static PyObject *
record_replace(PyObject *obj, PyObject *const *args, Py_ssize_t nargs,
               PyObject *kwnames)
{
    if (nargs) {
        PyErr_SetString(PyExc_TypeError, "__replace__ accepts only keyword fields");
        return NULL;
    }
    return record_replace_impl(obj, args, kwnames);
}

/* grail.replace(record, /, **changes) */
static PyObject *
grail_replace(PyObject *module, PyObject *const *args, Py_ssize_t nargs,
              PyObject *kwnames)
{
    if (nargs != 1) {
        PyErr_Format(PyExc_TypeError,
                     "replace() takes exactly one positional argument (%zd given)",
                     nargs);
        return NULL;
    }
    if (!is_record_type((PyObject *)Py_TYPE(args[0]))) {
        PyErr_SetString(PyExc_TypeError, "replace() requires a Grail record");
        return NULL;
    }
    return record_replace_impl(args[0], args + 1, kwnames);
}

PyDoc_STRVAR(grail_replace__doc__,
"replace($module, obj, /, **changes)\n"
"--\n"
"\n"
"Return a Grail record with the supplied fields replaced.\n"
"\n"
"Unchanged fields are shared, and defaults and factories are not replayed.");

/* ------------------------------------------------- copying and pickling */

static PyObject *
record_copy(PyObject *obj, PyObject *Py_UNUSED(ignored))
{
    return Py_NewRef(obj);
}

static PyObject *
record_deepcopy(PyObject *obj, PyObject *memo)
{
    if (!PyDict_Check(memo)) {
        PyErr_SetString(PyExc_TypeError, "deepcopy memo must be a dict");
        return NULL;
    }
    if (schema_of(Py_TYPE(obj))->eligible && ((RecordObject *)obj)->conforming) {
        return Py_NewRef(obj);
    }
    PyObject *facade = PyImport_ImportModule("grail");
    PyObject *result = facade == NULL ? NULL :
        PyObject_CallMethod(facade, "_deepcopy_record", "OO", obj, memo);
    Py_XDECREF(facade);
    return result;
}

static PyObject *
record_values_tuple(PyObject *obj)
{
    Py_ssize_t n = record_count(obj);
    PyObject *values = PyTuple_New(n);
    if (values == NULL) {
        return NULL;
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        PyTuple_SET_ITEM(values, i, Py_NewRef(((RecordObject *)obj)->fields[i]));
    }
    return values;
}

static PyObject *
record_reduce(PyObject *obj, PyObject *Py_UNUSED(ignored))
{
    PyObject *values = record_values_tuple(obj);
    if (values == NULL) {
        return NULL;
    }
    PyObject *result = PyTuple_Pack(2, (PyObject *)Py_TYPE(obj), values);
    Py_DECREF(values);
    return result;
}

static PyMethodDef record_methods[] = {
    {"__copy__", record_copy, METH_NOARGS, NULL},
    {"__deepcopy__", record_deepcopy, METH_O, NULL},
    {"__reduce__", record_reduce, METH_NOARGS, NULL},
    {"__reduce_ex__", record_reduce, METH_O, NULL},
    {"__replace__", (PyCFunction)(void (*)(void))record_replace,
     METH_FASTCALL | METH_KEYWORDS, NULL},
    {NULL}
};

/* ------------------------------------------------------------ kernels */

enum { KERNEL_ADD, KERNEL_SUB, KERNEL_MUL, KERNEL_DIV };

static inline double
kernel_double_op(int op, double x, double y)
{
    switch (op) {
        case KERNEL_ADD: return x + y;
        case KERNEL_SUB: return x - y;
        case KERNEL_MUL: return x * y;
        default: return x / y;
    }
}

static PyObject *
kernel_number_op(int op, PyObject *x, PyObject *y)
{
    switch (op) {
        case KERNEL_ADD: return PyNumber_Add(x, y);
        case KERNEL_SUB: return PyNumber_Subtract(x, y);
        case KERNEL_MUL: return PyNumber_Multiply(x, y);
        default: return PyNumber_TrueDivide(x, y);
    }
}

static PyObject *
kernel_apply(KernelObject *k, PyObject *a, PyObject *b)
{
    PyTypeObject *type = k->record_type;
    int op = k->op, kind = k->kind;
    PyObject *rec = kind == 2 ? b : a;
    PyObject *other = kind == 2 ? a : b;
    if (type == NULL || Py_TYPE(rec) != type ||
        (kind == 0 ? Py_TYPE(other) != type :
         !PyFloat_CheckExact(other) && !PyLong_CheckExact(other))) {
        PyErr_Format(PyExc_TypeError,
                     "element-wise kernel does not support '%.100s' and '%.100s'",
                     Py_TYPE(a)->tp_name, Py_TYPE(b)->tp_name);
        return NULL;
    }
    PyObject **x = ((RecordObject *)rec)->fields;
    PyObject **y = kind == 0 ? ((RecordObject *)other)->fields : NULL;
    int fast = ((RecordObject *)rec)->conforming &&
               (kind != 0 || ((RecordObject *)other)->conforming);
    double scalar = 0.0;
    if (fast && kind != 0) {
        if (PyFloat_CheckExact(other)) {
            scalar = PyFloat_AS_DOUBLE(other);
        }
        else {
            scalar = PyLong_AsDouble(other);
            if (scalar == -1.0 && PyErr_Occurred()) {
                return NULL;
            }
        }
        /* The ordinary operator raises ZeroDivisionError. */
        fast = !(op == KERNEL_DIV && scalar == 0.0);
    }
    Py_ssize_t n = record_count(rec);
    PyObject *small[8];
    PyObject **values = small;
    if (n > (Py_ssize_t)Py_ARRAY_LENGTH(small)) {
        values = PyMem_Malloc(n * sizeof(PyObject *));
        if (values == NULL) {
            return PyErr_NoMemory();
        }
    }
    PyObject *result = NULL;
    Py_ssize_t filled;
    for (filled = 0; filled < n; filled++) {
        PyObject *value;
        if (fast) {
            double field = PyFloat_AS_DOUBLE(x[filled]);
            double partner = y != NULL ? PyFloat_AS_DOUBLE(y[filled]) : scalar;
            value = PyFloat_FromDouble(kind == 2 ?
                kernel_double_op(op, partner, field) :
                kernel_double_op(op, field, partner));
        }
        else if (kind == 2) {
            value = kernel_number_op(op, other, x[filled]);
        }
        else {
            value = kernel_number_op(op, x[filled], y != NULL ? y[filled] : other);
        }
        if (value == NULL) {
            goto done;
        }
        values[filled] = value;
    }
    /* Every field of a fast result is a new exact float: conforming. */
    result = fast ? record_build(type, values, 1) : record_finish(type, values);
done:
    for (Py_ssize_t i = 0; i < filled; i++) {
        Py_DECREF(values[i]);
    }
    if (values != small) {
        PyMem_Free(values);
    }
    return result;
}

static void
kernel_dealloc(PyObject *self)
{
    PyTypeObject *tp = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    Py_CLEAR(((KernelObject *)self)->record_type);
    tp->tp_free(self);
    Py_DECREF(tp);
}

static int
kernel_traverse(PyObject *self, visitproc visit, void *arg)
{
    Py_VISIT(Py_TYPE(self));
    Py_VISIT(((KernelObject *)self)->record_type);
    return 0;
}

static int
kernel_clear(PyObject *self)
{
    Py_CLEAR(((KernelObject *)self)->record_type);
    return 0;
}

static PyObject *
kernel_vectorcall(PyObject *self, PyObject *const *args, size_t nargsf,
                  PyObject *kwnames)
{
    if (kwnames != NULL || PyVectorcall_NARGS(nargsf) != 2) {
        PyErr_SetString(PyExc_TypeError,
                        "element-wise kernel takes exactly two positional arguments");
        return NULL;
    }
    return kernel_apply((KernelObject *)self, args[0], args[1]);
}

static PyObject *
kernel_repr(PyObject *self)
{
    KernelObject *k = (KernelObject *)self;
    static const char symbols[] = "+-*/";
    PyObject *name = k->record_type == NULL ? PyUnicode_FromString("?") :
                     type_name(k->record_type);
    if (name == NULL) {
        return NULL;
    }
    const char *s = PyUnicode_AsUTF8(name);
    PyObject *result = s == NULL ? NULL : PyUnicode_FromFormat(
        "<grail element-wise kernel %s %c %s>", k->kind == 2 ? "scalar" : s,
        symbols[k->op], k->kind == 1 ? "scalar" : s);
    Py_DECREF(name);
    return result;
}

static PyMemberDef kernel_members[] = {
    {"__vectorcalloffset__", Py_T_PYSSIZET, offsetof(KernelObject, vectorcall),
     Py_READONLY},
    {NULL}
};

static PyType_Slot kernel_slots[] = {
    {Py_tp_dealloc, kernel_dealloc},
    {Py_tp_traverse, kernel_traverse},
    {Py_tp_clear, kernel_clear},
    {Py_tp_call, PyVectorcall_Call},
    {Py_tp_repr, kernel_repr},
    {Py_tp_members, kernel_members},
    {0, NULL}
};

static PyType_Spec kernel_spec = {
    .name = "grail._core_native.Kernel",
    .basicsize = sizeof(KernelObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL |
             Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = kernel_slots,
};

/* _kernel(record_type, op, kind): op 0-3 is + - * /. */
static PyObject *
core_kernel(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "_kernel expects 3 arguments");
        return NULL;
    }
    PyObject *type = args[0];
    int op = PyLong_AsInt(args[1]);
    int kind = op == -1 && PyErr_Occurred() ? -1 : PyLong_AsInt(args[2]);
    if (PyErr_Occurred() || require_record_type(type) < 0) {
        return NULL;
    }
    if (op < KERNEL_ADD || op > KERNEL_DIV || kind < 0 || kind > 2 ||
        (kind == 0) != (op == KERNEL_ADD || op == KERNEL_SUB) ||
        (kind == 2 && op != KERNEL_MUL)) {
        PyErr_SetString(PyExc_ValueError, "unsupported element-wise operation");
        return NULL;
    }
    PyObject *plans = schema_of((PyTypeObject *)type)->plans;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(plans); i++) {
        if (PyTuple_GET_ITEM(plans, i) != (PyObject *)&PyFloat_Type) {
            PyObject *name = type_name((PyTypeObject *)type);
            if (name != NULL) {
                PyErr_Format(PyExc_TypeError,
                             "element-wise arithmetic needs every field of %U "
                             "to be annotated float", name);
                Py_DECREF(name);
            }
            return NULL;
        }
    }
    CoreState *st = module_state(module);
    KernelObject *k = PyObject_GC_New(KernelObject, st->kernel_type);
    if (k == NULL) {
        return NULL;
    }
    k->record_type = (PyTypeObject *)Py_NewRef(type);
    k->op = op;
    k->kind = kind;
    k->vectorcall = kernel_vectorcall;
    PyObject_GC_Track(k);
    return (PyObject *)k;
}

static PyObject *
core_is_kernel(PyObject *module, PyObject *obj)
{
    return PyBool_FromLong(Py_IS_TYPE(obj, module_state(module)->kernel_type));
}

/* ------------------------------------------------------- record types */

static PyObject *
make_record_type(PyObject *module, PyObject *source, PyObject *names,
                 PyObject *plans, PyObject *defaults, PyObject *annotations,
                 int eligible)
{
    CoreState *st = module_state(module);
    Py_ssize_t n = PyTuple_GET_SIZE(names);
    PyObject *name = PyObject_GetAttrString(source, "__name__");
    PyObject *modname = PyObject_GetAttrString(source, "__module__");
    if (name != NULL && modname != NULL &&
        (!PyUnicode_Check(name) || !PyUnicode_Check(modname))) {
        PyErr_SetString(PyExc_TypeError, "record name and module must be strings");
        Py_CLEAR(name);
    }
    PyObject *qualified = name != NULL && modname != NULL ?
                          PyUnicode_FromFormat("%U.%U", modname, name) : NULL;
    Py_XDECREF(name);
    Py_XDECREF(modname);
    if (qualified == NULL) {
        return NULL;
    }
    SchemaObject *schema = PyObject_GC_New(SchemaObject, st->schema_type);
    if (schema == NULL) {
        Py_DECREF(qualified);
        return NULL;
    }
    schema->names = Py_NewRef(names);
    schema->plans = Py_NewRef(plans);
    schema->defaults = Py_NewRef(defaults);
    schema->annotations = Py_NewRef(annotations);
    schema->n = n;
    schema->eligible = eligible;
    schema->hooks = 0;
    schema->nodes = plans_compile(plans);
    PyObject_GC_Track(schema);
    PyObject *result = NULL;
    PyMemberDef *members = NULL;
    if (schema->nodes == NULL) {
        goto done;
    }
    members = PyMem_Calloc(n + 1, sizeof(PyMemberDef));
    if (members == NULL) {
        PyErr_NoMemory();
        goto done;
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        /* The names tuple, owned by the schema, keeps the UTF-8 alive. */
        members[i].name = PyUnicode_AsUTF8(PyTuple_GET_ITEM(names, i));
        if (members[i].name == NULL) {
            goto done;
        }
        members[i].type = Py_T_OBJECT_EX;
        members[i].offset = offsetof(RecordObject, fields) + i * sizeof(PyObject *);
        members[i].flags = Py_READONLY;
    }
    PyType_Slot slots[] = {
        {Py_tp_token, schema},
        {Py_tp_new, record_new},
        {Py_tp_dealloc, record_dealloc},
        {Py_tp_traverse, record_traverse},
        {Py_tp_clear, record_clear},
        {Py_tp_setattro, record_setattr},
        {Py_tp_members, members},
        {Py_tp_methods, record_methods},
        {Py_tp_repr, record_repr},
        {Py_tp_richcompare, record_richcompare},
        {Py_tp_hash, record_hash},
        {Py_nb_add, record_add},
        {Py_nb_subtract, record_sub},
        {Py_nb_multiply, record_mul},
        {Py_nb_true_divide, record_div},
        {Py_nb_negative, record_neg},
        {Py_nb_absolute, record_abs},
        {0, NULL}
    };
    PyType_Spec spec = {
        .name = PyUnicode_AsUTF8(qualified),
        .basicsize = (int)(offsetof(RecordObject, fields) + n * sizeof(PyObject *)),
        .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .slots = slots,
    };
    if (spec.name == NULL) {
        goto done;
    }
    result = PyType_FromModuleAndSpec(module, &spec, NULL);
    if (result == NULL) {
        goto done;
    }
    /* Metadata, and the schema's owning reference, before freezing. */
    PyObject *qualname = PyObject_GetAttrString(source, "__qualname__");
    PyObject *doc = PyObject_GetAttrString(source, "__doc__");
    if (qualname == NULL || doc == NULL ||
        PyObject_SetAttrString(result, "__grail_schema__", (PyObject *)schema) < 0 ||
        PyObject_SetAttrString(result, "__qualname__", qualname) < 0 ||
        PyObject_SetAttrString(result, "__doc__", doc) < 0 ||
        PyObject_SetAttrString(result, "__match_args__", names) < 0) {
        Py_CLEAR(result);
    }
    Py_XDECREF(qualname);
    Py_XDECREF(doc);
    if (result == NULL) {
        goto done;
    }
    ((PyTypeObject *)result)->tp_vectorcall = record_vectorcall;
    if (PyType_Freeze((PyTypeObject *)result) < 0) {
        Py_CLEAR(result);
    }
done:
    PyMem_Free(members);
    Py_DECREF(qualified);
    Py_DECREF(schema);
    return result;
}

static PyObject *
core_make_record(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 5) {
        PyErr_SetString(PyExc_TypeError, "_make_record expects 5 arguments");
        return NULL;
    }
    PyObject *source = args[0], *names = args[1], *plans = args[2];
    PyObject *defaults = args[3], *annotations = args[4];
    if (!Py_IS_TYPE(source, &PyType_Type)) {
        PyErr_SetString(PyExc_TypeError, "records require the standard type metaclass");
        return NULL;
    }
    PyObject *parts[] = {names, plans, defaults, annotations};
    for (int i = 0; i < 4; i++) {
        if (!PyTuple_CheckExact(parts[i]) ||
            PyTuple_GET_SIZE(parts[i]) != PyTuple_GET_SIZE(names)) {
            PyErr_SetString(PyExc_TypeError, "record schema requires equal-sized tuples");
            return NULL;
        }
    }
    Py_ssize_t n = PyTuple_GET_SIZE(names);
    if ((size_t)n > (INT_MAX - offsetof(RecordObject, fields)) / sizeof(PyObject *)) {
        return PyErr_NoMemory();
    }
    int eligible = 1;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *name = PyTuple_GET_ITEM(names, i);
        if (!PyUnicode_CheckExact(name) || !PyUnicode_IsIdentifier(name)) {
            PyErr_SetString(PyExc_TypeError, "record fields must be identifiers");
            return NULL;
        }
        const char *s = PyUnicode_AsUTF8(name);
        if (s == NULL) {
            return NULL;
        }
        if (s[0] == '_' && s[1] == '_') {
            PyErr_SetString(PyExc_TypeError, "dunder record fields are reserved");
            return NULL;
        }
        for (Py_ssize_t j = 0; j < i; j++) {
            int eq = PyObject_RichCompareBool(name, PyTuple_GET_ITEM(names, j), Py_EQ);
            if (eq != 0) {
                if (eq > 0) {
                    PyErr_SetString(PyExc_TypeError, "duplicate record field");
                }
                return NULL;
            }
        }
        PyObject *plan = PyTuple_GET_ITEM(plans, i);
        int ok = plan_check(plan);
        if (ok < 0 || PyErr_Occurred()) {
            return NULL;
        }
        if (plan != Py_None && !ok) {
            PyErr_SetString(PyExc_TypeError, "invalid native value plan");
            return NULL;
        }
        eligible &= plan != Py_None;
        PyObject *entry = PyTuple_GET_ITEM(defaults, i);
        if (!PyTuple_CheckExact(entry) || PyTuple_GET_SIZE(entry) != 2 ||
            !PyLong_CheckExact(PyTuple_GET_ITEM(entry, 0))) {
            PyErr_SetString(PyExc_TypeError, "invalid record default");
            return NULL;
        }
        int overflow;
        long kind = PyLong_AsLongAndOverflow(PyTuple_GET_ITEM(entry, 0), &overflow);
        if (overflow || kind < 0 || kind > 2 ||
            (kind == 2 && !PyCallable_Check(PyTuple_GET_ITEM(entry, 1)))) {
            PyErr_SetString(PyExc_TypeError, "invalid record default kind/factory");
            return NULL;
        }
    }
    CoreState *st = module_state(module);
    int configured;
    Py_BEGIN_CRITICAL_SECTION(module);
    configured = st->operators != NULL;
    Py_END_CRITICAL_SECTION();
    if (!configured) {
        PyErr_SetString(PyExc_RuntimeError, "import grail before creating records");
        return NULL;
    }
    /* Interned names let keyword lookups (field_index) match by pointer. */
    PyObject *interned = PyTuple_New(n);
    if (interned == NULL) {
        return NULL;
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *name = Py_NewRef(PyTuple_GET_ITEM(names, i));
        PyUnicode_InternInPlace(&name);
        PyTuple_SET_ITEM(interned, i, name);
    }
    PyObject *result = make_record_type(module, source, interned, plans,
                                        defaults, annotations, eligible);
    Py_DECREF(interned);
    return result;
}

/* ------------------------------------------------- module-level access */

static PyObject *
core_record_info(PyObject *module, PyObject *type)
{
    if (require_record_type(type) < 0) {
        return NULL;
    }
    SchemaObject *s = schema_of((PyTypeObject *)type);
    return Py_BuildValue("(OOOO)O", s->names, s->plans, s->defaults,
                         s->annotations, s->eligible ? Py_True : Py_False);
}

static PyObject *
core_record_values(PyObject *module, PyObject *obj)
{
    if (require_record(obj) < 0) {
        return NULL;
    }
    RecordObject *self = (RecordObject *)obj;
    PyObject *result = PyDict_New();
    if (result == NULL) {
        return NULL;
    }
    PyObject *names = schema_of(Py_TYPE(obj))->names;
    for (Py_ssize_t i = 0; i < record_count(obj); i++) {
        if (PyDict_SetItem(result, PyTuple_GET_ITEM(names, i), self->fields[i]) < 0) {
            Py_DECREF(result);
            return NULL;
        }
    }
    return result;
}

static PyObject *
core_is_record_type(PyObject *module, PyObject *obj)
{
    return PyBool_FromLong(is_record_type(obj));
}

static PyObject *
core_is_conforming(PyObject *module, PyObject *obj)
{
    if (require_record(obj) < 0) {
        return NULL;
    }
    return PyBool_FromLong(schema_of(Py_TYPE(obj))->eligible &&
                           ((RecordObject *)obj)->conforming);
}

static PyObject *
core_deepcopy_is_self(PyObject *module, PyObject *obj)
{
    return PyBool_FromLong(is_record_type((PyObject *)Py_TYPE(obj)) &&
                           schema_of(Py_TYPE(obj))->eligible &&
                           ((RecordObject *)obj)->conforming);
}

static PyObject *
core_substitutable(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "substitutable expects 2 arguments");
        return NULL;
    }
    if (require_record(args[0]) < 0 || require_record(args[1]) < 0) {
        return NULL;
    }
    int result = record_substitutable(args[0], args[1]);
    return result < 0 ? NULL : PyBool_FromLong(result);
}

static PyObject *
core_default_repr(PyObject *module, PyObject *obj)
{
    if (!is_record_type((PyObject *)Py_TYPE(obj))) {
        return PyObject_Repr(obj);
    }
    return record_default_repr(obj);
}

static PyObject *
core_default_eq(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "_default_eq expects 2 arguments");
        return NULL;
    }
    if (require_record(args[0]) < 0) {
        return NULL;
    }
    int result = record_default_eq(args[0], args[1]);
    if (result == 2) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    return result < 0 ? NULL : PyBool_FromLong(result);
}

static PyObject *
core_default_hash(PyObject *module, PyObject *obj)
{
    if (require_record(obj) < 0) {
        return NULL;
    }
    Py_hash_t hash = record_default_hash(obj);
    return hash == -1 ? NULL : PyLong_FromSsize_t(hash);
}

/* ---------------------------------------------------------- the hooks */

/* _watch(generic, types): called by a watched generic after a registration
 * is published.  Marks the record types among types so their slots call the
 * hook from now on. */
static PyObject *
core_watch(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 2 || !PyTuple_Check(args[1])) {
        PyErr_SetString(PyExc_TypeError, "_watch expects a generic and a tuple");
        return NULL;
    }
    CoreState *st = module_state(module);
    PyObject *generic = args[0];
    uint32_t bit = 0;
    Py_BEGIN_CRITICAL_SECTION(module);
    if (st->operators != NULL) {
        if (generic == st->repr_hook) {
            bit = HOOK_REPR;
        }
        else if (generic == st->eq_hook) {
            bit = HOOK_EQ;
        }
        else if (generic == st->hash_hook) {
            bit = HOOK_HASH;
        }
        for (int i = 0; i < 6 && !bit; i++) {
            if (generic == PyTuple_GET_ITEM(st->operators, i)) {
                bit = HOOK_OP(i);
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    for (Py_ssize_t i = 0; bit && i < PyTuple_GET_SIZE(args[1]); i++) {
        PyObject *t = PyTuple_GET_ITEM(args[1], i);
        if (is_record_type(t)) {
            _Py_atomic_or_uint32(&schema_of((PyTypeObject *)t)->hooks, bit);
        }
    }
    Py_RETURN_NONE;
}

static int
generic_set(PyObject *generic, const char *name, PyObject *value)
{
    PyObject *key = PyUnicode_FromString(name);
    if (key == NULL) {
        return -1;
    }
    int result = PyObject_GenericSetAttr(generic, key, value);
    Py_DECREF(key);
    return result;
}

static PyObject *
core_configure(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    CoreState *st = module_state(module);
    if (nargs != 4 || !PyTuple_CheckExact(args[3]) || PyTuple_GET_SIZE(args[3]) != 6) {
        PyErr_SetString(PyExc_TypeError, "invalid Grail hooks");
        return NULL;
    }
    for (int i = 0; i < 3; i++) {
        if (!Py_IS_TYPE(args[i], (PyTypeObject *)st->generic_type)) {
            PyErr_SetString(PyExc_TypeError, "invalid Grail hooks");
            return NULL;
        }
    }
    for (int i = 0; i < 6; i++) {
        if (!Py_IS_TYPE(PyTuple_GET_ITEM(args[3], i), (PyTypeObject *)st->generic_type)) {
            PyErr_SetString(PyExc_TypeError, "operators must be native generics");
            return NULL;
        }
    }
    PyObject *watch = PyObject_GetAttrString(module, "_watch");
    if (watch == NULL) {
        return NULL;
    }
    int ok = 1;
    Py_BEGIN_CRITICAL_SECTION(module);
    if (st->operators != NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Grail hooks are already configured");
        ok = 0;
    }
    for (int i = 0; ok && i < 4; i++) {
        ok = generic_set(PyTuple_GET_ITEM(args[3], i), "_central", Py_True) == 0;
    }
    for (int i = 0; ok && i < 6; i++) {
        ok = generic_set(PyTuple_GET_ITEM(args[3], i), "_watch", watch) == 0;
    }
    for (int i = 0; ok && i < 3; i++) {
        ok = generic_set(args[i], "_watch", watch) == 0;
    }
    if (ok) {
        st->repr_hook = Py_NewRef(args[0]);
        st->eq_hook = Py_NewRef(args[1]);
        st->hash_hook = Py_NewRef(args[2]);
        st->operators = Py_NewRef(args[3]);
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(watch);
    if (!ok) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* ------------------------------------------------------------- module */

#define FASTCALL(f) (PyCFunction)(void (*)(void))(f)

static PyMethodDef core_methods[] = {
    {"_configure", FASTCALL(core_configure), METH_FASTCALL, NULL},
    {"_watch", FASTCALL(core_watch), METH_FASTCALL, NULL},
    {"_make_record", FASTCALL(core_make_record), METH_FASTCALL, NULL},
    {"_record_info", core_record_info, METH_O, NULL},
    {"_record_values", core_record_values, METH_O, NULL},
    {"_is_record_type", core_is_record_type, METH_O, NULL},
    {"_is_conforming", core_is_conforming, METH_O, NULL},
    {"_deepcopy_is_self", core_deepcopy_is_self, METH_O, NULL},
    {"_kernel", FASTCALL(core_kernel), METH_FASTCALL, NULL},
    {"_is_kernel", core_is_kernel, METH_O, NULL},
    {"substitutable", FASTCALL(core_substitutable), METH_FASTCALL, NULL},
    {"_default_repr", core_default_repr, METH_O, NULL},
    {"_default_eq", FASTCALL(core_default_eq), METH_FASTCALL, NULL},
    {"_default_hash", core_default_hash, METH_O, NULL},
    {"replace_record", FASTCALL(grail_replace), METH_FASTCALL | METH_KEYWORDS,
     grail_replace__doc__},
    {NULL}
};

static int
core_exec(PyObject *module)
{
    CoreState *st = module_state(module);
    st->schema_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &schema_spec, NULL);
    if (st->schema_type == NULL) {
        return -1;
    }
    st->kernel_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &kernel_spec, NULL);
    if (st->kernel_type == NULL) {
        return -1;
    }
    st->str_state = PyUnicode_InternFromString("_state");
    if (st->str_state == NULL) {
        return -1;
    }
    PyObject *generics = PyImport_ImportModule("grail._generic");
    if (generics == NULL) {
        return -1;
    }
    st->generic_type = PyObject_GetAttrString(generics, "Generic");
    Py_DECREF(generics);
    if (st->generic_type == NULL || !PyType_Check(st->generic_type)) {
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_TypeError, "grail._generic.Generic is not a type");
        }
        return -1;
    }
    if (PyModule_AddObjectRef(module, "Generic", st->generic_type) < 0 ||
        PyModule_AddStringConstant(module, "backend", "shim") < 0) {
        return -1;
    }
    return 0;
}

static int
core_traverse(PyObject *module, visitproc visit, void *arg)
{
    CoreState *st = module_state(module);
    Py_VISIT(st->schema_type);
    Py_VISIT(st->kernel_type);
    Py_VISIT(st->generic_type);
    Py_VISIT(st->repr_hook);
    Py_VISIT(st->eq_hook);
    Py_VISIT(st->hash_hook);
    Py_VISIT(st->operators);
    return 0;
}

static int
core_clear(PyObject *module)
{
    CoreState *st = module_state(module);
    Py_CLEAR(st->schema_type);
    Py_CLEAR(st->kernel_type);
    Py_CLEAR(st->generic_type);
    Py_CLEAR(st->repr_hook);
    Py_CLEAR(st->eq_hook);
    Py_CLEAR(st->hash_hook);
    Py_CLEAR(st->operators);
    Py_CLEAR(st->str_state);
    return 0;
}

static void
core_free(void *module)
{
    core_clear((PyObject *)module);
}

static PyModuleDef_Slot core_slots[] = {
    {Py_mod_exec, core_exec},
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
    {0, NULL}
};

static struct PyModuleDef core_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "grail._core_native",
    .m_doc = "C implementation of the Grail record core.",
    .m_size = sizeof(CoreState),
    .m_methods = core_methods,
    .m_slots = core_slots,
    .m_traverse = core_traverse,
    .m_clear = core_clear,
    .m_free = core_free,
};

PyMODINIT_FUNC
PyInit__core_native(void)
{
    return PyModuleDef_Init(&core_module);
}

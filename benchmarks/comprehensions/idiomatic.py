"""Immutable widget filtering, joining, sorting and projection.

Same algorithm as pyperformance's bm_comprehensions. Domain data is published
as records/tuples; dictionaries and sorting lists are local scratch. Keep the
upstream ascending Boolean sort (despite its contradictory prose comment),
filtered join domain, missing-reference counts and last-ID-wins lookup.
There is no polymorphism or useful parallel work in this 24-widget workload.
"""

import grail


@grail.record
class WidgetKind:
    value: int


# Upstream's enum members; .value keeps the upstream attribute.
BIG = WidgetKind(1)
SMALL = WidgetKind(2)
WIDGET_KINDS = (BIG, SMALL)


@grail.record
class Widget:
    widget_id: int
    creator_id: int
    derived_widget_ids: tuple[int, ...]
    kind: WidgetKind
    has_knob: bool
    has_spinner: bool


@grail.record
class WidgetTray:
    owner_id: int
    sorted_widgets: tuple[Widget, ...]


def any_knobby(widgets):
    return any(w.has_knob for w in widgets if w)


def is_big_spinny(widget: Widget) -> bool:
    return widget.kind == BIG and widget.has_spinner


def make_tray(owner_id: int, widgets: tuple[Widget, ...]) -> WidgetTray:
    widgets = [w for w in widgets if not is_big_spinny(w)]
    id_to_widget = {w.widget_id: w for w in widgets}
    id_to_derived = {
        w.widget_id: [id_to_widget.get(dwid) for dwid in w.derived_widget_ids]
        for w in widgets
    }
    sortable_widgets = [
        (
            w.creator_id == owner_id,
            any_knobby(id_to_derived[w.widget_id]),
            len(id_to_derived[w.widget_id]),
            w.widget_id,
        )
        for w in widgets
    ]
    sortable_widgets.sort()
    return WidgetTray(owner_id, tuple(id_to_widget[sw[-1]] for sw in sortable_widgets))


def make_some_widgets() -> tuple[Widget, ...]:
    widgets = []
    for creator_id in range(3):
        for kind in WIDGET_KINDS:
            for has_knob in (True, False):
                for has_spinner in (True, False):
                    derived = tuple(w.widget_id for w in widgets[::creator_id + 1])
                    widgets.append(Widget(len(widgets), creator_id, derived,
                                          kind, has_knob, has_spinner))
    assert len(widgets) == 24
    return tuple(widgets)


def main() -> WidgetTray:
    return make_tray(1, make_some_widgets())


if __name__ == "__main__":
    print(main())
